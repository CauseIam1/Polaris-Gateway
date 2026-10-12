# Polaris — Current System Reference

## Single source of truth for the Polaris AI stack as it currently sits: containers, services, APIs, subsystems, and deployment facts. Scope: `freeroam/polaris-gateway` (gateway) and `freeroam/polaris-dashboard` (dashboard). 
---


## 1. Overview

### Polaris is the private AI assistant and system hub running on the Dell Mainframe host (`192.168.50.51`, LAN-only). Two primary Docker containers plus supporting infrastructure:

| Component | Container | Role |
|---|---|---|
| **Polaris Gateway** | `polaris-gateway` | One container, three Flask services: Chat Gateway (AI chat, tool calling, memory, desktop-vision turns), Burst Receiver (telemetry, motion-radar, vision stream), Sandbox Gateway (X17 SSH console) |
| **Polaris Dashboard** | `polaris-dashboard` | Static nginx page (`Polaris_page.html`) serving the Holodeck split-view chat UI |
| Ollama | `ollama` | Local LLM inference — `polaris-ai:latest` (chat), `nomic-embed-text` (embeddings) |
| Gotify | `gotify` | Push notification delivery (consumed by the AI as a tool) |
| Polaris Playground | `polaris-playground` | nginx serving AI-generated web apps from `playground/www` on :8090 |
| LiveCharts | `livecharts` | XRPL asset monitoring page (nginx) on :7001 |
| WireGuard | `freeroam-wireguard` | Secure tunnel for the FreeRoam Android client (UDP 51820) |

### **Operator identity** resolves from the client IP first (`X-Forwarded-For` first hop, else `remote_addr`); an explicit `user_id` (REST JSON body / GET query string — the dashboard sends `rich`/`matt` on the user's behalf) can then override it, trust-gated by `resolve_config_with_override()`: the caller must first pass the standard IP gate (exact `USER_CONFIG` match or an allowed Docker-bridge prefix) and the requested identity must match a configured user name, otherwise the IP-derived config stands — honored overrides log `[Chat]` / `[Chat History] Identity override: user_id='…' from IP …`. `[WebSocket]` sessions resolve identically via `_ws_config_for()` — IP-derived config first, then this socket's `'identity'` announce (`WS_IDENTITIES`; the live Chat Gateway is `polaris_continuous_learning_gateway.py` under gunicorn+gevent, and `polaris_websocket_gateway.py` in the repo is legacy/unused). Known IPs: `RICH_IP = 192.168.50.42` (Rich), `MATT_IP = 192.168.50.98` (Matt), `OPERATOR_IP = 192.168.50.51` (the Mainframe itself — persona "Operator", operator data files), `DASHBOARD_TEST_IP = 192.168.50.227` (also the X17 / LGI host). WireGuard tunnel aliases (WG-primary restore, 2026-10-03 — live-verified: fresh re-handshake over home-WiFi hairpin, 0%-loss tunnel ping, `[Chat History] Identity override: user_id='rich'` heartbeats): `RICH_WG_TUNNEL_IP = 10.20.30.3` (Rich's phone inside the `freeroam-wireguard` tunnel) → Rich, `MATT_WG_TUNNEL_IP = 10.20.30.2` (Matt's phone) → Matt — direct `USER_CONFIG` entries in Burst Receiver + Chat Gateway — and `DOCKER_HAIRPIN_IP = 172.18.0.1` (the docker-proxy source the tunnel's MASQUERADE'd traffic arrives as after hairpinning the published ports; the tunnel IPs `10.20.30.x` themselves never reach the gateway directly) → **Rich**, the WG-primary hairpin catch-all; likewise `192.168.50.1` — the router-LAN SNAT source when a device's WAN-bound traffic exits via the router with WG toggled off at home — resolves to **Rich** (2026-10-03 pin in both gateways). The announce path is unchanged: WS sessions resolve IP-derived first, then an honored `identity` announce / `user_id` override can switch the config (same trust gate). One client-side nuance remains: a WiFi-less device defaults its identity to `Rich` in the app itself (unknown-device fallback per freeroam.md), so Matt's phone announcing from the field may still land in Rich's bucket via the honored announce — but every IP-derived path (bursts, REST without `user_id`) is now unambiguous. (The historical MissPi→Rich alias ended when MissPi was decommissioned on Sep 30, 2026.) Traffic from other Docker bridge ranges (`172.17.*`–`172.20.*` — NAT-masked, no explicit `USER_CONFIG` entry, e.g. `172.17.0.1`) falls back to `DEFAULT_USER_CONFIG` (persona "Dashboard", operator data files). Identity selects the user name, profile/behavior/lists/transcript paths, session partitioning, and chat history — chat-history keys additionally funnel through `normalize_chat_identity()` (`SHARED_IDENTITY_ALIASES`: `dashboard` → `rich`, `operator` → `rich`), so LGI voice, the dashboard browser, and any Docker-bridge client share one `rich` bucket (§3.4); `matt` stays partitioned.

### **Edge device:** the X17 (Windows 11) at `192.168.50.227`, allowlisted as `DASHBOARD_TEST_IP`, user `rbuit`, LGI workdir `D:/LGI`. Runs the LGI v1.2 "Her Seeing Me" stack (device `x17-webcam`): it provides the `radar_motion` + `vision_frame` feeds into the Burst Receiver (socketio — the IP-ungated path), and its desktop-vision chat turns (screenshot + OCR on `POST /api/chat`) are answered by the Chat Gateway's vision LLM ladder (§3.1). Teachable hands (LGI v1.6.0, Oct 2026): voice lessons (*"Polaris, teach a gesture"* → hold a pose ~3 s → speak its meaning, plus *"forget gesture <name>"* / *"list gestures"*) persist position/scale-invariant landmark profiles in the device-local `D:\LGI\taught_gestures.json` (`gesture_teaching.py::GestureStore` — atomic `.tmp`+replace write, corrupt file degrades to empty + log), and the same webcam Hands pass then matches live hands at runtime — gated by `LGI_TAUGHT_GESTURES` (auto-inactive until the first profile exists; `0` hard-disables independent of `LGI_GESTURES`), skipped entirely during lessons, per-profile thresholds/cooldowns, one fire per match — firing `taught:<name>:<action>` commands and logging `[perception] taught gesture:`; lessons run wake-free (the utterance hook owns the mic while live) and trace `[teach]` lines. Recognition is device-local: the gateway still receives only the unchanged `radar_motion` + `vision_frame` feeds — nothing new rides the wire. It is also the SSH target for every remote-execution tool (user `rbuit`, cmd.exe default shell — Windows command guidance is baked into the tool schemas and the system prompt). **MissPi / Mini Pi (Raspberry Pi 5) at `192.168.50.179` was fully decommissioned on Sep 30, 2026:** its `ALLOWED_IPS` / `USER_CONFIG` entries were removed from both gateways and from the `ssh_tools.py` defaults; its old storage (`/data/freeroam/misspi/`) is preserved untouched for history.

### 1.1 The North Star team — one intelligence, three stations

## Polaris is ONE intelligence distributed across three stations, with the operator as final authority: this gateway is the **brain and hands** (persona + dual-tier memory + the `execute_tool_call` dispatcher + task tracking; repo eyes via the v1.4.2 read-only workspace pair); the X17 running LGI is her **senses and voice** (webcam/desktop vision in, Kokoro TTS + Faster-Whisper STT out, floating HUD; teachable hands since v1.6.0 — taught-gesture lessons/recognition on-device, profiles in `D:\LGI\taught_gestures.json`; the device tag `lgi-hud-x17` origin-gates spoken `chat_tool_report` speech to LGI only); FreeRoam on the phone is her **field presence** (on-device STT/TTS walkie-talkie over WireGuard — text-only over the tunnel). Repo authority is absolute: only the operator and **Cline** ever edit `/home/causeiam/docker-containers` — Polaris reads it read-only (`/data/workspace:ro`) and writes only her ingest vault (`write_doc`). Charter: the top team section of `northstar.md`; embodiment contract: `LGI/LGI.md` §7.

---

## 2. Port Map

