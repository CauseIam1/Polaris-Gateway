#!/usr/bin/env python3
"""
Polaris Memory Vault - Obsidian-Style Persistent Memory Layer
==============================================================
Durable, human-readable memory stored as categorized markdown files
(frontmatter + body). The ChromaDB collection 'vault_documents' is a
REBUILDABLE semantic index over those files, created through the shared
PolarisVectorMemory client (same PersistentClient, same /data store).

Source of truth   : markdown files on the vault volume
Rebuildable index : ChromaDB 'vault_documents' collection

A watchdog observer watches the vault root so files edited directly on
the host (e.g. via Obsidian) are automatically re-embedded.

Categories (routed directories):
    core         -> 00-Core                  (guarded: allow_core=True)
    architecture -> 01-Architecture
    user         -> 02-User/<user>/          (requires user)
    lesson       -> 03-Lessons
    journal      -> 04-Journal/YYYY-MM-DD.md (append-only, timestamped)
    attachment   -> 05-Attachments
"""

import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from vector_memory import get_vector_memory

# ============== CONFIGURATION ==============
VAULT_ROOT = os.environ.get("POLARIS_VAULT_ROOT", "/polaris_memory_vault")
VAULT_COLLECTION = "vault_documents"

VAULT_CATEGORIES = {
    "core": "00-Core",
    "architecture": "01-Architecture",
    "user": "02-User",
    "lesson": "03-Lessons",
    "journal": "04-Journal",
    "attachment": "05-Attachments",
}
DIR_TO_CATEGORY = {v: k for k, v in VAULT_CATEGORIES.items()}
CATEGORY_NAMES = sorted(VAULT_CATEGORIES.keys())
JOURNAL_DIR = VAULT_CATEGORIES["journal"]

MAX_CONTENT_CHARS = 12000   # hard cap per document body
MAX_SNIPPET_CHARS = 280     # search result snippet length
MAX_EMBED_CHARS = 4000      # cap on text sent to the embedding model

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is",
    "are", "was", "were", "be", "been", "it", "this", "that", "with",
    "what", "how", "why", "who", "when", "where", "at", "by", "from",
}

_vault_lock = threading.RLock()
_vault_instance: Optional["MemoryVault"] = None


