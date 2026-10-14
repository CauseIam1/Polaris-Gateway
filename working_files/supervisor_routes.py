#!/usr/bin/env python3
"""
Polaris Supervisor Audit - Screen/Clipboard Triage Blueprint
=============================================================
Receives periodic activity snapshots from the FreeRoam mobile edge
(active window title, screen keywords, clipboard snippet) and triages
them with fast local heuristics plus an optional Ollama LLM pass.
SUPERVISOR_ALERT events are persisted to the Memory Vault journal
(embedded + searchable) and kept in a bounded in-memory audit log.

Endpoints:
    POST /api/supervisor/audit          submit an activity snapshot for triage
    GET  /api/supervisor/audit/recent   recent audit records (default 25)
    GET  /api/supervisor/status         lightweight service status
"""

import json
import os
import re
import threading
from collections import deque
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request

from memory_vault import get_memory_vault

supervisor_bp = Blueprint("supervisor_audit_api", __name__, url_prefix="/api/supervisor")

# Mirror the gateway's Ollama configuration exactly
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "my-stark-ai:latest")

ALERT_KEYWORDS = {
    "financial": ["invoice", "payment", "billing", "refund", "charge",
                  "transaction", "bank", "wallet", "xrp", "crypto", "price"],
    "security": ["password", "login", "2fa", "otp", "passphrase",
                 "private key", "phishing", "breach", "ssh key"],
    "urgent": ["urgent", "asap", "emergency", "critical", "immediately",
               "deadline", "overdue", "action required"],
    "error": ["error", "failed", "failure", "crash", "exception",
              "traceback", "timeout", "unreachable", "panic"],
    "schedule": ["meeting", "appointment", "calendar", "standup",
                 "zoom", "teams call", "schedule"],
}

AUDIT_LOG = deque(maxlen=200)
AUDIT_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat()


def _error(message, code: int = 400):
    return jsonify({"status": "error", "error": str(message), "timestamp": _now()}), code


def _heuristic_triage(active_window: str, clipboard: str, keywords) -> dict:
    """Fast local keyword scan across the activity snapshot."""
    parts = [str(active_window or "")]
    parts.extend(str(k) for k in (keywords or []))
    parts.append(str(clipboard or ""))
    haystack = " ".join(parts).lower()
    if not haystack.strip():
        return {"verdict": "NO_ACTION", "category": "general", "hits": {},
                "reason": "empty snapshot"}

    hits: dict = {}
    for category, terms in ALERT_KEYWORDS.items():
        matched = [t for t in terms if t in haystack]
        if matched:
            hits[category] = matched

    if not hits:
        return {"verdict": "NO_ACTION", "category": "general", "hits": {},
                "reason": "no alert keywords detected"}

    # Strong signals alert immediately; soft signals log only
    if any(c in hits for c in ("security", "financial", "urgent", "error")):
        primary = next(c for c in ("security", "financial", "urgent", "error")
                       if c in hits)
        return {"verdict": "SUPERVISOR_ALERT", "category": primary, "hits": hits,
                "reason": f"keyword matches: {primary}"}
    return {"verdict": "LOG_ONLY", "category": "schedule", "hits": hits,
            "reason": "soft keyword match (schedule)"}

def _llm_triage(payload: dict) -> dict:
    """Optional Ollama pass. Returns {verdict, category, reason, model}."""
    prompt = (
        "You are the Polaris Supervisor, a local privacy-first triage agent. "
        "Decide whether this workstation activity snapshot contains an event the "
        "operator must be alerted to (financial action, security-sensitive action, "
        "urgent request, system failure, scheduled commitment) or routine activity.\n"
        "Respond with ONLY one compact JSON object on a single line, no markdown:\n"
        '{"verdict": "SUPERVISOR_ALERT" or "LOG_ONLY" or "NO_ACTION", '
        '"category": "financial|security|urgent|error|schedule|general", '
        '"reason": "max 20 words"}\n\n'
        "ACTIVITY SNAPSHOT:\n"
        f"- active_window: {str(payload.get('active_window', ''))[:200]}\n"
        f"- application: {str(payload.get('app', ''))[:80]}\n"
        f"- screen_keywords: {json.dumps(list(payload.get('screen_keywords', []))[:20])}\n"
        f"- clipboard_snippet: {str(payload.get('clipboard_snippet', ''))[:300]}\n"
    )
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.2}},
            timeout=15,
        )
        response.raise_for_status()
        raw = response.json().get("response", "")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return {"verdict": "UNAVAILABLE", "category": "general",
                    "reason": "no JSON in LLM output", "model": OLLAMA_MODEL}
        parsed = json.loads(match.group(0))
        verdict = str(parsed.get("verdict", "NO_ACTION")).upper().strip()
        if verdict not in ("SUPERVISOR_ALERT", "LOG_ONLY", "NO_ACTION"):
            verdict = "NO_ACTION"
        return {"verdict": verdict,
                "category": str(parsed.get("category", "general")).lower()[:40],
                "reason": str(parsed.get("reason", ""))[:300],
                "model": OLLAMA_MODEL}
    except Exception as e:
        return {"verdict": "UNAVAILABLE", "category": "general",
                "reason": f"LLM triage failed: {e}", "model": OLLAMA_MODEL}