| Host Port | Container Port | Service | Purpose |
|---|---|---|---|
| 8082 | 5000 | Chat Gateway | AI chat (strictly text-only), memory, tool calling, task tracking, Socket.IO |
| 8083 | 5001 | Burst Receiver | Burst/telemetry ingestion, CSI radar, vision stream, Socket.IO |
| 7007 | 7007 | Sandbox Gateway | X17 (LGI) SSH web console |
| 7000 | 80 | Polaris Dashboard | Holodeck UI (nginx) |
| 8090 | 80 | Polaris Playground | AI-generated web app server |
| 7001 | 80 | LiveCharts | XRPL asset monitor |
| 8081 | 80 | Gotify | Push notification server |
| 11434 | 11434 | Ollama | LLM inference + embeddings |

All services live on the external Docker network `docker-containers_default`.

---

## 3. Chat Gateway (host 8082 → container 5000)

**File:** `polaris_continuous_learning_gateway.py` — Flask + Flask-SocketIO app powered by Ollama, with tool calling, dual-tier memory, session management, and heuristics. The gateway is strictly text-only - no voice pipeline exists on the mainframe. Blueprints registered: `memory_bp` (Memory Vault), `supervisor_bp` (Supervisor Audit).

**Production WSGI server (gunicorn + gevent).** Served by `gunicorn -k gunicorn_gevent_ws.GeventWebSocketWorker -w 1` from `run_gateway.sh` (a tiny local worker subclass that serves `gevent.pywsgi` with `WebSocketHandler` — the stock `gevent` worker lacks `wsgi.websocket` in the environ, which makes every engineio websocket upgrade fail with "The gevent-websocket server is not configured appropriately"; werkzeug threading mode remains the direct-run fallback). `SocketIO` picks `async_mode='gevent'` when running under gunicorn (clean WS session close — no werkzeug 500-spam artifacts) and `threading` otherwise; a failed gevent init falls back to threading. Single worker by design — Flask-SocketIO multi-worker requires a message queue (Redis). Boot-time systems (Memory Vault watchdog, node-cleanup loop) start via `_start_background_systems()`, invoked on import under gunicorn and from `__main__` otherwise. Knobs: `GUNICORN_TIMEOUT` (default 120 s); `--access-logfile -` keeps per-request lines greppable in `docker logs`.

### 3.1 REST API

