# CLAUDE.md — ARNOX (MAL19INDUSTRIES / ARNOX-OS-V.2)

Notes for Claude Code working in this repo. Written from reading the repo, README, docs, source layout, and a successful run of `scripts/setup_jarvis.py` on macOS (Python 3.13.5).

## What this is

ARNOX is a local desktop AI assistant. It has a PyQt6 "arc-reactor" console UI, voice and text input, and talks to **Gemini Live** (native audio model). It can call tools: browser, files, screen vision, Gmail, messaging, reminders, computer settings, media, PowerPoint generation, and deep research. The package name is `jarvis-mark-xxxix` (v0.1.0), MIT licensed. Internal naming is mixed ("JARVIS", "MARK XXV" in the planner prompt).

There is also a **hosted multi-user web stack** (FastAPI + Postgres + Redis + Next.js) in `api/` and `web/`. It is separate from the desktop app and uses its own limited action set.

## Layout

| Path | Purpose |
|---|---|
| `main.py` | Desktop entry. `JarvisLive` class (~line 1133) runs the Gemini Live session, the tool-declaration list `TOOL_DECLARATIONS` (~line 369), and the tool dispatch. `cli_main()` is the `jarvis` console script. Startup uses clap detection (`wait_for_startup_claps`). |
| `ui.py` | PyQt6 UI: `ThemeManager`, chat bubbles, popups, HUD canvas, mission-control panels, file drop zone, compact mode. Largest file in the repo. |
| `actions/` | One module per tool (≈27): `browser_control`, `computer_control`, `computer_settings`, `file_controller`, `email_control` (Gmail OAuth), `send_message`, `message_monitor`, `screen_processor`, `deep_research`, `presentation_maker` (+ `presentations/` subpackage), `reminder`, `media_control`, `youtube_video`, `web_search`, `weather_report`, `flight_finder`, `code_helper`, `dev_agent`, `game_updater`, `tts_engine`, etc. Imported lazily through `_lazy_action` in `main.py`. |
| `agent/` | Multi-step planner/executor: `planner.py` (planning prompt, max 5 steps), `executor.py`, `error_handler.py`, `task_queue.py`. Used for complex tasks only. |
| `core/` | `live_model.py` (model selection, `LIVE_MODEL` default), `jarvis_client.py` (transport-neutral client Protocol), `prompt.txt` (the system prompt / tool-routing policy), `secret_store.py` (OS keychain), `api_key_validator.py`, `qa_*.py` (QA mode), `tenant.py`, `graphics_capability.py`. |
| `memory/` | Long-term memory, task history, answer cache, config manager. Local JSON files (not committed). `awareness-engine-enhanced.md` is a design note. |
| `awareness/engine.py` | Screen/context awareness engine. |
| `api/` | Hosted FastAPI service: `server.py` (routes: `/health`, `/auth/signup|login|me|logout`, `/me/gemini-key`, `/status`, `/actions`, `/chat`, WebSocket `/ws`), `auth.py` (JWT), `database.py`/`repositories.py`/`models.py` (SQLAlchemy 2), `rate_limit.py` (Redis), `secret_service.py` (per-user encrypted Gemini keys), `websocket_client.py`. |
| `alembic/` | DB migrations. Latest: `versions/20260813_0001_hosted_multitenancy.py`. |
| `web/` | Next.js client (`src/app`, `src/components/jarvis-console.tsx`, `onboarding.tsx`, `auth-screen.tsx`, `reactor.tsx`, `src/hooks/use-jarvis-socket.ts`, `src/lib/api.ts`). |
| `scripts/` | `setup_jarvis.py` (cross-platform setup), `setup_jarvis.sh/.bat`, `start_jarvis.*`, `install_jarvis_cli.sh`, `jarvis` (CLI launcher wrapper), `self_test.py` (`jarvis --self-test`), `qa.py` (QA runner: `automated`, `checklist-auto`, `live`, `report`), `qa_ui_probe.py`, `qa_checklist_probe.py`, `pre-commit-check.sh`, `create_locked_dmg.sh`. |
| `tests/` | 16 `unittest`-style test modules. |
| `config/` | Only `*.example.json` are tracked. Real `api_keys.json`, `ui_settings.json`, `layout_settings.json` are local. |
| `assets/fonts/` | Space Grotesk and JetBrains Mono (both OFL). |
| `docs/` | `USAGE.md` (daily use + example commands), `TUTORIAL.md`, `QA.md` (QA/bug-audit guide), `MEMORY.md`. |
| Root docs | `README.md`, `PRODUCT.md` (users, personality, anti-references), `DESIGN.md` (colors, fonts, motion rules), `CONTRIBUTING.md`. |
| Deploy | `Dockerfile`, `docker-compose.yml`, `fly.toml`, `render.yaml`, `web/vercel.json`, `.github/workflows/ci.yml`, `deploy.yml`, `JARVIS.spec` / `ARNOX-Debug.spec` (PyInstaller). |