class MemoryVault:
    """Markdown-file-backed memory with a ChromaDB semantic index."""

    def __init__(self, vault_root: str = VAULT_ROOT):
        self.vault_root = os.path.abspath(vault_root)
        self.vector_memory = get_vector_memory()
        # Same PersistentClient as the rest of the gateway -> shared store
        self.collection = self.vector_memory._get_or_create_collection(
            VAULT_COLLECTION,
            metadata={"description": "Obsidian-style memory vault document index"}
        )
        self.watchdog_running = False
        self._observer = None
        self._event_queue: deque = deque()
        self._event_thread: Optional[threading.Thread] = None
        self._last_rebuild: Optional[str] = None
        self._ensure_structure()

    # ============== FILESYSTEM HELPERS ==============

    def _ensure_structure(self) -> None:
        for dirname in VAULT_CATEGORIES.values():
            os.makedirs(os.path.join(self.vault_root, dirname), exist_ok=True)

    def _safe_rel_path(self, rel_path: str) -> Optional[str]:
        """Normalize a vault-relative path; reject traversal outside the vault."""
        if not rel_path or not isinstance(rel_path, str):
            return None
        clean = os.path.normpath(rel_path.strip().lstrip("/\\"))
        if clean in (".", "..") or clean.startswith("..") or os.path.isabs(clean):
            return None
        return clean

    def _abs_path(self, rel_path: str) -> str:
        return os.path.join(self.vault_root, rel_path)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _slugify(text: str, max_len: int = 60) -> str:
        slug = re.sub(r"[^a-zA-Z0-9\s-]", "", str(text)).strip().lower()
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
        return (slug or "untitled")[:max_len]

    @staticmethod
    def _chroma_id(*parts: str) -> str:
        """Deterministic Chroma-safe id (alphanumerics, dash, underscore only)."""
        raw = "/".join(str(p) for p in parts if p)
        return re.sub(r"[^a-zA-Z0-9_-]", "_", raw) or "doc"

    # ============== FRONTMATTER ==============

    @staticmethod
    def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
        """Parse a frontmatter block (delimited by '---' lines) into meta + body."""
        if not text.startswith("---"):
            return {}, text
        lines = text.split("\n")
        if len(lines) < 2 or lines[0].strip() != "---":
            return {}, text
        meta: Dict[str, str] = {}
        end = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
            line = lines[i]
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip()
        if end < 0:
            return {}, text
        body = "\n".join(lines[end + 1:]).strip("\n")
        return meta, body

    @staticmethod
    def _build_frontmatter(meta: Dict[str, Any]) -> str:
        """Render a metadata dict into YAML-ish frontmatter text."""
        lines = ["---"]
        for key, value in meta.items():
            text = str(value).replace("\n", " ").replace("---", "- - -")
            lines.append(f"{key}: {text}")
        lines.append("---")
        return "\n".join(lines)

    # ============== WRITE PATH ==============

    def _write_file(self, rel_path: str, meta: Dict[str, Any], content: str) -> None:
        abs_path = self._abs_path(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        text = self._build_frontmatter(meta) + "\n\n" + content.rstrip() + "\n"
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _read_document(self, rel_path: str) -> Tuple[Dict[str, str], str]:
        with open(self._abs_path(rel_path), "r", encoding="utf-8", errors="replace") as fh:
            return self._parse_frontmatter(fh.read())

    def create_document(self, category: str, title: str, content: str,
                        tags: Optional[List[str]] = None, user: str = "",
                        source: str = "api", allow_core: bool = False,
                        extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a vault document and embed it into the semantic index."""
        with _vault_lock:
            if category not in VAULT_CATEGORIES:
                raise ValueError(f"Invalid category '{category}'. Valid: {CATEGORY_NAMES}")
            if category == "core" and not allow_core:
                raise PermissionError("Writing to 'core' requires allow_core=True")
            if category == "user" and not str(user or "").strip():
                raise ValueError("Category 'user' requires a 'user' value")

            title = str(title or "Untitled").strip()[:200]
            content = str(content or "").strip()
            if not content:
                raise ValueError("'content' is required")
            if len(content) > MAX_CONTENT_CHARS:
                content = content[:MAX_CONTENT_CHARS]

            now = self._now_iso()
            doc_id = uuid.uuid4().hex[:12]
            tags = [str(t).strip() for t in (tags or []) if str(t).strip()][:12]
            meta = {
                "id": doc_id,
                "title": title,
                "category": category,
                "user": str(user or ""),
                "tags": ", ".join(tags),
                "source": str(source or "api"),
                "created": now,
                "updated": now,
            }
            if extra:
                for key, value in extra.items():
                    meta[str(key)] = value

            if category == "journal":
                return self._append_journal(meta, content)

            day = now[:10]
            fname = f"{day}_{self._slugify(title)}.md"
            subdir = VAULT_CATEGORIES[category]
            if category == "user":
                rel_path = f"{subdir}/{self._slugify(user)}/{fname}"
            else:
                rel_path = f"{subdir}/{fname}"
            if os.path.exists(self._abs_path(rel_path)):
                rel_path = rel_path[:-3] + f"_{doc_id}.md"

            self._write_file(rel_path, meta, content)
            indexed = self._embed_document(rel_path)
            return {
                "status": "created",
                "path": rel_path,
                "id": doc_id,
                "title": title,
                "category": category,
                "indexed": indexed,
                "timestamp": now,
            }

    def _append_journal(self, meta: Dict[str, Any], content: str) -> Dict[str, Any]:
        """Append a timestamped entry to the daily journal and embed the entry."""
        now = meta["created"]
        day, entry_time = now[:10], now[11:19]
        rel_path = f"{JOURNAL_DIR}/{day}.md"
        abs_path = self._abs_path(rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        if not os.path.exists(abs_path):
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(f"# Polaris Journal — {day}\n")
        with open(abs_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n## {entry_time} — {meta['title']}\n\n{content}\n")

        entry_id = self._chroma_id(rel_path, entry_time.replace(":", ""))
        embedding = self.vector_memory._generate_embedding(
            f"{meta['title']}\n{content}"[:MAX_EMBED_CHARS]
        )
        indexed = False
        if embedding:
            self.collection.upsert(
                ids=[entry_id],
                embeddings=[embedding],
                metadatas=[{
                    "path": rel_path,
                    "journal_file": rel_path,
                    "entry_time": now,
                    "title": meta["title"],
                    "category": "journal",
                    "user": meta.get("user", ""),
                    "tags": meta.get("tags", ""),
                    "source": meta.get("source", "api"),
                    "created": now,
                    "updated": now,
                    "content": content[:2000],
                }]
            )
            indexed = True
        else:
            print(f"[MemoryVault] Embedding failed (Ollama down?) for {rel_path}")
        return {
            "status": "created",
            "path": rel_path,
            "id": meta["id"],
            "title": meta["title"],
            "category": "journal",
            "entry_time": entry_time,
            "indexed": indexed,
            "timestamp": now,
        }

    def update_document(self, rel_path: str, content: str,
                        title: Optional[str] = None,
                        tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """Update an existing vault document's body (and optional meta) and re-embed it."""
        with _vault_lock:
            clean = self._safe_rel_path(rel_path)
            if clean is None:
                raise ValueError(f"Invalid path: {rel_path}")
            abs_path = self._abs_path(clean)
            if not os.path.isfile(abs_path):
                raise FileNotFoundError(f"Document not found: {clean}")
            meta, _body = self._read_document(clean)
            category = str(meta.get("category", ""))
            if category == "core":
                raise PermissionError("core documents cannot be edited via the API")
            if category == "journal" or clean.startswith(JOURNAL_DIR + "/"):
                raise ValueError("journal entries are append-only; use write")
            content = str(content or "").strip()
            if not content:
                raise ValueError("'content' is required")
            if len(content) > MAX_CONTENT_CHARS:
                content = content[:MAX_CONTENT_CHARS]
            if title is not None and str(title).strip():
                meta["title"] = str(title).strip()[:200]
            if tags is not None:
                clean_tags = [str(t).strip() for t in tags if str(t).strip()][:12]
                meta["tags"] = ", ".join(clean_tags)
            meta["updated"] = self._now_iso()
            self._write_file(clean, meta, content)
            indexed = self._embed_document(clean)
            now = self._now_iso()
            return {
                "status": "updated",
                "path": clean.replace(os.sep, "/"),
                "title": meta.get("title", ""),
                "category": category,
                "indexed": indexed,
                "timestamp": now,
            }

    # ============== EMBEDDING ==============

    def _embed_document(self, rel_path: str) -> bool:
        """(Re-)embed one vault file into the semantic index (upsert)."""
        try:
            meta, content = self._read_document(rel_path)
        except OSError as e:
            print(f"[MemoryVault] embed skip ({rel_path}): {e}")
            return False
        embedding = self.vector_memory._generate_embedding(
            f"{meta.get('title', '')}\n{content}"[:MAX_EMBED_CHARS]
        )
        if not embedding:
            print(f"[MemoryVault] embedding unavailable for {rel_path} (Ollama down?)")
            return False
        self.collection.upsert(
            ids=[self._chroma_id(rel_path)],
            embeddings=[embedding],
            metadatas=[{
                "path": rel_path,
                "title": str(meta.get("title", ""))[:200],
                "category": str(meta.get("category", ""))[:40],
                "user": str(meta.get("user", ""))[:80],
                "tags": str(meta.get("tags", ""))[:300],
                "source": str(meta.get("source", ""))[:80],
                "created": str(meta.get("created", ""))[:40],
                "updated": str(meta.get("updated", ""))[:40] or self._now_iso(),
                "content": content[:2000],
            }],
        )
        return True

    # ============== READ PATH ==============

    def get_document(self, rel_path: str) -> Dict[str, Any]:
        """Fetch a single vault document (frontmatter meta + full content)."""
        clean = self._safe_rel_path(rel_path)
        if clean is None:
            raise ValueError(f"Invalid path: {rel_path}")
        abs_path = self._abs_path(clean)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f"Document not found: {clean}")
        meta, content = self._read_document(clean)
        stat = os.stat(abs_path)
        return {
            "path": clean.replace(os.sep, "/"),
            "meta": meta,
            "content": content,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        }

    def list_documents(self, category: Optional[str] = None,
                       user: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        """List vault files newest-first, optionally filtered by category/user."""
        if category and category not in VAULT_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Valid: {CATEGORY_NAMES}")
        limit = max(1, min(int(limit), 500))
        subdirs = ([VAULT_CATEGORIES[category]] if category
                   else list(VAULT_CATEGORIES.values()))
        docs: List[Dict[str, Any]] = []
        with _vault_lock:
            for subdir in subdirs:
                base = os.path.join(self.vault_root, subdir)
                if not os.path.isdir(base):
                    continue
                for root, _dirs, files in os.walk(base):
                    for fname in files:
                        if not fname.endswith(".md") or len(docs) >= 500:
                            continue
                        rel = os.path.relpath(os.path.join(root, fname), self.vault_root)
                        try:
                            meta, content = self._read_document(rel)
                        except OSError:
                            continue
                        if user and str(meta.get("user", "")) != user:
                            continue
                        docs.append({
                            "path": rel.replace(os.sep, "/"),
                            "title": meta.get("title", fname),
                            "category": meta.get("category", ""),
                            "user": meta.get("user", ""),
                            "tags": meta.get("tags", ""),
                            "source": meta.get("source", ""),
                            "created": meta.get("created", ""),
                            "updated": meta.get("updated", ""),
                            "snippet": content.strip()[:MAX_SNIPPET_CHARS],
                        })
        docs.sort(key=lambda d: (d.get("updated") or "", d.get("created") or ""), reverse=True)
        return docs[:limit]

    def delete_document(self, rel_path: str) -> Dict[str, Any]:
        """Delete a vault file and remove it from the semantic index."""
        with _vault_lock:
            clean = self._safe_rel_path(rel_path)
            if clean is None:
                raise ValueError(f"Invalid path: {rel_path}")
            abs_path = self._abs_path(clean)
            if not os.path.isfile(abs_path):
                raise FileNotFoundError(f"Document not found: {clean}")
            try:
                meta, _ = self._read_document(clean)
            except OSError:
                meta = {}
            if str(meta.get("category", "")) == "core":
                raise PermissionError("core documents cannot be deleted via the API")
            os.remove(abs_path)
            try:
                self.collection.delete(ids=[self._chroma_id(clean)])
            except Exception as e:
                print(f"[MemoryVault] index delete failed for {clean}: {e}")
            try:  # also drop embedded journal entries pointing at this file
                self.collection.delete(where={"path": clean})
            except Exception:
                pass
            return {"status": "deleted", "path": clean.replace(os.sep, "/"),
                    "title": meta.get("title", ""),
                    "timestamp": self._now_iso()}

    # ============== SEARCH ==============

    def _snippet_around(self, content: str, terms: List[str]) -> str:
        """Build a search-result snippet centred on the first term match."""
        lower = content.lower()
        pos = -1
        for t in terms:
            p = lower.find(t)
            if p >= 0 and (pos < 0 or p < pos):
                pos = p
        if pos < 0:
            return content.strip()[:MAX_SNIPPET_CHARS]
        start = max(0, pos - MAX_SNIPPET_CHARS // 3)
        end = min(len(content), start + MAX_SNIPPET_CHARS)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(content) else ""
        return (prefix + content[start:end].strip() + suffix)[:MAX_SNIPPET_CHARS + 2]

    def _keyword_search(self, query: str, categories, user: str,
                        limit: int) -> List[Dict[str, Any]]:
        """Local scan: score title/tag/content term matches across vault files."""
        terms = [t for t in re.findall(r"[a-zA-Z0-9']+", query.lower())
                 if len(t) > 2 and t not in _STOPWORDS][:12]
        if not terms:
            return []
        cats = set(categories) if categories else set(VAULT_CATEGORIES)
        results: List[Dict[str, Any]] = []
        for cat, subdir in VAULT_CATEGORIES.items():
            if cat not in cats:
                continue
            base = os.path.join(self.vault_root, subdir)
            if not os.path.isdir(base):
                continue
            for root, _dirs, files in os.walk(base):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fname), self.vault_root)
                    try:
                        meta, content = self._read_document(rel)
                    except OSError:
                        continue
                    if user and str(meta.get("user", "")) != user:
                        continue
                    title_l = str(meta.get("title", "")).lower()
                    tags_l = str(meta.get("tags", "")).lower()
                    body_l = content.lower()
                    score = 0
                    for t in terms:
                        if t in title_l:
                            score += 3
                        if t in tags_l:
                            score += 2
                        score += min(body_l.count(t), 5)
                    if score <= 0:
                        continue
                    results.append({
                        "path": rel.replace(os.sep, "/"),
                        "title": meta.get("title", fname),
                        "category": meta.get("category", cat),
                        "user": meta.get("user", ""),
                        "tags": meta.get("tags", ""),
                        "updated": meta.get("updated", ""),
                        "score": float(score),
                        "match_type": "keyword",
                        "snippet": self._snippet_around(content, terms),
                    })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit * 3]

    def _semantic_search(self, query: str, categories, user: str,
                         limit: int) -> List[Dict[str, Any]]:
        """Chroma similarity search over embedded documents and journal entries."""
        embedding = self.vector_memory._generate_embedding(query[:MAX_EMBED_CHARS])
        if not embedding:
            print("[MemoryVault] semantic search skipped (embedding unavailable)")
            return []
        clauses = []
        if categories:
            clauses.append({"category": {"$in": [str(c) for c in categories]}})
        if user:
            clauses.append({"user": user})
        where = clauses[0] if len(clauses) == 1 else ({"$and": clauses} if clauses else None)
        try:
            res = self.collection.query(
                query_embeddings=[embedding], n_results=limit, where=where,
                include=["metadatas", "distances"])
        except Exception as e:
            print(f"[MemoryVault] semantic query failed: {e}")
            return []
        out: List[Dict[str, Any]] = []
        metas = (res.get("metadatas") or [[]])[0] or []
        dists = (res.get("distances") or [[]])[0] or []
        for meta, dist in zip(metas, dists):
            if not isinstance(meta, dict):
                continue
            out.append({
                "path": str(meta.get("path", "")),
                "title": meta.get("title", ""),
                "category": meta.get("category", ""),
                "user": meta.get("user", ""),
                "tags": meta.get("tags", ""),
                "updated": meta.get("updated", ""),
                "score": round(max(0.0, 1.0 - float(dist)), 4),
                "match_type": "semantic",
                "snippet": str(meta.get("content", ""))[:MAX_SNIPPET_CHARS],
            })
        return out[:limit]

    def search(self, query: str, categories=None, user: str = "",
               limit: int = 10, mode: str = "hybrid") -> List[Dict[str, Any]]:
        """Search the vault: hybrid (default), keyword-only, or semantic-only."""
        query = str(query or "").strip()
        if not query:
            raise ValueError("'query' is required")
        mode = str(mode or "hybrid").strip().lower()
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be one of: hybrid, keyword, semantic")
        limit = max(1, min(int(limit), 50))
        with _vault_lock:
            kw = self._keyword_search(query, categories, user, limit) \
                if mode in ("hybrid", "keyword") else []
            sem = self._semantic_search(query, categories, user, limit) \
                if mode in ("hybrid", "semantic") else []
        if mode == "keyword":
            return kw[:limit]
        if mode == "semantic":
            return sem[:limit]
        merged: Dict[str, Dict[str, Any]] = {}
        max_kw = max((r["score"] for r in kw), default=0) or 1.0
        max_sem = max((r["score"] for r in sem), default=0) or 1.0
        for r in kw:
            m = dict(r)
            m["score"] = round(r["score"] / max_kw, 4)
            merged[m["path"]] = m
        for r in sem:
            m = dict(r)
            m["score"] = round(r["score"] / max_sem, 4)
            if m["path"] in merged:
                merged[m["path"]]["score"] = round(
                    merged[m["path"]]["score"] + m["score"], 4)
                merged[m["path"]]["match_type"] = "hybrid"
            else:
                merged[m["path"]] = m
        return sorted(merged.values(),
                      key=lambda r: r["score"], reverse=True)[:limit]

    # ============== MAINTENANCE ==============

    def rebuild_index(self, force: bool = False) -> Dict[str, Any]:
        """Re-embed vault markdown files into the semantic index.

        force=False : embed only files missing from the index (new files).
        force=True  : wipe the collection first, then re-embed everything.
        """
        embedded = skipped = 0
        with _vault_lock:
            if force:
                try:
                    existing = self.collection.get(include=[]) or {}
                    if existing.get("ids"):
                        self.collection.delete(ids=existing["ids"])
                except Exception as e:
                    print(f"[MemoryVault] index wipe failed: {e}")
                    return {"status": "error", "detail": str(e)}
            for subdir in VAULT_CATEGORIES.values():
                base = os.path.join(self.vault_root, subdir)
                if not os.path.isdir(base):
                    continue
                for root, _dirs, files in os.walk(base):
                    for fname in files:
                        if not fname.endswith(".md"):
                            continue
                        rel = os.path.relpath(
                            os.path.join(root, fname), self.vault_root)
                        rel = rel.replace(os.sep, "/")
                        if not force:
                            try:
                                hit = self.collection.get(
                                    ids=[self._chroma_id(rel)]).get("ids")
                                if hit:
                                    skipped += 1
                                    continue
                            except Exception:
                                pass  # index lookup failed -> re-embed
                        if self._embed_document(rel):
                            embedded += 1
                        else:
                            skipped += 1
        return {"status": "ok", "force": bool(force), "embedded": embedded,
                "skipped": skipped, "timestamp": self._now_iso()}

    def get_stats(self) -> Dict[str, Any]:
        """Lightweight vault counts for the /api/memory/vault/stats route."""
        counts: Dict[str, int] = {}
        total = 0
        for cat, subdir in VAULT_CATEGORIES.items():
            n = 0
            base = os.path.join(self.vault_root, subdir)
            if os.path.isdir(base):
                for _root, _dirs, files in os.walk(base):
                    n += sum(1 for f in files if f.endswith(".md"))
            counts[cat] = n
            total += n
        try:
            indexed = int(self.collection.count())
        except Exception:
            indexed = -1
        return {
            "vault_root": self.vault_root,
            "collection": VAULT_COLLECTION,
            "categories": counts,
            "documents": total,
            "indexed_entries": indexed,
            "timestamp": self._now_iso(),
        }