@supervisor_bp.route("/status", methods=["GET"])
def supervisor_status():
    try:
        return jsonify({"status": "success", "ollama_url": OLLAMA_URL,
                        "ollama_model": OLLAMA_MODEL,
                        "audit_log_size": len(AUDIT_LOG),
                        "timestamp": _now()})
    except Exception as e:
        return _error(e, 500)


@supervisor_bp.route("/audit/recent", methods=["GET"])
def supervisor_recent():
    try:
        try:
            limit = max(1, min(int(request.args.get("limit", 25)), 200))
        except ValueError:
            limit = 25
        with AUDIT_LOCK:
            records = list(AUDIT_LOG)[-limit:]
        return jsonify({"status": "success", "count": len(records),
                        "records": records, "timestamp": _now()})
    except Exception as e:
        return _error(e, 500)


@supervisor_bp.route("/audit", methods=["POST"])
def supervisor_audit():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("JSON body required")
        active_window = str(data.get("active_window", "") or "")
        clipboard = str(data.get("clipboard_snippet", "") or "")
        keywords = data.get("screen_keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        user = str(data.get("user", "") or "")
        app = str(data.get("app", "") or "")
        use_llm = bool(data.get("llm_triage", True))

        heuristic = _heuristic_triage(active_window, clipboard, keywords)
        llm = (_llm_triage(data) if use_llm
               else {"verdict": "SKIPPED", "category": "general",
                     "reason": "llm_triage=false", "model": OLLAMA_MODEL})

        final = "NO_ACTION"
        if (heuristic["verdict"] == "SUPERVISOR_ALERT"
                or llm.get("verdict") == "SUPERVISOR_ALERT"):
            final = "SUPERVISOR_ALERT"
        elif (llm.get("verdict") == "LOG_ONLY"
                or heuristic["verdict"] == "LOG_ONLY"):
            final = "LOG_ONLY"

        category = heuristic.get("category", "general")
        if final == "SUPERVISOR_ALERT" and llm.get("verdict") == "SUPERVISOR_ALERT":
            category = llm.get("category") or category

        record = {
            "verdict": final,
            "category": category,
            "active_window": active_window[:200],
            "app": app[:80],
            "screen_keywords": [str(k)[:60] for k in keywords[:20]],
            "clipboard_snippet": clipboard[:300],
            "triage": {"heuristic": heuristic, "llm": llm},
            "vault_persisted": False,
            "timestamp": _now(),
        }

        if final == "SUPERVISOR_ALERT":
            try:
                vault = get_memory_vault()
                content = (
                    f"**Verdict:** {final}\n**Category:** {category}\n"
                    f"**Active window:** {active_window[:200]}\n"
                    f"**Application:** {app[:80]}\n"
                    f"**Screen keywords:** {', '.join(str(k) for k in keywords[:20])}\n"
                    f"**Clipboard:** {clipboard[:300]}\n"
                    f"**Heuristic:** {heuristic.get('reason', '')}\n"
                    f"**LLM ({llm.get('model', '')}):** {llm.get('reason', '')}\n"
                )
                doc = vault.create_document(
                    category="journal",
                    title=f"Supervisor Alert — {category}",
                    content=content,
                    tags=["supervisor", "alert", str(category)],
                    user=user,
                    source="supervisor_audit",
                )
                record["vault_persisted"] = True
                record["vault_path"] = doc.get("path", "")
            except Exception as e:
                record["vault_persisted"] = False
                record["vault_error"] = str(e)

        with AUDIT_LOCK:
            AUDIT_LOG.append(record)

        return jsonify({"status": "success", "verdict": final,
                        "category": category,
                        "vault_persisted": record["vault_persisted"],
                        "triage": record["triage"],
                        "timestamp": record["timestamp"]})
    except Exception as e:
        return _error(e, 500)