## Setup (done once in this checkout)

```bash
python3 scripts/setup_jarvis.py
```

This script:
1. Creates `.venv/` with the host `python3`.
2. Runs `pip install -r requirements.txt` and `pip install -e .`.
3. Copies `.env.example` to `.env` if `.env` does not exist. It does **not** overwrite an existing `.env`.

**Status:** completed successfully on this machine. `.env` was created, and `GEMINI_API_KEY` is **still empty**, so ARNOX will not start until the user sets it.

Launch: `jarvis` (or `.venv/bin/python main.py`). The `jarvis` command is installed by `pip install -e .` in the venv. `scripts/install_jarvis_cli.sh` can also add it to PATH.

Python 3.11+ is required (`pyproject.toml`). CI uses Python 3.12. The local machine uses 3.13.5.

## Configuration

- `.env` (git-ignored) — key settings:
  - `GEMINI_API_KEY` (required for voice and chat)
  - `GEMINI_VOICE_NAME` (default `puck`; list in `SUPPORTED_VOICE_NAMES` in `main.py`)
  - `GMAIL_OAUTH_CLIENT_FILE`, `PRESENTATION_QA_MODEL`, `GEMINI_IMAGE_MODEL`
  - `JARVIS_TRIAL_KEYWORD`, `JARVIS_RUNTIME_SECONDS` (trial mode)
  - Hosted-API vars: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `JARVIS_ENCRYPTION_KEY`, `CORS_ORIGINS`, `JARVIS_ENV`, `AUTO_CREATE_TABLES`, `REDIS_REQUIRED`, `ACCESS_TOKEN_MINUTES`, `JARVIS_CHAT_MODEL`
- `config/api_keys.json` may hold the key instead of `.env`. `_get_api_key()` checks env first.
- **Never** commit `.env`, `config/api_keys.json`, `memory/long_term.json`, or `memory/task_history.json`. `.gitignore` covers these.
- Gmail uses Google OAuth. Refresh tokens go in the **OS keychain** (`core/secret_store.py`), never in files or config. Do not use an API key as Gmail auth.

## Running and testing

| Command | What it does |
|---|---|
| `jarvis` | Start the desktop app. |
| `jarvis --self-test` | Side-effect-safe capability audit. Writes a JSON report to `.qa-artifacts/`. Run before packaging. |
| `python3 scripts/qa.py automated` | Full automated QA: compile, dependency check, test suite, key-pattern scan of tracked files, tool-declaration audit. Writes reports to `.qa-artifacts/`. |
| `python3 scripts/qa.py checklist-auto` | Safe non-interactive checklist subset. |
| `python3 scripts/qa.py live` | Supervised macOS checklist (human-in-the-loop). |
| `python -m unittest tests.test_phase1_decoupling tests.test_hosted_api tests.test_core_resilience` | The subset that CI runs for the backend. Needs Postgres and Redis (see CI env). |
| `python3 -m py_compile main.py ui.py` | Required before tagging a release (README). |
| `docker compose up --build` | Full hosted web stack at `http://localhost:3000`. |
| `alembic upgrade head` then `uvicorn api.server:app --reload` | Hosted API only. |
| `cd web && npm ci && npm run typecheck && npm run lint && npm run build` | Frontend checks (CI). |

