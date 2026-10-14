#!/usr/bin/env python3
"""
Polaris Gateway - Memory Vault Chat Tools (Phase 5 + v1.5 voice journal)

Chat access to the Obsidian-style Memory Vault. Two tools:

  memory_vault_query   READ-only consult of her distilled long-term memory:
                       lessons learned, architecture notes, user knowledge,
                       and journal entries. Primary use case: reviewing prior
                       lessons BEFORE performing risky or unfamiliar operations
                       (e.g. remote process management) instead of
                       rediscovering failures the hard way.

  memory_journal_write APPEND-only operator journal ("Polaris, log this"): a
                       timestamped entry lands in 04-Journal/YYYY-MM-DD.md and
                       is embedded for semantic recall. This is the ONLY vault
                       write reachable from chat - no edits, no deletes, no
                       core/architecture/lesson writes from here.

Backed by memory_vault.MemoryVault (markdown files are the source of truth;
ChromaDB is a rebuildable semantic index). All vault writes besides the
append-only journal remain the operator's (dashboard REST) and the
distillation/learning process's.

Configuration: none required - VAULT_ROOT is handled by memory_vault.
"""

import json
from typing import Any, Dict, List

from memory_vault import get_memory_vault, VAULT_CATEGORIES

# Guard rails for the chat context window
MAX_RESULTS = 10          # hard cap on returned search/list results
MAX_READ_CHARS = 4000     # hard cap on full-document content returned

# Tool schemas for LLM function calling
MEMORY_TOOL_SCHEMAS = [
    {
        "name": "memory_vault_query",
        "description": (
            "Read-only query of your long-term Memory Vault (your own distilled "
            "lessons, architecture notes, user knowledge, and journal). Use it "
            "BEFORE risky or unfamiliar operations to review prior lessons. "
            "Provide 'query' to search the vault, a 'path' (from a previous "
            "result) to read one note in full, or a 'category' alone to list "
            "note titles. You cannot write, modify, or delete vault notes from chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text search terms, e.g. 'remote daemon process kill pgrep autostart'"
                },
                "category": {
                    "type": "string",
                    "enum": ["core", "architecture", "user", "lesson", "journal", "attachment"],
                    "description": "Optional category filter. Pass without query/path to list note titles."
                },
                "mode": {
                    "type": "string",
                    "enum": ["hybrid", "keyword", "semantic"],
                    "description": "Search mode. hybrid (default) merges keyword and semantic matches."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 5, max: 10)"
                },
                "path": {
                    "type": "string",
                    "description": "Vault-relative path of one note to read in full, e.g. '03-Lessons/<file>.md' (take it from a search result)"
                }
            }
        }
    },
    {
        "name": "memory_journal_write",
        "description": (
            "Append a timestamped entry to today's journal page ("
            "04-Journal/YYYY-MM-DD.md) in your Memory Vault and embed it for "
            "search. Use when the operator says 'log this', 'remember this', "
            "'make a note of it', or asks you to record an outcome for later. "
            "Keep 'entry' self-contained (context plus the fact), one topic "
            "per call. Journal entries are append-only: you cannot edit or "
            "delete them afterwards."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "string",
                    "description": "The note to log. Make it self-contained - enough context to be useful months later. Max ~2000 chars."
                },
                "title": {
                    "type": "string",
                    "description": "Optional short label used as the entry heading (default: derived from the entry's first words)."
                },
                "tags": {
                    "type": "string",
                    "description": "Optional comma-separated tags, e.g. 'radar, x17, followup'"
                }
            },
            "required": ["entry"]
        }
    }
]


def get_memory_tool_schemas_json() -> str:
    """Return Memory tool schemas as JSON string for LLM system prompt."""
    return json.dumps(MEMORY_TOOL_SCHEMAS, indent=2)