# ============== SINGLETON + WATCHDOG ==============

_vault_instance: Optional[MemoryVault] = None
_vault_singleton_lock = threading.Lock()
_vault_watchdog_started = False


def get_memory_vault() -> MemoryVault:
    """Process-wide MemoryVault singleton used by the route blueprints."""
    global _vault_instance
    if _vault_instance is None:
        with _vault_singleton_lock:
            if _vault_instance is None:
                _vault_instance = MemoryVault()
    return _vault_instance


def _vault_watchdog(interval: float = 30.0) -> None:
    """Daemon loop: re-embed vault files created/edited outside the API
    (e.g. directly on the host via Obsidian). Tracks mtimes so host edits
    are picked up automatically; failed embeds retry on the next pass.
    """
    seen: Dict[str, float] = {}
    while True:
        try:
            vault = get_memory_vault()
            changed = 0
            for subdir in VAULT_CATEGORIES.values():
                base = os.path.join(vault.vault_root, subdir)
                if not os.path.isdir(base):
                    continue
                for root, _dirs, files in os.walk(base):
                    for fname in files:
                        if not fname.endswith(".md"):
                            continue
                        rel = os.path.relpath(
                            os.path.join(root, fname), vault.vault_root)
                        try:
                            mtime = os.stat(os.path.join(root, fname)).st_mtime
                        except OSError:
                            continue
                        if seen.get(rel) == mtime:
                            continue
                        if vault._embed_document(rel.replace(os.sep, "/")):
                            seen[rel] = mtime
                            changed += 1
            if changed:
                print(f"[MemoryVault] watchdog re-embedded {changed} file(s)")
        except Exception as e:
            print(f"[MemoryVault] watchdog error: {e}")
        time.sleep(interval)


def start_vault_watchdog(interval: float = 30.0) -> None:
    """Idempotent start of the vault watchdog daemon thread."""
    global _vault_watchdog_started
    if _vault_watchdog_started:
        return
    with _vault_singleton_lock:
        if _vault_watchdog_started:
            return
        _vault_watchdog_started = True
        threading.Thread(target=_vault_watchdog, args=(interval,),
                         name="vault-watchdog", daemon=True).start()
        print("[MemoryVault] watchdog started")