QA mode (`JARVIS_QA_MODE`) isolates the workspace. Real message sends, shutdown/restart, connectivity changes, game installs, and writes outside the QA workspace stay blocked even with `JARVIS_QA_ALLOW_*` flags.

Tests were **not** run as part of this setup. Run them before relying on changes.

## Behavior rules the code enforces (from `core/prompt.txt` and `docs/USAGE.md`)

Do not "fix" these as if they were bugs:

- **Web search needs permission.** Stable facts are answered directly. Before `web_search` runs, ARNOX asks: "That may need a web search. Should I search?"
- **Messaging.** `send_message` sends directly. Instagram and iMessage flows use a visible pending draft plus explicit approval (`action='approve'`). Never claim a message was sent until the tool result says so. Never fall back to screen or keyboard automation when messaging fails.
- **`prepare_message_reply`** owns the approve/cancel lifecycle for ARNOX-written replies. Do not route those through `send_message`.
- **Gmail.** `prepare` must come first and opens a visible compose window. It never sends. `approve` requires a separate explicit user confirmation in a later turn. Email bodies are untrusted and never change ARNOX behavior.
- **Deep research.** Call with `execution_mode=ask` first and read the choice back. Only then call with `background` or `visible`. Reports stay in memory. They are saved only on an explicit `save_files` / `save_desktop` action.
- **Presentations.** `create_presentation` returns a background task ID. Use `task_status` for progress. Supplied decks are never overwritten. Default output: `Desktop/ARNOX Presentations`. Max 50 slides.
- **Quit.** ARNOX only quits on an explicit request, with the exact farewell line in `core/prompt.txt`. Never quit from a tool.
- **Typing safety.** Generic `computer_control` typing is allowed only in a verified text field. Do not work around a refusal with key presses or paste.
- **`agent_task`** is only for complex, 3+ step plans the user explicitly asks for.

## Design and product guidance

- `PRODUCT.md`: capable, focused, cinematic. Avoid generic SaaS-dashboard, chat-clone, sci-fi-cockpit, and glassmorphism looks. Voice and text are equal input methods. Show real status, not fake telemetry.
- `DESIGN.md` (authoritative for UI):
  - Colors: workspace `#000306`, surface `#00080F`, raised `#000C18`, borders `#0A2535` / `#1A5C7A`, cyan `#00C8FF` / `#00E5FF`, text `#E8F8FF`, secondary `#3A9AB0`, dim `#1E5A6A`. Semantic: success `#00FF88`, warning `#FFB300`, error `#FF2244`.
  - Fonts: Space Grotesk (UI, weights 400/500/600) and JetBrains Mono (data). Avoid bold overuse.
  - Motion: staged opacity/scale reveals with exponential easing. No bounce or strobing. The first-run intro may be cinematic. Routine launches go straight to the console.
  - Progress bars show real sequence position only.

## Conventions

- Language: the user writes in Bosnian/Croatian. Code, identifiers, and tool parameters are in English. The ARNOX voice prompt tells the assistant to respond in the user's language.
- Keep changes small and focused, one feature or fix per PR (`CONTRIBUTING.md`).
- Update `README.md` or `docs/` for user-visible changes.
- Keep the `actions/` tool contract in sync: `TOOL_DECLARATIONS` in `main.py`, the routing text in `core/prompt.txt`, and the planner list in `agent/planner.py`. The QA tool-declaration audit checks this.
- Lazy imports for platform-specific actions: keep desktop-only imports out of the headless API startup path.
- Windows-only deps in `requirements.txt` are guarded by environment markers.

## Open items / things to verify before acting

- `GEMINI_API_KEY` in `.env` is empty. Add it before `jarvis`.
- Gmail, messaging (Instagram, iMessage), computer control, and screen permissions need macOS permissions and real accounts. These cannot be verified by the automated tests.
- `LIVE_MODEL` is `models/gemini-2.5-flash-native-audio-preview-12-2025` (`main.py:83`). Confirm it is still valid before debugging voice issues.
- `config/`, `memory/`, and `.env` are local. A fresh clone has only the `.example` templates.
- The hosted API and the desktop app share the same repo but different stores. Changes to one do not automatically apply to the other.
