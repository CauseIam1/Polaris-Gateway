#!/usr/bin/env python3
"""
Polaris File Tools - Secure Local File Manipulation Suite
Restricted to ingest/ directory with strict path validation
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

# Security: Base directory restriction
INGEST_DIR = Path("/data/freeroam/polaris-gateway/ingest")

# Ensure ingest directory exists on startup
INGEST_DIR.mkdir(parents=True, exist_ok=True)

def _validate_path(filename: str) -> Path:
    """
    Validate and resolve file path to ensure it stays within ingest/ directory.
    Prevents directory traversal attacks and unauthorized file access.
    """
    # Sanitize filename - only allow alphanumeric, dashes, underscores, dots
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', filename):
        raise ValueError(f"Invalid filename: {filename}. Only alphanumeric, dashes, underscores, and dots allowed.")
    
    # Prevent directory traversal
    if '..' in filename or filename.startswith('/'):
        raise ValueError(f"Invalid filename: {filename}. Directory traversal not allowed.")
    
    # Resolve full path
    file_path = INGEST_DIR / filename
    
    # Ensure resolved path is still within ingest directory
    try:
        file_path.resolve().relative_to(INGEST_DIR.resolve())
    except ValueError:
        raise ValueError(f"Path {file_path} is outside allowed ingest directory")
    
    return file_path


# v3.4 Polaris doc paging: one read_doc call returns up to MAX_READ_PAGE_CHARS
# characters starting at `offset`. The gateway's tool-result truncation allows
# this same budget for the "content" field, so a full page reaches the model.
MAX_READ_PAGE_CHARS = 8000


def read_doc(filename: str, offset: int = 0, length: int = MAX_READ_PAGE_CHARS) -> Dict:
    """Read a slice of a markdown document (offset-based paging).

    Large documents no longer hit the model's context window in one gulp:
    each call returns up to `length` characters starting at `offset`, plus
    navigation metadata (size_chars, has_more, next_offset) so the caller
    can keep paging until the end of the file.
    """
    try:
        file_path = _validate_path(filename)

        if not file_path.exists():
            return {"success": False, "error": f"File not found: {filename}"}

        content = file_path.read_text(encoding='utf-8')
        size_chars = len(content)

        # Clamp the slice: paging starts at 0, and the page size is bounded so
        # a hallucinated length can never blow up the follow-up prompt.
        offset = max(0, min(int(offset or 0), size_chars))
        length = max(1, min(int(length or MAX_READ_PAGE_CHARS), MAX_READ_PAGE_CHARS))

        page = content[offset:offset + length]
        end = offset + len(page)
        has_more = end < size_chars
        return {
            "success": True,
            "filename": filename,
            "content": page,
            "offset": offset,                            # where this page starts
            "returned_chars": len(page),                 # chars in this page
            "size_bytes": len(content.encode('utf-8')), # true on-disk UTF-8 size
            "size_chars": size_chars,                    # total characters in the file
            "line_count": page.count('\n') + 1,
            "has_more": has_more,
            "next_offset": end if has_more else None     # pass as `offset` to continue
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Read error: {str(e)}"}


def write_doc(filename: str, text: str) -> Dict:
    """Write content to a markdown document (overwrites if exists)."""
    try:
        file_path = _validate_path(filename)
        INGEST_DIR.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding='utf-8')
        
        return {
            "success": True,
            "filename": filename,
            "action": "write",
            "size_bytes": len(text.encode('utf-8')),  # true UTF-8 byte size
            "size_chars": len(text),                  # character count
            "line_count": text.count('\n') + 1,
            "path": str(file_path)
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Write error: {str(e)}"}


def append_doc(filename: str, text: str) -> Dict:
    """Append content to an existing markdown document."""
    try:
        file_path = _validate_path(filename)
        INGEST_DIR.mkdir(parents=True, exist_ok=True)
        
        existing_content = ""
        if file_path.exists():
            existing_content = file_path.read_text(encoding='utf-8')
        
        if existing_content and not existing_content.endswith('\n'):
            text = '\n' + text
        
        new_content = existing_content + text
        file_path.write_text(new_content, encoding='utf-8')
        
        return {
            "success": True,
            "filename": filename,
            "action": "append",
            "appended_bytes": len(text.encode('utf-8')),      # true UTF-8 byte size
            "appended_chars": len(text),                      # character count
            "total_bytes": len(new_content.encode('utf-8')), # true UTF-8 byte size
            "total_chars": len(new_content),                   # character count
            "total_lines": new_content.count('\n') + 1,
            "path": str(file_path)
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Append error: {str(e)}"}


def list_docs(pattern: str = "*.md") -> Dict:
    """List documents in ingest/ directory matching pattern."""
    try:
        if not INGEST_DIR.exists():
            INGEST_DIR.mkdir(parents=True, exist_ok=True)
            return {"success": True, "files": [], "count": 0, "directory": str(INGEST_DIR), "pattern": pattern}
        
        files = []
        for file_path in INGEST_DIR.glob(pattern):
            if file_path.is_file():
                stat = file_path.stat()
                files.append({
                    "filename": file_path.name,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "path": str(file_path)
                })
        
        files.sort(key=lambda x: x['modified'], reverse=True)
        
        return {
            "success": True,
            "files": files,
            "count": len(files),
            "directory": str(INGEST_DIR),
            "pattern": pattern
        }
    except Exception as e:
        return {"success": False, "error": f"List error: {str(e)}"}


def delete_doc(filename: str) -> Dict:
    """Delete a document from ingest/ directory."""
    try:
        file_path = _validate_path(filename)
        
        if not file_path.exists():
            return {"success": False, "error": f"File not found: {filename}"}
        
        file_path.unlink()
        
        return {
            "success": True,
            "filename": filename,
            "action": "delete",
            "message": f"Successfully deleted {filename}"
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Delete error: {str(e)}"}


# Tool schemas for LLM function calling
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_doc",
            "description": "Read a markdown document from the ingest directory, one page at a time. Returns up to `length` characters starting at `offset`, plus `size_chars`, `has_more`, and `next_offset` so large files can be walked in chunks. When `has_more` is true, call again with `offset` set to `next_offset` to read the next page. Use this to retrieve existing notes, documentation, or knowledge files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The name of the file to read (e.g., 'northstar.md', 'project-notes.md', 'meeting-2024-01-15.md')"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Character offset to start reading from (default 0). Use the `next_offset` value from a previous page to continue reading.",
                        "default": 0
                    },
                    "length": {
                        "type": "integer",
                        "description": "Maximum characters to return in this page (default 8000, hard-capped at 8000).",
                        "default": 8000
                    }
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_doc",
            "description": "Write content to a markdown document. Creates new file or overwrites existing file. Use for creating new notes, summaries, or documentation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The name of the file to write"
                    },
                    "text": {
                        "type": "string",
                        "description": "The complete content to write to the file"
                    }
                },
                "required": ["filename", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_doc",
            "description": "Append content to an existing markdown document. Use this to add updates, follow-up notes, or additional information to existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The name of the file to append to"
                    },
                    "text": {
                        "type": "string",
                        "description": "The content to append to the file"
                    }
                },
                "required": ["filename", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_docs",
            "description": "List all markdown documents in the ingest directory. Use this to discover available files or check what knowledge is stored.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter files (default: '*.md')",
                        "default": "*.md"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_doc",
            "description": "Delete a markdown document from the ingest directory. Use with caution - this action cannot be undone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The name of the file to delete"
                    }
                },
                "required": ["filename"]
            }
        }
    }
]


def get_tool_schemas_json() -> str:
    """Return tool schemas as JSON string for LLM system prompt."""
    import json
    return json.dumps(TOOL_SCHEMAS, indent=2)
# ─────────────────────────────────────────────────────────────────────────────
# v1.4.2 North Star workspace access — READ-ONLY file intelligence for Polaris
# docker-compose mounts the whole host repo (/home/causeiam/docker-containers)
# at /data/workspace:ro; the two tools below let Polaris FIND and READ any file
# in the repo (e.g. "can you look at trading-bot.md?") without an SSH hop.
# Nothing here ever writes — repo edits stay with operator + Cline.

WORKSPACE_DIR = Path("/data/workspace")
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", "target", ".cache", ".gradle", ".idea", ".vscode",
}
_MAX_WORKSPACE_MATCHES = 50


def _validate_workspace_path(rel_path: str) -> Path:
    """Resolve a workspace-relative path under WORKSPACE_DIR, refusing
    traversal outside the root. Raises ValueError on bad input."""
    if not rel_path or not str(rel_path).strip():
        raise ValueError("Empty path — name the file to read")
    clean = str(rel_path).strip().replace("\\", "/").lstrip("/")
    if clean.startswith("../") or ".." in clean.split("/"):
        raise ValueError("Directory traversal not allowed")
    root = WORKSPACE_DIR.resolve()
    resolved = (WORKSPACE_DIR / clean).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"Path '{rel_path}' escapes the workspace root")
    return resolved


def list_workspace_files(query: str = "", extension: str = "") -> Dict:
    """Find repo files by filename substring (and optional extension filter).

    Query is matched case-insensitively against the file's relative path, so
    both names ("trading-bot.md") and folder fragments ("LGI/") work. Walks
    /data/workspace read-only, skipping VCS/dependency/build directories.
    Returns {success, matches[], count, truncated} and never raises.
    """
    try:
        if not WORKSPACE_DIR.is_dir():
            return {"success": False,
                    "error": "workspace volume not mounted (/data/workspace missing)"}
        q = str(query or "").strip().lower()
        ext = str(extension or "").strip().lower().lstrip(".")
        if not q and not ext:
            return {"success": False,
                    "error": "Empty query — give a filename fragment (e.g. 'trading-bot') "
                             "and/or an extension (e.g. 'md')"}
        matches: List[str] = []
        truncated = False
        root = WORKSPACE_DIR.resolve()
        for current, dirs, files in os.walk(WORKSPACE_DIR):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            if len(matches) >= _MAX_WORKSPACE_MATCHES:
                truncated = True
                break
            for name in files:
                rel = str(Path(current).joinpath(name).relative_to(root))
                low = rel.lower()
                if q and q not in low:
                    continue
                if ext and not low.endswith("." + ext):
                    continue
                matches.append(rel)
                if len(matches) >= _MAX_WORKSPACE_MATCHES:
                    truncated = True
                    break
        return {
            "success": True,
            "query": q or (f"*.{ext}" if ext else ""),
            "root": "/data/workspace (= /home/causeiam/docker-containers)",
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
        }
    except Exception as e:
        return {"success": False, "error": f"List error: {str(e)}"}


def read_workspace_file(path: str, offset: int = 0,
                        length: int = MAX_READ_PAGE_CHARS) -> Dict:
    """Read a slice of ONE repo file (UTF-8 text; offset-based paging).

    Same page contract as read_doc (offset / length / next_offset) so the
    tool-result truncation never chokes and long files can be walked slice
    by slice. Read-only: raises-free, returns {success, error} on failure.
    """
    try:
        file_path = _validate_workspace_path(path)
        if not file_path.exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not file_path.is_file():
            return {"success": False, "error": f"Not a regular file: {path}"}
        try:
            content = file_path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return {"success": False, "error": f"Binary file — text read not possible: {path}"}
        size_chars = len(content)
        offset = max(0, min(int(offset or 0), size_chars))
        length = max(1, min(int(length or MAX_READ_PAGE_CHARS), MAX_READ_PAGE_CHARS))
        page = content[offset:offset + length]
        end = offset + len(page)
        has_more = end < size_chars
        return {
            "success": True,
            "path": str(file_path.relative_to(WORKSPACE_DIR.resolve())),
            "content": page,
            "offset": offset,
            "returned_chars": len(page),
            "size_chars": size_chars,
            "line_count": page.count("\n") + 1,
            "has_more": has_more,
            "next_offset": end if has_more else None,
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Read error: {str(e)}"}