**Chat & History** — one shared server-side store (`dashboard`/`operator` aliased into `rich` by `normalize_chat_identity()`), trust-gated explicit `user_id` override on every endpoint (see §3.4)
| Method | Route | Purpose |
|---|---|---|
| POST | `/api/chat` | Main chat endpoint (LLM + memory context + tool calling); persists both turns to chat history, echoes `chat_message` to the sender's IP room, and emits `tool_execution` when tools run (§3.2 tool pipeline). With `image_base64` it becomes a desktop-vision turn (§3.1 note below); an explicit `user_id` (trust-gated) overrides the IP-derived identity before both turns are persisted |
| GET | `/api/history/<user_id>` | Paired user↔Polaris history for a user, dashboard format (identity by path, not IP — the dashboard's 3 s cross-device sync poll) |
| GET | `/api/chat/history` | Full history → `{status, user, messages}` (the phone's sync read); optional `?user_id=<name>` trust-gated override — `user` echoes the caller's resolved display name (e.g. `Rich` vs `Dashboard`) even when both callers share the same bucket |
| POST | `/api/chat/history` | Replace the IP-authenticated user's full history (`{"messages": [...]}`); optional body `user_id` (same trust policy) |
| DELETE | `/api/chat/history` | Clear the IP-authenticated user's history; optional body `user_id` (same trust policy) |
| POST | `/api/chat/history/sync` | Append one client-supplied message object (`{"message": {...}}`) to the IP-authenticated user's history; optional body `user_id` (same trust policy) |

**`POST /api/chat` HTTP return — raw envelope, by design.** The handler ends with `return jsonify(response_data)`, passing the raw Ollama envelope verbatim (unstripped `response` + `thinking`). It is a debug/API surface only — **no rendering path consumes it**: the dashboard's fetch handler discards the HTTP body (§11), the Android app renders only from `GET /api/chat/history`, and every persisted/broadcast surface (transcript, session, chat history, socketio `chat_message`) carries only the stripped prose (§3.2 pipeline).

**Desktop-vision turns (Polaris Desktop Vision, LGI v1.2).** A `POST /api/chat` request carrying `image_base64` (desktop screenshot JPEG) — optionally `ocr_text` (≤4000 chars) and `active_window` — bypasses the tool pipeline and is answered by the vision LLM ladder: `VISION_LLM_MODEL` (`polaris-ai:latest`) first, then `VISION_LLM_FALLBACK_MODEL` (`mistral-large-3:675b-cloud`) on any failure — degrade, never fabricate. The image rides Ollama's multimodal `images` field (`OLLAMA_URL`, default `http://ollama:11434/api/generate` via Docker DNS) with a desktop-context prompt built from the active-window title and OCR text; timeout is 120 s (`VISION_LLM_TIMEOUT`, hardcoded). Total ladder failure raises into the standard 503 error path. The reply envelope adds `vision: true` + `vision_model` (raw-HTTP surface only — clients render via `chat_message` as usual).

**Voice - retired (text-only mainframe directive, Oct 2026).** The gateway performs zero audio processing: no models, no STT/TTS code, no audio libraries. The five voice routes (`/api/tts`, `/api/tts-stream`, `/api/tts/cancel`, `/api/voice-command`, `/api/voice-health`) were deleted MissPi-style; requests now 404. Kokoro TTS + Faster-Whisper STT run locally on the X17 (LGI `audio_tts.py` / `audio_stt.py`; LGI HUD chat is text-only with the gateway) and fully on-device in the FreeRoam Android app. The dashboard AUDIO RESPONSE checkbox defaults off.

**Status & Tasks**
| Method | Route | Purpose |
|---|---|---|
| GET | `/api/status` | Overall system status |
| GET | `/api/status/tasks` | All tracked tasks |
| GET | `/api/status/tasks/active` | Active tasks |
| GET | `/api/status/tasks/completed` | Completed tasks |
| GET | `/status` | Status alias |

**Session & Heuristics**
| Method | Route | Purpose |
|---|---|---|
| GET | `/api/session` / `/api/session/status` / `/api/session/topics` | Session window, status, detected topics |
| POST | `/api/session/clear` / `/api/session/reset` | Reset session state |
| GET/POST | `/api/heuristics` | List / create behavior heuristics |
| GET | `/api/heuristics/staged` | Staged (unapproved) rules |
| POST | `/api/heuristics/approve/<rule_id>` | Approve a rule |
| DELETE | `/api/heuristics/reject/<rule_id>` | Reject a rule |
| PUT | `/api/heuristics/edit/<rule_id>` | Edit a rule |
| GET | `/api/behavior` | Behavior adaptation state |

**Memory, Users & Lists**
| Method | Route | Purpose |
|---|---|---|
| GET | `/api/memory/stats` | SQLite + ChromaDB tier stats |
| POST | `/api/vector/search` | Semantic vector search |
| GET | `/api/whoami` / `/api/users` | IP identity / registered users |
| GET/POST | `/api/profile` | User profile read/write |
| GET | `/api/transcripts` | Daily transcripts |
| GET/POST | `/api/lists`, `/api/lists/<name>` (+`/view`, `/add`, `/remove`) | Persistent list store |

**Distillation, Nodes, Supervisor**
| Method | Route | Purpose |
|---|---|---|
| POST | `/api/distill` / `/api/distill/auto` | Run / schedule memory distillation |
| POST | `/api/sync/daily-transcript` | Ingest daily transcript |
| POST | `/api/nodes/register`, `/api/nodes/heartbeat`; GET `/api/nodes` | Edge-node registry + heartbeats |
| GET | `/api/supervisor/status`, `/api/supervisor/audit/recent`; POST `/api/supervisor/audit` | Supervisor audit (screen/clipboard triage) |

### 3.2 Socket.IO Protocol (port 8082)

**Client → Server events**
| Event | Behavior |
|---|---|
| `connect` | Server resolves operator identity from client IP, joins the client into a per-IP room, emits a welcome `message` event |
| `message` | Chat / query messages from the dashboard **and the phone** (type-based routing); reply streams back as `chat_stream` chunks with a 48-char hold-back buffer, completed turns echo via `chat_message`, and **both turns are persisted** to chat history. Tool calls embedded in the reply are handled **server-side** (tool pipeline below) — raw tool JSON never reaches the client |
| `join_status_room` | Joins room `status`; server immediately emits current `active_tasks` |
| `join_tasks` | Dashboard TASK FEED's join — puts the feed socket into room `status` (the `tasks_dashboard` room name in the payload is ignored) and emits an `active_tasks` snapshot; joins regardless of operator config |
| `leave_status_room` | Leaves room `status` |

There is **no** `request_history` handler and no `chat_history` emit — history sync is REST-poll based (§3.4).

**Server → Client broadcasts** (to room `status`)
- `task_registered`, `task_started`, `task_progress`, `task_completed`, `task_failed` — real-time task lifecycle events (uploads, SSH commands, distillation, container scans, etc.)

**Server → Client broadcasts** (to the sender's per-IP room — live chat delivery)
- `chat_message` — instant echo of the user's own prompt (the phone deliberately skips adding sent messages locally to avoid duplicates) and of the completed Polaris reply the moment the stream ends, so the phone renders it without waiting for the 3 s history poll; payloads carry both `content` and `message` keys — Android's `handleChatMessage()` reads `message`, REST payloads use `content`
- `message` (type `chat_stream`) — streaming reply chunks (`stream_id` / `is_complete`); the same event also carries connection/welcome, `error`, and `active_tasks` payloads
- `tool_execution` — emitted the moment server-side tool execution finishes (payload: `tool_calls`, `results`, `timestamp`); fired by **both** REST `/api/chat` and the WS `message` handler

**Tool pipeline (server-side — identical on REST `/api/chat` and the WS `message` handler)**
1. The Ollama stream is scanned for tool-call markers; the WS stream keeps the last **48 chars** un-emitted (`STREAM_HOLD_BACK`) so a marker split across chunks can never leak mid-stream
2. On a tool marker (`{"tool":` …), the stream **freezes** — only the prose preceding it is emitted (dangling code fences trimmed)
3. After the stream ends: parse (`parse_tool_calls_from_response`) → strip (`strip_tool_calls_from_response`) → execute each call via `execute_tool_call()`; every call logs a `[TOOL]` transcript line
4. The stripper closes three leak vectors — **text cleanup only**, parser/execution semantics untouched:
   - **Malformed tool JSON:** a balanced `{"tool": …}` span `_safe_parse_tool_json` cannot repair is still stripped *if it names a known tool* (union of every schema registry + the legacy inline names, via `_known_tool_names()`); a `[DEBUG] Stripped malformed tool JSON…` line preserves the evidence in `docker logs`. JSON naming no known tool (e.g. the user's own pasted JSON echoed back) stays visible
   - **Legacy inline calls** (`read_doc(filename="…")`, `run_sandbox_code(code="…")`, …): executed by the parser but previously left in the bubble — stripped with the same name/argument shapes the parser matches
   - **Empty `json` fences** left behind after inner-JSON removal, plus triple-newline collapse
5. `tool_execution` carries the actual results; only the **cleaned prose** is flushed to the stream, fed to the session window, and — on tool-less turns (v1.6.2, one-reply-per-turn) — persisted and echoed via `chat_message`: on tool turns the narration never becomes a master-history row or a `chat_message`, the truthful report is the turn's only Polaris record/broadcast on every surface — raw tool JSON never pollutes history, memory, or future-turn context
6. When tools ran, a **truthful tool-report follow-up turn** is generated: a second LLM pass over the real results (large fields truncated to 1 500 chars by `_truncate_tool_results()`; document `content` pages carry their own 8 000-char budget — `MAX_READ_PAGE_CHARS`, matching `read_doc`'s hard page cap), reporting-only instructions (never claim success on a failure result, plain text, no further tool calls); the report turn itself is stripped so it can never recurse. Fallback `generate_follow_up_message()` is result-aware — outcomes are reported exactly as they happened, never fabricated

### 3.3 Configuration (environment)

| Variable | Value | Purpose |
|---|---|---|
| `GATEWAY_PORT` | `5000` | Flask listen port |
| `OLLAMAHOST` | `172.17.0.1:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `polaris-ai:latest` | Chat model |
| `DATA_DIR` | `/app/data` | Distillation DB + user data |
| `USER_DATA_DIR` | `/data/freeroam` | Bursts, telemetry, profiles, chat history |
| `LIVECHARTS_DIR` | `/livecharts` | LiveCharts mount |
| `WWW_DIR` | `/playground/www` | Playground web root |
| `GOTIFY_*` | see §10 | Notification configuration |

### 3.4 Cross-Device Chat History

One shared server-side store: every read/write path funnels through `normalize_chat_identity()` (`SHARED_IDENTITY_ALIASES`: `dashboard` → `rich`, `operator` → `rich`), applied at the request handlers *and* inside `append_chat_history()` at the storage layer, so LGI voice, the dashboard browser, and any Docker-bridge client converge on one `rich` bucket; `matt` remains partitioned. Legacy `dashboard`/`operator` keys still present in `chat_history.json` are orphaned — no handler or append path can reach them (left archived on disk).

**Storage**
- In-memory dict `CHAT_HISTORY` keyed by (normalised) identity, guarded by `CHAT_HISTORY_LOCK`, capped at **200 messages per bucket** (rolling `[-200:]` trim after each append).
- Persisted as JSON to `/data/freeroam/chat_history.json` (host bind `/mnt/containers/freeroam/data/chat_history.json`); loaded on startup; saves run on a daemon thread outside the lock.
- **One-boot leak scrub** — `scrub_chat_history_leaks()` runs from both entry points (WSGI import + `__main__`, deliberately not from `load_chat_history_from_disk()`): `strip_tool_calls_from_response()` over every stored entry in every bucket, then a one-time sidecar backup `chat_history.json.pre-scrub` (written only if absent, while the on-disk file is still pre-scrub) and an async re-persist; logs `[Chat History] Startup scrub: N leaked entries cleaned` (`0` on an already-clean file). Guards against raw tool-call JSON that predates the unconditional stripper (§3.2).
- Record shape: `{ "role": "user"|"assistant", "content", "timestamp", "sender" }`.

**Boot-time window hydration (Oct 4, 2026)** — `_start_background_systems()` calls `hydrate_sessions_from_history()` before any traffic: it reloads `/data/freeroam/chat_history.json` and replays the newest `SLIDING_WINDOW_SIZE` (**20** — raised from 10, which was losing recent context across restarts even while it sat on disk) turns of every identity bucket into that session's sliding window via the canonical `add_exchange()`. Idempotent by design: identities already carrying window content are skipped, and only `user`/`assistant`/`system` records with non-empty string content replay. Nothing is written back to history, so the startup scrub cannot re-run on restored content. Log line: `[Session Hydration] restored N turns into windows (20-exchange cap per identity)`. When the window renders into the system prompt, each exchange is capped at `MAX_EXCHANGE_RENDER_CHARS` = **400** chars (raised from 200) to keep the block readable.

**Timestamps — Z-suffixed UTC, non-negotiable.** `utc_now_iso()` emits ISO-8601 UTC with a trailing `Z` (e.g. `2026-09-25T01:23:45.678Z`). Android's `Instant.parse()` rejects the legacy formats (`%Y-%m-%d %H:%M:%S`, naive `.isoformat()`); parse failures collapse to `now()`, which corrupted ordering and made the phone's sync loop wholesale-replace its window with stale history. All records must use `utc_now_iso()`.

**Both turns always persisted** — every exchange appends the user prompt *and* the Polaris reply, on both transports:
- REST `POST /api/chat`
- WS `message` handler (previously appended only Polaris's reply, so the user side of WS chats never reached other devices)
- When the turn executed tools, the truthful tool-report follow-up is that turn's **only assistant record** on both transports (transcript line `Polaris (tool report)` — see §3.2 tool pipeline). The pre-tool narration stays in the session window, the machine transcript, and the HTTP body/stream typing UX, but is never appended as a master-history row nor broadcast as `chat_message` (v1.6.2: appending both rendered every tool turn as TWO Polaris messages — a paired narration row + a standalone report row — doubling ⌁ LAST EXCHANGES on the LGI HUD, dashboard bubbles, and the phone's sync window)

**Delivery**
- **Live, on the sending device:** `chat_message` to the sender's per-IP room (§3.2) — the user's own prompt echo and the completed Polaris reply the instant the stream ends; the reply body streams as `chat_stream` chunks.
- **Cross-device, ≤3 s:** REST polling. The phone polls `GET /api/chat/history` (IP-authenticated, optional `user_id` override) on connect and every 3000 ms (`CHAT_SYNC_INTERVAL_MS`), rebuilding its rolling **10-message** window (`MAX_CHAT_MESSAGES`) whenever the server's newest timestamp beats the local newest, and auto-speaking new Polaris replies (1 s dedupe gate). The dashboard polls `GET /api/history/<user_id>` every 3 s.

**Vestigial socket events:** the phone emits `request_history` and listens for `chat_history` over Socket.IO; the gateway implements neither. The REST poll loop is the sync path — the WS pair is dead code on the wire.

### 3.5 System Prompt & Persona (North Star primacy)

`build_system_prompt()` assembles the per-request system prompt. Subject-matter precedence is fixed: **Project North Star is Polaris's primary domain and the default subject of any ambiguous request** — the X17 (LGI), Gotify, and the Memory Vault are supporting infrastructure, routed to only when explicitly asked or clearly relevant.

Prompt blocks, in order:
- Persona header — witty, bubbly AI companion for `{user_name}`
- **[PROJECT BLUEPRINTS - LIVE DIGEST OF YOUR AUTHORITATIVE DOCS]** — `{project_digest_block}`: a compact, bounded heading map (`◆ <doc>:` lines, ≤ `DOC_DIGEST_MAX_CHARS` = 1 100 chars per doc) of **northstar.md**, **freeroam/Polaris-gateway.md**, and **LGI/LGI.md**, plus a ground-first directive — open the original (`read_doc` for the two ingest-vault mirrors, `read_workspace_file("LGI/LGI.md")` for the live repo copy) before answering anything touching the North Star ecosystem. `_extract_doc_outline()` renders headings plus one short lead line per H1/H2; `get_project_digest_block()` resolves each source through `POLARIS_DOC_SOURCES` (ingest-vault copy first for the two mirrored docs, the live `/data/workspace` mount first for `LGI/LGI.md`) and caches by `(path, mtime_ns, size)` in `_BLUEPRINT_DIGEST_CACHE`, so content is stable across turns and refreshes the moment a source file is edited. Returns `""` — the block vanishes gracefully — when no source is readable, or on unexpected failure (`[Blueprint Digest]` log).
- **[PROJECT NORTH STAR - YOUR PRIMARY ECOSYSTEM]** — Trading Matrix (operator-owned AMM mesh, 0.05% fee, multi-hop arbitrage loops, LP-fee recycling), Zero-Fiat Rule (stablecoins are transient pass-through settlement nodes only; capital retained in XRP + whitelisted meme coins), Wallet Topology (COLD_WALLET / BOT MPT_RPN hot wallet / TRADING_WALLET), Stack (Java engines, QuestDB :8812 with the epoch / `java.sql.Timestamp` binding rule, local rippled `http://rippled:5005`, North Star Holodeck dashboard), SCOPE (the primacy statement above)
- [REMOTE INFRASTRUCTURE - X17 (LGI)] — SSH facts (user `rbuit`, LGI workdir `D:/LGI`, Windows cmd.exe guidance, `ssh_deploy_file`/`ssh_fetch_file` vault deployment)
- [SSH REACHABILITY DOCTRINE] — ON/OFF answers, not failures; connection failures map to exact phrasings (OFF / ON but SSH down / auth failure / ON and responding)
- [REMOTE PROCESS MANAGEMENT RULES] — the `[b]racket` pgrep/pkill trick, nohup + redirect daemon starts, PID-stability proof, JSON-safe command strings, and HONEST REPORTING (never claim an outcome in the same response that performs the action)
- Tool schemas + **[TOOL USAGE EXAMPLES]** — 11 few-shot pairs covering the full tool surface, all North Star / trading-infrastructure flavored (XRP dashboard publishing, playground listing, sandbox code, trading notes, X17 uptime/connectivity, arbitrage journaling, trading-bot + XRP-price Gotify alerts, QuestDB-lessons vault query, operator voice-journal logging) + [TOOL EXECUTION RULE] (the JSON tool-call contract)
- [PUSH NOTIFICATIONS - GOTIFY] · [MEMORY VAULT - YOUR LONG-TERM MEMORY] (read `memory_vault_query` · v1.5 append-only voice journal `memory_journal_write` → `04-Journal/YYYY-MM-DD.md` — the only chat-side vault write; all other writes stay with the learning process + operator)
- [KNOWN FACTS ABOUT {USER}] — memory context + behavior lib + heuristics + session window + tools block · CORE DIRECTIVES (vibe; TTS-ready, no emojis; ≤3 sentences unless asked; personalize; follow heuristics; use recent context)

**Context guardrail:** the prompt renders once; if it exceeds `SYSTEM_PROMPT_TOKEN_GUARD` (**9000** est. tokens, `chars ÷ 4` — raised from 6500 on Oct 4, 2026 to fund the 20-exchange window plus the blueprint digest), the conversation window shrinks stepwise (`WINDOW_SHRINK_STEPS = [12, 6, 3]` exchanges) and re-renders so the persona, KNOWN FACTS, and directives survive — only the oldest exchanges are sacrificed, and **3 is the hard floor: the window is never collapsed to zero** (`[GUARDRAIL]` log line). The headroom is deliberate under the model's 32768 `num_ctx` (v3.4, raised from 8192): it funds the widened 20-exchange window and the blueprint digest while still leaving room for tool-result pages (up to `MAX_READ_PAGE_CHARS` = 8 000 chars) plus the user turn, reply, and thinking.

**Modelfile mirror:** the Ollama-side base model carries the same persona — `freeroam/polaris-gateway/Modelfile` defines `polaris-ai:latest` as `FROM glm-5.3-flash:cloud` (temperature 0.7, `num_ctx 32768`) with the North Star primacy block in its SYSTEM text. Modelfile edits are applied with `ollama create polaris-ai:latest -f <Modelfile>` — an instant manifest swap on the running ollama container, no restart.

---

## 4. Burst Receiver (host 8083 → container 5001)

**File:** `burst_receiver.py` — high-throughput telemetry/burst ingestion, CSI radar spatial tracking, the vision stream (X17 webcam YOLO detections + narration), and Ollama image-analysis endpoints.

### REST API
| Method | Route | Purpose |
|---|---|---|
| POST | `/api/burst` | Submit burst message |
| POST | `/api/telemetry` | Submit telemetry data |
| GET | `/api/burst/list`, `/api/burst/<burst_id>` | List / fetch stored bursts |
| GET | `/api/telemetry/recent` | Recent telemetry |
| POST | `/api/radar/ingest` | Submit CSI vector for spatial processing |
| POST | `/api/vision/analyze` | Analyze an image (`image_path`, or base64 `image_data` + `filename`) with the Ollama vision model — prompt types: general / tactical / ocr / sharpness_check |
| GET | `/api/vision/analyze-burst/<burst_id>`, `/api/vision/models`, `/api/vision/test`, `/api/vision/stream/status` | Burst image analysis (cached `vision_analysis.json` or async trigger), vision-capable model list, pipeline test, honest live stream status (`stream_active` keyed to the camera's real state, `device` = last genuine announce, `status` = active/waiting, `jarvis_announcements`) |
| GET | `/api/jarvis/announcements` | Announcement feed |
| GET | `/api/status` | Receiver status |

### CSI Radar Processing (`POST /api/radar/ingest`)
Payload: `{ "csi_vector": [...], "timestamp"?, "device_id"?, "metadata"? }`

1. First vector initializes the weighted subcarrier array
2. X/Y coordinates from split subcarrier weights (left/right halves)
3. Z coordinate from amplitude decay/growth against baseline
4. Motion detection — velocity + confidence from position deltas
5. Broadcast `radar_update` to room `radar_dashboard`

### Socket.IO
| Event | Direction | Behavior |
|---|---|---|
| `join_radar` / `leave_radar` | client → server | Join/leave room `radar_dashboard` (default room) |
| `request_radar_snapshot` | client → server | Server responds with trajectory snapshot |
| `radar_update` | broadcast | Emitted on every CSI ingest (room `radar_dashboard`) |
| `radar_status`, `radar_snapshot`, `radar_history` | per-requester | Status / snapshot / history replies |
| `vision_connect` | client → server | CAMERA-ONLY announce (the LG app's handshake; dashboard viewers only `join_vision` — they are never cameras). Registers `vision_device_name` + `vision_camera_sid`, flags the stream active, lazily arms the staleness watchdog, replies `vision_ack {status, stream_id, device}`, emits the typed `jarvis_announcement` (`connection`) into `vision_dashboard` and a `status` awareness note into `radar_dashboard` |
| `vision_frame` | client → server | Live frame from the LGI YOLO daemon (detections + narration) → adopts that socket as the current camera, re-arms `vision_stream_active` if a stale sweep or missed disconnect had cleared it, buffers into `vision_detections` (last 100), broadcast as `vision_update` (room `vision_dashboard`); narrations over 0.7 confidence add a `detection` announcement (≥ 5 s dedupe) |
| `vision_disconnect` | client → server | Camera ends its stream — flags off + typed `disconnection` announcement |
| `radar_motion` | client → server | Pre-processed motion event (timestamp / confidence / intensity / duration). Historical source: Miss Pi CSI radar daemon (decommissioned Sep 30, 2026). Current source: X17 webcam perception (LGI SpatialReporter @ 1 Hz, `device_id: x17-webcam`) sending explicit `x` / `y` / `z` / `velocity` — real coordinates pass through untouched into the `radar_update` broadcast (`people_count` also rides the payload but is not currently forwarded); coordinate-less legacy payloads keep the intensity-driven simulation |
| `join_vision` | client → server | Join room `vision_dashboard`; server replies with `vision_history` — last 10 detections plus the camera's REAL `stream_active` state and `last_update` |
| `disconnect` (socket death) | server-side | If the dying socket is the tracked camera (`vision_camera_sid`), the stream is marked offline via the shared offline path (see below) — a raw transport drop or killed app can no longer ghost the flag; viewer-socket deaths never touch camera state |

### Vision stream honesty (camera-truth state)

`vision_stream_active` is only ever moved by camera truth — never by viewer traffic:

- **State:** `vision_camera_sid` = socket of the last genuine camera (set by `vision_connect`, re-adopted by every `vision_frame`, so an LG reconnect that skips its announce still owns the flag); `vision_device_name` = device from the last genuine announce; `last_vision_update` = last frame time; `vision_detections` = last 100 frames; `jarvis_announcements` = last 5.
- **Single offline path — `announce_vision_offline(reason)`:** every way the feed can end routes through it (explicit `vision_disconnect`, camera socket death, stale watchdog): flag off, typed `disconnection` announcement to `vision_dashboard`, `warning` awareness note to `radar_dashboard`. The device name is preserved — it stays the last genuine announce until the camera announces again, so a reconnect that runs on frames only reports `device: Unknown` by design until the LG app's next `vision_connect`.
- **Stale-feed watchdog:** lazily started on the first camera announce; 5 s tick — if the stream is flagged active but no `vision_frame` arrived for **20 s** (frame cadence is ~2.5 s), the feed is marked offline once; frames resuming re-arm the flag and dashboards recover with the next update. Catches the LG app keeping its socket open while silently dropping frames.
- **Typed announcements:** `connection` (genuine camera announces) / `disconnection` (any offline path) / `detection` (high-confidence narration, 5 s dedupe) / `status` + `warning` (awareness notes to the radar room) — dashboard status pills key on the typed `connection`/`disconnection` events, never on viewer socket state.

### Configuration

All values are hardcoded constants in `burst_receiver.py` — the service reads no environment variables.

| Constant | Value | Purpose |
|---|---|---|
| `ALLOWED_IPS` | `192.168.50.42` / `.98` / `.51` / `.227` + `10.20.30.3` / `10.20.30.2` / `172.18.0.1` / `192.168.50.1` | LAN-only REST guard (Rich / Matt / Operator / X17-LGI) + the WireGuard tunnel IPs (Rich `10.20.30.3` / Matt `10.20.30.2`) and the docker-proxy hairpin IP `172.18.0.1` (→ Rich, the 2026-10-03 WG-primary restore). Loopback and other Docker-bridge source IPs are denied — `docker exec` curls get 403 by design (§13); host-shell curls keep the host IP (source-preserved DNAT path, = `HOST_IP`); WireGuard tunnel traffic arrives as `172.18.0.1` (docker-proxy hairpin) |
| `USER_CONFIG` | per allowed IP | Per-user storage: `burst_dir` = `/data/freeroam/<user>/bursts`, `telemetry_log` = `/data/freeroam/<user>/telemetry.log`; the X17 (LGI) flagged `radar_source: True` (storage under `/data/freeroam/x17/`); tunnel/hairpin aliases (2026-10-03 WG-primary restore): `10.20.30.3` → Rich, `10.20.30.2` → Matt, `172.18.0.1` (catch-all) → Rich, `192.168.50.1` (router-LAN SNAT, WG-off-at-home) → Rich |
| `MAX_BURST_AGE_HOURS` | `24` | Burst retention before rotation |
| `OLLAMA_BASE_URL` | `http://ollama:11434` (env-able) | Ollama endpoint for `/api/vision/*` — Docker DNS, verified 200 in-container. Pre-2026-10-11 builds hardcoded `localhost:11434`, which does not resolve in-container, leaving these endpoints permanently 503; live vision then ran via the Socket.IO feeds + Chat Gateway Desktop Vision ladder (§3.1) — that dead corner was the 2026-10-11 fix target |
| `VISION_MODEL` | `polaris-ai:latest` (env-able) | Ollama model for `/api/vision/*` analysis — vision-capable ladder rung 1 (cloud-backed); pre-fix default was `mistral-large-3:675b-cloud` |
| `VISION_TIMEOUT` | `60` | Ollama vision request timeout (s) |
| `VISION_PROMPTS` | general / tactical / ocr / sharpness_check | Analysis prompt presets |
| `CSIRadarEngine` | `window_size=30`, `movement_threshold=0.15` | Rolling CSI buffer for trajectory + motion detection threshold |
| Vision staleness watchdog | `20 s` stale / `5 s` tick | Marks a flagged-active stream offline once no `vision_frame` has arrived for 20 s; lazily started on the first `vision_connect`, self-re-arms when frames resume |
---

## 5. Sandbox Gateway (host 7007)

**File:** `sandbox_gateway.py` — web terminal for SSH operations against the X17 (LGI).

### REST API
| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Web dashboard (served from `sandbox_static/`) |
| POST | `/api/ssh/execute` | Execute command: `{ "target_host", "command", "timeout" }` → `{ success, returncode, stdout, stderr, timestamp }` |
| POST | `/api/ssh/check` | SSH connectivity check |
| GET | `/api/ssh/history` | Command history (last 50) |
| POST | `/api/ssh/history/clear` | Clear history |
| GET | `/api/status` | Gateway status |

### SSH Target
- Host: `192.168.50.227` (env `LGI_X17_HOST`, legacy `MINI_PI_HOST` name still honored), user `rbuit`, workdir `D:/LGI` (env `LGI_X17_WORKDIR`, legacy `MINI_PI_WORKDIR` still honored)
- Key: `/root/.ssh/id_ed25519` (mounted read-only from `/home/causeiam/.ssh`)
- Options: `StrictHostKeyChecking=no`, `BatchMode=yes`, `IdentitiesOnly=yes`

---

## 6. Voice - Retired from the Mainframe (Text-Only Directive, Oct 2026)

The Polaris Gateway performs **zero audio processing**. Per operator directive the Dell mainframe is a strictly text-only node: chat is a pure text back-and-forth, and no STT/TTS code, libraries, or model files exist anywhere in the gateway image. `faster-whisper`, `kokoro`, and `soundfile` were stripped from the Dockerfile and `requirements.txt`; the `VOICE_ENABLED` gate, `VOICE_PROCESSING_LOCK`, Whisper/Kokoro lazy-loaders, boot warmup thread, `ACTIVE_TTS_STREAMS` bookkeeping, and all five voice routes (→ `/api/tts`, `/api/tts-stream`, `/api/tts/cancel`, `/api/voice-command`, `/api/voice-health`) were excised from `polaris_continuous_learning_gateway.py` (MissPi-style deletion: unknown routes now return 404). The `kokoro-cache` bind mount and `KOKORO_CACHE_DIR` / `VOICE_ENABLED` env entries were removed from `docker-compose.yml`.

**Where voice lives now:**
- **X17 (LGI) - sole Kokoro/Whisper host in the ecosystem.** `audio_stt.py` (open-mic VAD + Faster-Whisper) and `audio_tts.py` (Kokoro, CUDA, 24 kHz) run locally on the laptop; the LGI HUD chat exchanges **text only** with the gateway.
- **FreeRoam Android app - fully on-device.** Android SpeechRecognizer STT + Kokoro-82M TTS (sherpa-onnx, bf_emma) with streamed 24 kHz PCM; only text rides the tunnel.
- **Polaris Dashboard - text-only surface.** The AUDIO RESPONSE checkbox defaults off; with the synthesis routes gone, any manual ▶ attempt simply 404s (the console handler logs once, benign).

The system prompt's “TTS-ready” persona directive (§3.5) remains accurate - synthesis happens off-mainframe, so replies stay conversational and speakable.

---

## 7. AI Tool Calling

Dispatcher: `execute_tool_call(tool_name, arguments)`. All tool schemas are injected into the LLM system prompt at request time.

| Tool group | Source module | Tools |
|---|---|---|
| File operations | `file_tools.py` | `read_doc`, `write_doc`, `append_doc`, `list_docs`, `delete_doc` |
| Playground | `playground_tools.py` | `publish_web_asset`, `list_playground_files`, `run_sandbox_code`, `spawn_service`, `stop_service`, `list_services` |
| Remote execution | `ssh_tools.py` | `ssh_execute`, `ssh_check_connectivity`, `ssh_deploy_file`, `ssh_fetch_file` (targets the X17 / Mainframe) |
| Notifications | `gotify_service.py` | `send_gotify_notification` |
| Memory | `memory_tools.py` | `memory_vault_query` (read), `memory_journal_write` (v1.5 — append-only journal entries; the only chat-side vault write) |
| North Star workspace | `file_tools.py` (v1.4.2) | `list_workspace_files`, `read_workspace_file` — READ-ONLY over the entire repo mounted at `/data/workspace:ro`; prompt-described (no JSON schema — outside the 18-name `_known_tool_names()` union), dispatched by `execute_tool_call`, paged like `read_doc` |

**18 schema-registry tools** plus the two **v1.4.2 workspace tools** — `list_workspace_files` / `read_workspace_file`, prompt-described (no JSON schema) and dispatched by the same `execute_tool_call` — make **20 routable names**; the table above plus the workspace pair is the complete routable set, and unknown names return `{"success": false, "error": "Unknown tool: …"}`. The SSH group ships four tools: `ssh_execute`, `ssh_check_connectivity`, and **`ssh_deploy_file` / `ssh_fetch_file`** (scp-based vault→X17 push and X17→vault pull; the legacy `ssh_upload_file` name is superseded). The 18-name union — every schema registry plus the 11 legacy inline-call names, via `_known_tool_names()` — drives the stripper's known-tool check (§3.2, point 4); the two workspace tools are prompt-only additions outside that union. (v1.5, Oct 4: `memory_journal_write` joined the registry — append-only journal logging, the only chat-side vault write.)

**Routing policy (in system prompt):** notification requests → Gotify tool (never SSH); remote command execution → SSH tools; file operations → file tools.

**File tool size semantics (`file_tools.py`):** every reported size is a true UTF-8 byte length, never a Python character count — `read_doc`/`write_doc` return `"size_bytes": len(content.encode('utf-8'))`, `append_doc` returns `"appended_bytes"` + `"total_bytes"` computed the same way, and `list_docs` uses `os.stat.st_size`. Byte and char counts diverge on non-ASCII content (e.g. `northstar.md`: 54,523 bytes vs 49,967 chars). **`read_doc` is page-based (v3.4):** it returns up to `length` characters starting at `offset` (hard cap `MAX_READ_PAGE_CHARS` = 8 000), with `has_more` / `next_offset` navigation fields so large vault files (e.g. the bind-mounted `northstar.md`) can be walked across conversational turns — one page per chat message, since the tool loop is capped at a single execution turn.

**Standalone toolkit modules not wired into the running gateway:** `system_tools.py`, `livecharts_tools.py`, `www_tools.py` (imported only by the non-launched legacy entrypoint `freeroam_gateway.py`).
---

## 8. Memory Architecture

### Dual-tier live memory
- **SQLite** (`memory_db.py`): users, messages, heuristics, profiles, lists, transcripts. DB file: `/data/state/polaris_state.db` (`DEFAULT_DB_PATH`), lazily created on first use → host bind `/mnt/containers/freeroam/polaris-gateway/state/`, so memory state survives container recreates.
- **ChromaDB** (`vector_memory.py`): collections for global knowledge, user memories, and conversation context. Embeddings generated by Ollama `nomic-embed-text` (hardcoded in `vector_memory.py`).
- `GET /api/memory/stats` returns both tiers; `POST /api/vector/search` for semantic retrieval.

### Memory Vault (`memory_vault.py` + `memory_routes.py`)
Obsidian-style persistent markdown memory with a ChromaDB semantic index:
- Storage: `/polaris_memory_vault` in-container → host bind `/mnt/containers/freeroam/polaris-gateway/memory-vault` (host-editable).
- Index: ChromaDB collection `vault_documents` at `/data/polaris_vector_db` → host bind `/mnt/containers/freeroam/polaris-gateway/vector-db`.
- API: `GET /api/memory/vault/{stats,list,doc}` · `POST /api/memory/vault/{write,update,query,delete,reindex}`.
- `POST /api/memory/vault/update {"path", "content", optional "title"/"tags"}` edits an existing document in place (blocked for `core` and append-only `journal`) and re-embeds it into `vault_documents`.
- Query modes: `hybrid` (default — merges normalized keyword + semantic scores), `keyword`, `semantic`.
- **Watchdog:** daemon thread re-embeds created/changed markdown every 30 s; failed embeds (e.g., Ollama down) auto-retry on the next pass. Requires `nomic-embed-text` in Ollama.
- **Blueprint mirror sync (Oct 4, 2026, boot-time):** `_start_background_systems()` runs `sync_project_docs_to_vault()` before the watchdog starts — it upserts a heading outline (≤ `DOC_OUTLINE_MAX_CHARS` = 11 000 chars; `_extract_doc_outline` with 400-char lead lines; the header records the source path and sync mtime) of `northstar.md`, `freeroam/Polaris-gateway.md`, and `LGI/LGI.md` into **01-Architecture** as `Blueprint - <repo path>` notes (tags `blueprint`, `northstar`, source basename). Deterministic titles mean restarts update in place — never duplicate; a note is re-upserted only when its source's mtime/size changed or the tracked vault copy vanished, with restart-idempotent state in `/data/freeroam/doc_blueprint_sync.json` and a `[Doc Sync] blueprint mirror - created: … | updated: … | unchanged: …` log line (second restart prints `unchanged:` for all three). The repo MDs stay the editable source of truth (operator + Cline only); the vault copies are read-only semantic-search mirrors, re-embedded by the watchdog within 30 s of each sync.
- `POST /api/memory/vault/reindex {"force": false}` rebuilds the index; `force: true` wipes the collection and re-embeds everything.

### Distillation
`distill_memory.py` — nightly fact extraction for the FreeRoam memory loop. Triggered via `POST /api/distill` or scheduled via `POST /api/distill/auto`; daily transcripts ingested via `POST /api/sync/daily-transcript`.

### Sessions & heuristics
- Sliding-window session context (**20** exchanges, 400-char per-exchange render cap) with topic detection (`GET /api/session/topics`); windows are refilled from `chat_history.json` at boot by `hydrate_sessions_from_history()` (§3.4), so the short-term context survives container restarts.
- Heuristics lifecycle: staged → approved / rejected / edited via `/api/heuristics/*`; persisted to `polaris_heuristics.json`.

---

## 9. Supervisor Audit

Blueprint (`supervisor_routes.py`), prefix `/api/supervisor` — screen/clipboard triage records:
`GET /api/supervisor/status` · `GET /api/supervisor/audit/recent` · `POST /api/supervisor/audit`

---

## 10. Gotify Notifications

**Module:** `gotify_service.py` (gateway) · **Server:** `gotify` container (`8081:80`).

Configuration (compose): `GOTIFY_URL=http://gotify:80`, `GOTIFY_TOKEN=<app token>`, `GOTIFY_ENABLED=true`.

Capabilities: `send_notification(title, message, priority, extras)` · scheduled status digests (`start_digest_scheduler`, `queue_digest`) · `health_check()` · `shutdown()`. Exposed to the AI as the `send_gotify_notification` tool; also used by the trading bot for arbitrage alerts.

---

## 11. Polaris Dashboard (host 7000)

Static page `Polaris_page.html` baked into an `nginx:alpine` image as `index.html`. **Depends on QuestDB.**

### Layout
- **Nav bar:** POLARIS logo, user selector (RICH / MATT), SESSION / MEMORY / RADAR dropdown HUD panels, WebSocket status indicator, radar status dot.
- **Chat (top section):** pinned message input + AUDIO RESPONSE checkbox; reverse-chronological exchange feed (newest at top), auto-pruned to 5 messages per sender; optimistic UI; 3-second cross-device auto-sync (polls `GET /api/history/<user_id>`, see §3.4).
- **Holodeck panel (bottom section):** resizable 150–800 px, collapsible; two tabs:
  - **📋 TASK FEED** — Socket.IO client on `:8082` (emits `join_tasks` on every connect/reconnect; the `tasks_dashboard` room name in the payload is ignored); 50-item rolling window; ▶ cyan started · ✓ green completed · ✗ red failed. The gateway's `join_tasks` handler joins the feed socket into room `status`, where all task lifecycle events broadcast, and emits an `active_tasks` snapshot on join — live push is functional.
  - **🧠 MEMORY VAULT** — REST to `:8082` (`/api/memory/vault/{stats,list,doc,write,update,query,delete,reindex}`): category-filtered browse list, hybrid/keyword/semantic search, in-browser read/edit (SAVE re-embeds the document into the ChromaDB index), new-doc compose (user follows the dashboard user switcher, source `dashboard`), confirm-guarded delete, one-click reindex, 30 s status polling with live doc/index counts. Replaces the former SSH SANDBOX tab — the `:7007` sandbox REST API remains for Polaris's own tools.

### HUD panels
- **SESSION:** session duration, sliding window.
- **MEMORY:** SQLite counts (users/messages/heuristics) + ChromaDB tabs GLOBAL / USER / CONTEXT with vector counts; delta-based ops/sec; polls `GET /api/memory/stats` every 5 s.
- **RADAR:** 350×350 canvas — concentric rings, position trail, X/Y coordinates, velocity, motion state, confidence; Socket.IO client on `:8083`.
- **Vision stream:** Socket.IO client on `:8083` (`join_vision`, room `vision_dashboard`) — a viewer-only client (the phantom `vision_connect` handshake was deleted 2026-10-11). Detections/narration via `vision_update` + `vision_history`; the STREAM pill keys on camera truth from `vision_history.stream_active` on join plus typed `jarvis_announcement` `connection`/`disconnection` events — never on the viewer's own socket state.

### Client connections (from `Polaris_page.html`)
| Target | Use |
|---|---|
| `http://<host>:8082` | Chat REST (`/api/chat`, `/api/history/<user>`, `/api/memory/stats`, `/api/chat/history`) + Memory Vault REST (`/api/memory/vault/*`) |
| `ws://<host>:8082` (Socket.IO) | Chat `message` events + task feed |
| `<host>:8083` (Socket.IO) | CSI radar + vision stream |

The dashboard's `POST /api/chat` fetch **discards the HTTP response body** (source comment: "Do NOT add messages here — the WebSocket broadcast will add both user and Polaris messages") — chat bubbles render exclusively from socketio `chat_message` broadcasts, which carry the stripped reply (§3.2). The raw Ollama envelope in the HTTP return (§3.1 note) therefore has no rendering path on any client.

---

## 12. Deployment

### Compose service definitions (current, `docker-compose.yml`)

**polaris-gateway**
```yaml
polaris-gateway:
  build: { context: ./freeroam/polaris-gateway, dockerfile: Dockerfile }
  container_name: polaris-gateway
  ports: ["8082:5000", "8083:5001", "7007:7007"]
  volumes:
    - polaris_data:/app/data                                          # distillation DB
    - /mnt/containers/freeroam/data:/data/freeroam                    # bursts, telemetry, profiles, chat_history.json
    - /mnt/containers/freeroam/polaris-gateway/ingest:/data/freeroam/polaris-gateway/ingest
    - /home/causeiam/docker-containers/northstar.md:/data/freeroam/polaris-gateway/ingest/northstar.md:ro   # North Star blueprint, read-only in the ingest vault
    - /home/causeiam/docker-containers/freeroam/Polaris-gateway.md:/data/freeroam/polaris-gateway/ingest/Polaris-gateway.md:ro   # this document, read-only in the ingest vault
    - /mnt/containers/freeroam/polaris-gateway/playground:/data/freeroam/polaris-gateway/playground
    - /mnt/containers/freeroam/polaris-gateway/memory-vault:/polaris_memory_vault:rw
    - /mnt/containers/freeroam/polaris-gateway/vector-db:/data/polaris_vector_db:rw
    - /mnt/containers/freeroam/polaris-gateway/state:/data/state:rw   # SQLite state DB (memory_db, §8) — survives recreates
    - /mnt/containers/freeroam/livecharts:/livecharts:rw
    - /mnt/containers/freeroam/polaris-gateway/playground/www:/playground/www:rw
    - /home/causeiam/.ssh:/root/.ssh:ro                               # SSH keys (X17 / Mainframe)
    - /var/run/docker.sock:/var/run/docker.sock                       # container management
  environment:
    - GATEWAY_PORT=5000
    - OLLAMAHOST=172.17.0.1:11434
    - OLLAMA_MODEL=polaris-ai:latest
    - VISION_LLM_MODEL=polaris-ai:latest                        # Desktop Vision (LGI vision turns): primary image model
    - VISION_LLM_FALLBACK_MODEL=mistral-large-3:675b-cloud     # proven vision-capable fallback if the primary fails
    - DATA_DIR=/app/data
    - USER_DATA_DIR=/data/freeroam
    - LIVECHARTS_DIR=/livecharts
    - WWW_DIR=/playground/www
    - GOTIFY_URL=http://gotify:80
    - GOTIFY_TOKEN=<app token>
    - GOTIFY_ENABLED=true
  depends_on: [ollama]
  restart: unless-stopped
```

**Single-file `:ro` binds go stale on rename:** `northstar.md` and `Polaris-gateway.md` are mounted into the ingest vault as single-file bind mounts, which resolve the host file by inode — replacing either doc on the host via rename (`mv`, or an editor's atomic save) leaves the container serving the old inode. Recreate the container (or `docker cp` the new file back in) after editing either doc on the host.

The Chat Gateway resolves Ollama via `OLLAMA_URL` (default `http://ollama:11434/api/generate` — Docker DNS on `docker-containers_default`); the compose `OLLAMAHOST` value is read by neither service. `burst_receiver.py` reads `OLLAMA_BASE_URL` (default `http://ollama:11434`, env-able) since the 2026-10-11 fix — it previously hardcoded `localhost:11434`, which was dead in-container (§4 Configuration).

**polaris-dashboard**
```yaml
polaris-dashboard:
  build: { context: ./freeroam/polaris-dashboard, dockerfile: Dockerfile }
  container_name: polaris-dashboard
  ports: ["7000:80"]
  depends_on: [questdb]
  restart: unless-stopped
```

**freeroam** (one-shot Android APK builder)
```yaml
freeroam:
  build: { context: ./freeroam, dockerfile: Dockerfile }
  container_name: freeroam-apk-builder
  volumes: [ ./freeroam/build-output:/output ]
  restart: "no"   # one-shot: docker compose build freeroam && docker compose run --rm freeroam
```
Compiles the FreeRoam Android APK during image build (`RUN ./gradlew assembleDebug`), then copies `freeroam-debug.apk` to `./freeroam/build-output` on run and exits — build tooling, not running infrastructure.

Network: external `docker-containers_default`. Named volumes: `polaris_data`, `ollama_data`, `gotify_data`, `questdb_data` (the `ollama` service bind-mounts `/home/causeiam/.ollama` — `ollama_data` is declared but unused by it).

### Image contents

**Gateway Dockerfile** — `python:3.11-slim`; apt: `openssh-client`, `docker.io`, `curl`, `procps`, `adb`; pip: flask / flask-cors / flask-socketio / requests / python-socketio / watchdog / gunicorn / gevent / gevent-websocket, `chromadb==0.5.23` (voice libraries NOT installed - text-only mainframe directive); `COPY *.py *.json *.html run_gateway.sh sandbox_static/`; `EXPOSE 5000 5001 7007`; `CMD ["./run_gateway.sh"]`.

**Entrypoint `run_gateway.sh`** — arms an ADB watchdog for the operator phone (`192.168.50.42:5555`, 30 s auto-reconnect) and sets `PYTHONUNBUFFERED=1`, then launches `burst_receiver.py` (:5001), the chat gateway under `gunicorn -k gunicorn_gevent_ws.GeventWebSocketWorker -w 1 --access-logfile -` (:5000; custom worker = gevent.pywsgi + WebSocketHandler so engineio websocket upgrades work; auto-falls back to the werkzeug direct-run path if gunicorn/gevent is missing), and `sandbox_gateway.py` (:7007) as background processes and waits on all three PIDs.

**Dashboard Dockerfile** — `nginx:alpine`; `COPY nginx.conf` + `COPY Polaris_page.html /usr/share/nginx/html/index.html`; `EXPOSE 80`.

### Baked-source policy

Neither container bind-mounts its source code — all Python and HTML is baked into the image at build time. Any code change requires an image rebuild + container restart; the dashboard additionally requires a browser hard-refresh (Ctrl+Shift+R).

```bash
docker compose build polaris-gateway && docker compose up -d --no-deps polaris-gateway
docker compose build polaris-dashboard && docker compose up -d --no-deps polaris-dashboard
```

`--no-deps` guarantees linked services (`ollama`, `questdb`) are never recreated or restarted — only the named container is swapped.

### Required Ollama models

- `polaris-ai:latest` — chat model (compose `OLLAMA_MODEL`). Defined by `freeroam/polaris-gateway/Modelfile`: `FROM glm-5.3-flash:cloud`, temperature 0.7, `num_ctx 32768` (v3.4), base SYSTEM persona with the North Star primacy block (§3.5). Modelfile edits are applied with `ollama create polaris-ai:latest -f <Modelfile>` on the running ollama container — instant manifest swap, no restart
- `nomic-embed-text` — embeddings (vector memory + Memory Vault index)

```bash
curl -s http://localhost:11434/api/tags          # verify both are present
curl -X POST http://localhost:11434/api/pull -d '{"name": "nomic-embed-text"}'
```

---

## 13. Health & Operational Commands

```bash
curl -s http://localhost:8082/api/status             # Chat Gateway
curl -s http://localhost:8082/api/status/tasks       # Task tracking
curl -s http://localhost:8082/api/memory/vault/stats # Memory Vault (documents + indexed entries)
curl -s http://localhost:8083/api/vision/stream/status # Burst Receiver — verified 200 from the Mainframe host shell (hairpin path arrives as 172.18.0.1 → allowlisted → Rich; empirically confirmed 2026-10-11); 403 only from docker exec / in-container loopback sources (LAN-only guard) — probe via docker logs polaris-gateway when calling from exec. NOTE: /api/status exists ONLY on the Chat Gateway :8082 — never on the Burst Receiver.
docker exec polaris-gateway curl -s --max-time 4 http://ollama:11434/api/tags   # in-container Ollama path used by chat + Desktop Vision ladder (http://ollama:11434)
curl -s http://localhost:7007/api/status             # Sandbox Gateway
curl -s http://localhost:7000/ | head -5             # Dashboard page
curl -s http://localhost:11434/api/tags              # Ollama models
docker exec polaris-gateway tail -50 /data/freeroam/operator/operator_transcripts.log   # transcripts (root-owned bind mount — read inside the container)
docker logs polaris-gateway 2>&1 | grep -E 'Identity override|Startup scrub'   # identity-override + one-boot history-scrub activity
docker exec polaris-gateway ls -la /data/state/                    # SQLite state DB (memory_db, §8) — lazy-created on first use; absent right after a recreate is normal
docker logs polaris-gateway 2>&1 | grep -E 'DEBUG|Tool Execution|GUARDRAIL'              # stripper / parser / guardrail evidence
```

---

## 14. Gateway File Inventory

| File | Role |
|---|---|
| `polaris_continuous_learning_gateway.py` | Chat Gateway app — HTTP + Socket.IO, North Star-primacy system prompt + blueprint digest block (§3.5), tool calling + stripper pipeline (§3.2), sessions (boot hydration) + heuristics, blueprint vault mirror (§8), task tracking, Desktop Vision turns (§3.1) |
| `burst_receiver.py` | Burst Receiver app — telemetry ingestion, CSI radar, vision stream (X17 webcam `vision_frame` / `radar_motion` feeds), Ollama image-analysis endpoints (in-container Ollama URL dead, §4 Configuration) |
| `sandbox_gateway.py` | Sandbox Gateway app — X17 (LGI) SSH console |
| `run_gateway.sh` | Container entrypoint — launches all three services |
| `memory_db.py` | SQLite memory layer (users, messages, heuristics, profiles, lists) — DB at `/data/state/polaris_state.db`, host-persisted via the §12 state bind |
| `vector_memory.py` | ChromaDB vector layer — embeddings via `nomic-embed-text` |
| `memory_vault.py` | Memory Vault markdown store + 30 s re-index watchdog |
| `memory_routes.py` | Blueprint `/api/memory/vault/*` |
| `memory_tools.py` | Memory Vault query tool (LLM tool schema) |
| `supervisor_routes.py` | Blueprint `/api/supervisor/*` (screen/clipboard triage) |
| `file_tools.py` | LLM file operation tools |
| `ssh_tools.py` | LLM SSH execution tools (`ssh_execute`, `ssh_check_connectivity`) |
| `gotify_service.py` | Gotify notifications, digest scheduler, LLM tool schema |
| `playground_tools.py` | Web asset publishing, sandbox code execution, service spawning |
| `distill_memory.py` | Nightly fact extraction for the memory loop |
| `system_tools.py` / `livecharts_tools.py` / `www_tools.py` | Standalone toolkit modules (not wired into the running Chat Gateway) |
| `sandbox_static/` | Sandbox web dashboard assets |
| `polaris_heuristics.json` | Persisted heuristics store |
| `Dockerfile`, `requirements.txt`, `Modelfile`, `polaris-gateway.service` | Build config, dependency manifest, Ollama model definition, host systemd unit |

Legacy entrypoints on disk (copied into the image, **not launched**): `freeroam_gateway.py`, `polaris_gateway.py`, `polaris_websocket_gateway.py`, `websocket_server.py`, `status_dashboard.html`.

**Dashboard files** (`freeroam/polaris-dashboard/`): `Polaris_page.html` (the entire UI — single-file HTML/CSS/JS), `nginx.conf`, `Dockerfile`.
