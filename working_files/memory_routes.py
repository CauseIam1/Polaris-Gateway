#!/usr/bin/env python3
"""
Polaris Memory Vault - HTTP API Blueprint
==========================================
Vault routes are mounted under /api/memory/vault/* on the main gateway
app and automatically inherit its app-level LAN allowlist check
(@app.before_request). No separate auth layer is required.

Endpoints:
    GET  /api/memory/vault/stats     vault + index statistics
    GET  /api/memory/vault/list      list documents (filters: category, user, limit)
    GET  /api/memory/vault/doc       fetch one document (?path=...)
    POST /api/memory/vault/write     create a document
    POST /api/memory/vault/update    update an existing document by path
    POST /api/memory/vault/query     hybrid / keyword / semantic search
    POST /api/memory/vault/delete    delete a document by path
    POST /api/memory/vault/reindex   rebuild the semantic index from files
"""

from datetime import datetime

from flask import Blueprint, jsonify, request

from memory_vault import (
    CATEGORY_NAMES,
    VAULT_CATEGORIES,
    get_memory_vault,
)

memory_bp = Blueprint("memory_vault_api", __name__, url_prefix="/api/memory")


def _now() -> str:
    return datetime.now().isoformat()


def _vault():
    return get_memory_vault()


def _error(message, code: int = 400):
    return jsonify({"status": "error", "error": str(message), "timestamp": _now()}), code


def _payload() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _int_param(source: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(source.get(key, default)), high))
    except (TypeError, ValueError):
        return default


@memory_bp.route("/vault/stats", methods=["GET"])
def vault_stats():
    try:
        return jsonify({"status": "success", "stats": _vault().get_stats(),
                        "timestamp": _now()})
    except Exception as e:
        return _error(e, 500)


@memory_bp.route("/vault/list", methods=["GET"])
def vault_list():
    try:
        category = request.args.get("category")
        if category and category not in VAULT_CATEGORIES:
            return _error(f"Invalid category '{category}'. Valid: {CATEGORY_NAMES}")
        user = request.args.get("user", "")
        limit = _int_param(request.args, "limit", 200, 1, 500)
        docs = _vault().list_documents(category=category, user=user, limit=limit)
        return jsonify({"status": "success", "count": len(docs),
                        "documents": docs, "timestamp": _now()})
    except Exception as e:
        return _error(e, 500)


@memory_bp.route("/vault/doc", methods=["GET"])
def vault_doc():
    try:
        path = request.args.get("path", "")
        if not path:
            return _error("'path' query parameter is required")
        doc = _vault().get_document(path)
        return jsonify({"status": "success", "document": doc, "timestamp": _now()})
    except FileNotFoundError as e:
        return _error(e, 404)
    except ValueError as e:
        return _error(e, 400)
    except Exception as e:
        return _error(e, 500)

@memory_bp.route("/vault/write", methods=["POST"])
def vault_write():
    try:
        data = _payload()
        category = str(data.get("category", "")).strip().lower()
        if category not in VAULT_CATEGORIES:
            return _error(f"Invalid category '{category}'. Valid: {CATEGORY_NAMES}")
        title = str(data.get("title", "")).strip()
        if not title:
            return _error("'title' is required")
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        result = _vault().create_document(
            category=category,
            title=title,
            content=data.get("content", ""),
            tags=tags,
            user=str(data.get("user", "") or ""),
            source=str(data.get("source", "api") or "api"),
            allow_core=bool(data.get("allow_core", False)),
            extra=data.get("extra") if isinstance(data.get("extra"), dict) else None,
        )
        return jsonify({"status": "success", "document": result, "timestamp": _now()})
    except PermissionError as e:
        return _error(e, 403)
    except (ValueError, TypeError) as e:
        return _error(e, 400)
    except Exception as e:
        return _error(e, 500)


@memory_bp.route("/vault/update", methods=["POST"])
def vault_update():
    try:
        data = _payload()
        path = str(data.get("path", "")).strip()
        if not path:
            return _error("'path' is required")
        title = data.get("title")
        tags = data.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        result = _vault().update_document(
            path,
            content=str(data.get("content", "") or ""),
            title=title,
            tags=tags if isinstance(tags, list) else None,
        )
        return jsonify({"status": "success", "document": result, "timestamp": _now()})
    except PermissionError as e:
        return _error(e, 403)
    except FileNotFoundError as e:
        return _error(e, 404)
    except ValueError as e:
        return _error(e, 400)
    except Exception as e:
        return _error(e, 500)


@memory_bp.route("/vault/query", methods=["POST"])
def vault_query():
    try:
        data = _payload()
        query = str(data.get("query", "")).strip()
        if not query:
            return _error("'query' is required")
        categories = data.get("categories")
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(",") if c.strip()]
        if not isinstance(categories, list):
            categories = None
        invalid = [c for c in (categories or []) if c not in VAULT_CATEGORIES]
        if invalid:
            return _error(f"Invalid categories: {invalid}. Valid: {CATEGORY_NAMES}")
        mode = str(data.get("mode", "hybrid")).strip().lower()
        if mode not in ("hybrid", "keyword", "semantic"):
            return _error("mode must be one of: hybrid, keyword, semantic")
        limit = _int_param(data, "limit", 10, 1, 50)
        results = _vault().search(
            query=query,
            categories=categories,
            user=str(data.get("user", "") or ""),
            limit=limit,
            mode=mode,
        )
        return jsonify({"status": "success", "query": query, "mode": mode,
                        "count": len(results), "results": results,
                        "timestamp": _now()})
    except ValueError as e:
        return _error(e, 400)
    except Exception as e:
        return _error(e, 500)


@memory_bp.route("/vault/delete", methods=["POST"])
def vault_delete():
    try:
        data = _payload()
        path = str(data.get("path", "")).strip()
        if not path:
            return _error("'path' is required")
        result = _vault().delete_document(path)
        return jsonify({"status": "success", "result": result, "timestamp": _now()})
    except PermissionError as e:
        return _error(e, 403)
    except FileNotFoundError as e:
        return _error(e, 404)
    except ValueError as e:
        return _error(e, 400)
    except Exception as e:
        return _error(e, 500)


@memory_bp.route("/vault/reindex", methods=["POST"])
def vault_reindex():
    try:
        force = bool(_payload().get("force", False))
        result = _vault().rebuild_index(force=force)
        return jsonify({"status": "success", "result": result, "timestamp": _now()})
    except Exception as e:
        return _error(e, 500)