def memory_vault_query(query: str = "", category: str = "",
                       mode: str = "hybrid", limit: int = 5,
                       path: str = "") -> Dict[str, Any]:
    """
    Consult the Memory Vault (read-only).

    Behaviour (first matching argument wins):
      - path      -> fetch one full document
      - query     -> keyword + semantic search (optionally filtered by category)
      - category  -> list note titles/paths in that category

    Always returns a dict with a "success" key - never raises - so a failed
    lookup degrades gracefully inside the chat tool loop.
    """
    try:
        query = str(query or "").strip()
        category = str(category or "").strip()
        mode = str(mode or "hybrid").strip()
        path = str(path or "").strip()

        try:
            limit = max(1, min(int(limit), MAX_RESULTS))
        except (TypeError, ValueError):
            limit = 5

        vault = get_memory_vault()

        # --- 1. Full document read -------------------------------------
        if path:
            doc = vault.get_document(path)
            content = str(doc.get("content", ""))
            truncated = len(content) > MAX_READ_CHARS
            return {
                "success": True,
                "action": "read",
                "document": {
                    "path": doc.get("path", path),
                    "meta": doc.get("meta", {}),
                    "content": content[:MAX_READ_CHARS],
                    "truncated": truncated,
                    "size_bytes": doc.get("size_bytes", 0),
                }
            }

        # --- 2. Keyword/semantic search --------------------------------
        if query:
            categories = None
            if category:
                if category not in VAULT_CATEGORIES:
                    return {
                        "success": False,
                        "error": f"Invalid category '{category}'. Valid: {sorted(VAULT_CATEGORIES)}"
                    }
                categories = [category]
            results = vault.search(query, categories=categories,
                                   limit=limit, mode=mode)
            slim: List[Dict[str, Any]] = []
            for r in results:
                slim.append({
                    "path": r.get("path", ""),
                    "title": r.get("title", ""),
                    "category": r.get("category", ""),
                    "tags": r.get("tags", ""),
                    "score": r.get("score", 0),
                    "match_type": r.get("match_type", ""),
                    "snippet": r.get("snippet", ""),
                })
            return {
                "success": True,
                "action": "search",
                "query": query,
                "category": category or "all",
                "count": len(slim),
                "results": slim,
            }

        # --- 3. Category browsing --------------------------------------
        if category:
            if category not in VAULT_CATEGORIES:
                return {
                    "success": False,
                    "error": f"Invalid category '{category}'. Valid: {sorted(VAULT_CATEGORIES)}"
                }
            docs = vault.list_documents(category=category, limit=limit)
            listing = [
                {"path": d.get("path", ""), "title": d.get("title", ""),
                 "tags": d.get("tags", ""), "snippet": d.get("snippet", "")}
                for d in docs
            ]
            return {
                "success": True,
                "action": "list",
                "category": category,
                "count": len(listing),
                "results": listing,
            }

        return {
            "success": False,
            "error": ("Nothing to do: provide a 'query' to search the vault, "
                      "a 'path' to read one note, or a 'category' to list notes.")
        }

    except FileNotFoundError as e:
        return {"success": False, "error": f"Note not found: {e}"}
    except (ValueError, PermissionError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Memory vault query failed: {e}"}


# v1.5 voice journal: the ONLY chat-side vault write. Journal category is
# append-only inside memory_vault (no edit, no delete, never core), so this
# cannot damage doctrine, architecture notes, or lessons.
MAX_JOURNAL_ENTRY_CHARS = 2000  # hard cap per chat-side journal entry


def memory_journal_write(entry: str, title: str = "",
                         tags: str = "") -> Dict[str, Any]:
    """
    Log one operator-driven note to today's journal (04-Journal/YYYY-MM-DD.md).

    Appends a timestamped '## HH:MM:SS - <title>' entry to the daily journal
    file and embeds the entry immediately for semantic recall via
    memory_vault_query. The entry text (not the frontmatter title) is what
    future searches match on, so it must be self-contained.
    """
    try:
        entry = str(entry or "").strip()
        if not entry:
            return {"success": False,
                    "error": "Nothing to log: 'entry' is required."}
        if len(entry) > MAX_JOURNAL_ENTRY_CHARS:
            entry = entry[:MAX_JOURNAL_ENTRY_CHARS]

        if str(title or "").strip():
            entry_title = str(title).strip()[:60]
        else:
            # Derive a scannable heading from the entry's first words.
            first_words = " ".join(entry.split()[:8]).strip() or "Voice Log"
            entry_title = first_words[:60]

        raw_tags = str(tags or "").strip()
        tag_list = ([t.strip() for t in raw_tags.split(",") if t.strip()]
                    if raw_tags else None)

        vault = get_memory_vault()
        result = vault.create_document(
            category="journal",
            title=entry_title,
            content=entry,
            tags=tag_list,
            source="chat-journal",
        )
        return {
            "success": True,
            "action": "journal_append",
            "journal_file": result.get("path", ""),
            "entry_time": result.get("entry_time", ""),
            "title": result.get("title", entry_title),
            "indexed": result.get("indexed", False),
            "message": ("Logged. The entry is timestamped in today's journal "
                        "page and searchable in your Memory Vault."),
        }
    except (ValueError, PermissionError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Journal write failed: {e}"}


# Export all tool functions
__all__ = [
    "memory_vault_query",
    "memory_journal_write",
    "MEMORY_TOOL_SCHEMAS",
    "get_memory_tool_schemas_json"
]