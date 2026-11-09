# Cofinance local research workspace

The implementation checklist and remaining original milestones are in [TODO.md](../TODO.md).
This is a working interactive slice, not a calibrated or fully complete finance-research product.

## Start on Windows

Double-click `run-dashboard.cmd` in the repo root, then open http://127.0.0.1:8878
in a regular browser, not an embedded preview. Keep the terminal open; Ctrl+C stops it.
Do not start a second copy when the address is already running.

The launcher builds the frontend and starts the Python service. After a build,
Node is not needed to serve it:

```text
python -E -B -m ui.dashboard --port 8878
```

The interactive runtime needs the installed local inference packages. Do not use `-S`
for this server. The old read-only viewer remains independently available:

```text
python -S -E -B -m ui.workbench --port 8876
```

## Interface

- Chat: persistent v2 conversations, new chat, idempotent background runs, Stop,
  source-backed charts, explicit asset tracking, and a process map for each answer.
- Memory (brain button in the chat top bar): what the conversation can recall. Remember a short note, forget
  one item, or forget the whole conversation; *Forget from memory* sits under each answer. Forgetting stops recall
  only: the answer stays in the history. Notes are context for the narrator, never evidence.
- Research: recorded synthesis, specialist reports, and gate decisions.
- Execution: switch between Run map and Step list. Select a node or observation
  to inspect input, emitted labels or text, number validation, and accepted output.
- Models: narrator selection, download/resume, integrity verification, qualification,
  load/unload, the two-clock system guide, and actual model-call inventory. A read-only smoke-comparison
  disclosure records LFM3/MiniCPM measurements and their limits. LFM3 remains the chosen narrator;
  candidate scores do not change selection or make alternatives selectable.
- Dataset: source/date provenance and human-review readiness, without invented labels.

Maps show actual recorded activity. Missing and not-dispatched components are distinct
from successful calls. Fixture and replay modes stay explicit. Branches represent role
ownership, not concurrent execution. The current specialist workflow is sequential.

Spidermonkey/Jev inspired the signal-gating pattern. No Spidermonkey hosted service runs
behind the app. GLiNER classifies; Python owns policy; the selected base narrator can
serve multiple specialist roles through separate calls. Model output is candidate text,
not an accepted answer until the recorded guard permits it. No private chain-of-thought
is exposed or invented. Older traces may have a clearly labelled reconstruction of their
numeric allowlist; new traces record the guard evidence directly.

## Runtime boundaries

React + TypeScript + Vite, real shadcn/Radix controls, Tailwind v4. Geist and Lucide are
bundled, with no external browser assets. Plotly is lazy-loaded from the local build only
when a price chart is shown. Linear-inspired composition, not an official Linear package.
The dark identity and original keyboard controls are preserved.

The managed narrator uses the approved pinned official LFM3 GGUF and llama.cpp CPU build.
Model download, hash verification, qualification, and loaded state are separate concepts.
Passing the synthetic smoke suite does not establish financial accuracy. Failed or stale
qualification cannot enable the picker. Tool-only reporting does not count as model inference.
No hosted-model fallback, arbitrary model URLs, arbitrary shell commands, or model tool authority.

GLiNER runs from its pinned existing local cache in a bounded subprocess. Startup and
inference can be cancelled without importing the ML stack into the HTTP server. A restart
marks incomplete chat jobs interrupted rather than silently replaying user requests.

Market tools fetch real provider data. Failures stay unavailable. A 52-week high is never
called an all-time high, and missing sentiment does not become a default neutral score.
News currently reads the accepted operational local store; chat does not refresh news itself.
Full fundamentals, primary-source breadth, ongoing news collection, and human-labelled calibration
remain unfinished. Follow-ups reuse the conversation's assets and a bounded, same-asset recall of earlier
accepted turns and your notes (hybrid keyword + Hy-Memory lite by default, `COFINANCE_MEMORY=keyword` to
turn the semantic side off). Recalled text is context only: its numbers never pass the number guard.
See the repo checklist for acceptance gates.

## API and security

One production loopback origin serves the frontend, read-only trace/corpus routes, and
interactive routes. Host and Origin checks remain. A user-activated top-level navigation
may open `/`; cross-site API reads and frames are blocked. Mutations require exact same
Origin, JSON, a bounded body, and the server's request token. No wildcard CORS.

Read routes: `/api/runs`, `/api/run?id=...`, `/api/corpus`, `/api/state`,
`/api/conversation?id=...`, `/api/job?id=...`, `/api/labels`, `/api/memory?session_id=...`.

Explicit write actions: `/api/sessions`, `/api/chat`, `/api/jobs/cancel`,
`/api/models/action`, `/api/models/select`, `/api/watchlist`, `/api/news/collect`, `/api/labels`,
`/api/memory/note`, `/api/memory/forget` (`item_id` of `*` forgets the whole conversation).
Memory actions are refused while a run in that conversation is active, and are idempotent per request id.

Only the approved pinned catalog can download or execute a managed runtime. Downloads
are resumable partial files and require the pinned size and hash before promotion.
Runtime extraction is bounded and checked for unsafe paths. Local inference uses an owned,
authenticated loopback process, no inherited proxy credentials or hosted inference.

Trace, chat, model-operation, and live-map polling have timeouts and cleanup. Inspector
selection, disclosure, scroll, focus, and drafts remain stable across polling updates.
Action retries reuse request identity to avoid duplicate research submissions.

Evidence is rendered as text, never trusted HTML. External evidence links permit only
credential-free HTTP(S) and never open automatically. Traces are bounded and redact key
fields and tagged thinking; this is not comprehensive data-loss prevention. Numeric
matching is not complete factual verification: units and the claim itself still matter.

CSP permits self scripts, not inline scripts or eval; inline styles are allowed for Radix
positioning and nesting. Built assets are restricted to bounded files under `dist/assets`.
No arbitrary file serving, public binding, or unknown-path SPA fallback.

## Checks

```text
cd frontend
npm ci --ignore-scripts
npm test
npm run build
npm audit
```

Headless host/chat checks use the standard library:

```text
python -S -E -B -m unittest tests.test_chat tests.test_trace tests.test_dashboard.TestDashboardServer
python -m unittest discover -s tests -t .
```

The full suite needs the installed ML packages. `COFINANCE_HYMEMORY_TESTS=1` adds the live Hy-Memory
round trip and delete.

Browser regressions use installed Chrome/Playwright without browser downloads:

```text
set COFINANCE_BROWSER_TESTS=1
python -B -m unittest tests.test_dashboard tests.test_run_map_browser
```

Real local-model runs are separate from fixture tests and cannot be replaced by them.
Keep measured reports and traces private in ignored local directories.

Keyboard: Ctrl+K / Cmd+K finds a run; C opens Chat; 1-4 retain the original view shortcuts;
`/` opens step search; J/K move between execution steps; Escape closes or deselects.

## Development and rollback

Run the Python service in one terminal, then `cd frontend` and `npm run dev` in another.
Vite binds to http://127.0.0.1:5173 and proxies `/api` to the exact local backend origin.
It does not add broad CORS. The built production entry is preferred for normal use.

The original v1 UI and databases are preserved, not migrated. V2 conversations and model
files live under ignored `data/`. Orchestration changes repair numeric validation and
remove silent watchlist writes; they are not an unmodified copy of the v1 runtime.
Stop the React service and use `ui.workbench` for the read-only fallback.
No commit, push, branch, PR, or public deployment is authorized by these local changes.

Generated controls use shadcn 4.23.0. Official CSS helpers and MIT attribution are retained
in `src/vendor/`; the generator itself is not installed as an application dependency.
