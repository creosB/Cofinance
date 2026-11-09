# Cofinance v2: goal and delivery checklist

Working plan, reviewed 2026-11-09. Baris approved part-by-part commits, pushes and merges on 2026-11-08.
Deployment and disclosure of private local evidence are not authorized. Only non-sensitive measured summaries belong in the repo.

## Product goal

A local finance-research workspace where Baris can chat about stocks and crypto,
run a deterministic multi-agent research workflow, inspect every model and tool
boundary, and follow the accepted answer back to source evidence.

Python owns routing policy, fetching, validation, persistence, and explicit user
writes. Models classify or narrate; they do not receive tools or write authority.
No hosted inference. No fabricated prices, progress, reasoning, or accuracy claims.

## Current priorities (2026-11-09)

- CI is intentionally deferred by Baris. No workflow or CI setup is part of this iteration.
- [x] Fail closed on malformed classifier judgments (NaN, infinity, out-of-range probabilities, invalid direction/alert flag). Invalid output is deferred and remains retryable, never marked seen or alerted.
- [x] Replace the blanket risk-negation exemption: incidental "not" and "not only X but also Y" cannot authorize an invented risk. A narrow full no-risk statement remains allowed. Regression tests: tests/test_guard_boundaries.py.
- [x] Frozen historical experiment completed (2026-11-09): 125 non-overlapping sampled asset-windows across AAPL/NVDA/MSFT/BTC/ETH, outcomes through Nov 6, no skipped forecasts/errors/abstentions. LFM3 predicted UP every time: accuracy 20.0% / 53.3% / 51.2% for 1/3/6 months, exactly always-UP; momentum 65.0% / 60.0% / 53.6%. No predictive edge demonstrated. This is a price-only benchmark arm, NOT the production research pipeline. Code, frozen protocol, result and leakage limits: docs/benchmarks/HISTORICAL.md. LFM3 stays the research narrator.
- [x] Genuine public-data chat/execution screenshots added to README and docs/screenshots; isolated conversation, real GLiNER/Yahoo, explicitly tool-only. No private chats or fake successful inference.
- [x] Forward benchmark started 2026-11-09 (qualify/forward.py, docs/benchmarks/FORWARD.md). Batch 1 sealed 06:45 UTC before entry: LFM3 news and price arms both UP on all five assets again; momentum DOWN on 4/5; news rule UP on NVDA only. Hash-chained ledger in ignored data/forward_benchmark, public labels in docs/benchmarks/forward-ledger.jsonl. tests/test_forward.py (6).
- [ ] Weekly: Monday before 17:30 Istanbul collect news, record, commit forward-ledger.jsonl; score after Friday. No claim before 26 matured batches.
- [ ] Still open: timestamp-safe full-research replay with time-valid news/fundamentals (needs a dated news source, approval). Never fill old cases with today's facts or retune on the measured historical period.

## Current truth

- [x] Headless GLiNER routing, specialist orchestration, signal gates, and event bus exist.
- [x] React dashboard, local API, trace inspector, model picker, and managed downloads exist.
- [x] Official narrator/runtime pins, integrity checks, license copy, and smoke qualification exist.
- [x] Persistent v2 conversations, background research jobs, cancellation, and explicit watchlist actions exist.
- [x] A real chat completed with source-backed market data and an interactive chart; reload persistence was browser-checked.
- [x] That run visibly used deterministic fallback after narrator validation rejected candidate text.
- [ ] Meet the full original calibrated finance-research product acceptance gates. The current slice is not the complete product.

Checked means the stated scoped behavior was observed, not that every adjacent
feature or finance claim is validated. Local logs and traces remain in ignored data.

## Original six workstreams

### 1. Headless core: implemented, ongoing hardening

- [x] Separate classification, Python policy, tools, and narration.
- [x] Preserve equity versus crypto risk modes and fail-closed signal errors.
- [x] Repair the empty-market-evidence number-gate bypass.
- [x] Stop research queries from implicitly writing a watchlist.
- [ ] Expand adversarial tests for malformed routing heads and entity resolution.
- [ ] Strengthen numeric provenance beyond matching values, including units, field identity, dates, signs, and scale.
- [ ] Remove stale documentation claims and keep capability labels tied to measured evidence.

### 2. Qualification: partial, not calibrated

- [x] Narrator comparison, 2026-11-09: LFM3 1.7B, MiniCPM5-1B and MiniCPM5-2B (Q4_K_M, llama.cpp b12110, 2 repeats) all passed the 7-unique-case narrator gate. Scores and limits are recorded in README.md and the Models disclosure; no selection changed. Baris explicitly retained LFM3 for quality, not the fastest smoke-suite candidate.
- [ ] Repair judge bake-off integration failures: MiniCPM5-2B-Jev hit the request timeout on 91/91 train rows; d1-2B failed before inference on an incompatible Transformers import. These are NOT valid model-quality scores; no judge or cascade adoption decision follows from them.

- [x] Collect a real, source-preserving candidate corpus and blank human-label workflow.
- [x] Run pinned local narrator smoke gates before allowing selection.
- [ ] Obtain the original target of at least 120 human-labelled headlines, including at least 20 traps.
- [ ] Keep shared events grouped across train/held-out splits; finalize only complete evidence and labels.
- [ ] Calibrate decision thresholds on training data, then test once on held-out data.
- [ ] Complete balanced narrator role and failure-case evaluation beyond the small synthetic smoke suite.
- [ ] Compare optional adjudicators only after a labelled decision baseline exists; adopt only on the original improvement gates.
- [ ] Record hardware, revision, quantization, decoding, and provenance for every comparison.

### 3. Clock 1 news: collection and diagnostics implemented, operational loop unfinished

- [x] Isolated last30days collection, pre-gate, dedupe, SQLite TTL/cooldowns, and replay tests exist.
- [x] Limited real keyless collection and official issuer-feed collection were exercised.
- [ ] Connect approved sources to the operational news store with freshness and failure visibility.
- [ ] Add a bounded ongoing collection scheduler, cancellation, retries, and run-health controls.
- [ ] Add regulatory/primary evidence and primary Bitcoin sources.
- [x] Treat prediction-market-style questions as speculation without hiding genuine reported questions. Built 2026-11-08: core/pregate.py speculation_reason (prediction-market sources, market-odds text, yes/no title questions with a deadline or price line), IGNORE with reason speculation:<kind>, RunSummary.speculation; tests/test_clock1.py 5 flagged, 7 real-question controls kept.
- [ ] Retune cascade gray zones from labelled data instead of guessed score lines.
- [ ] Never show a diagnostic alert outcome as a sent notification.

### 4. Real tools and narrator runtime: partial

- [x] Managed pinned llama.cpp, local narrator inference, download/resume, integrity, and qualification controls.
- [x] Real market snapshot/series path and locally bundled interactive Plotly charts.
- [x] Source failures stay unavailable; no random demo prices or neutral sentiment substitutes.
- [x] A 52-week high is no longer presented as an all-time high.
- [x] Fundamentals 2026-11-08. Finding: the old code read P/E, market cap and EPS from the chart response, which never carries them, so fundamentals were ALWAYS unavailable. Probe (data/_fundamentals_probe.py, same approved Yahoo host, keyless, no cookie or crumb): fundamentals-timeseries returns trailing and forward P/E, market cap, TTM revenue and diluted EPS with as-of dates for NVDA and AAPL. core/market.py add_fundamentals: latest point per field, its own as-of date and period kept for the UI (never narrated), money fields dropped when their currency differs from the quote, each field independently absent on failure, one bounded extra request. Narrator labels added; "Fundamentals: not available" only when none is present. tests/test_chat.py +3.
- [x] True ATH 2026-11-08: highest monthly high over the source's full history (chart range=max, split-adjusted), drawdown from it, history start shown (NVDA since 1999-02, BTC since 2014-10, so "all-time" means in source history). Unavailable stays explicit. Still open: validate crypto volatility and sentiment provenance.
- [x] Live narrator check with the new fundamentals facts 2026-11-08: 3/3 turns accepted, no fallback, every fundamentals number copied exactly. Finding: answers became unreadable number dumps ("4,506,796,080,000", "23.5954") and the follow-up restated the whole fact block.
- [x] Readable numbers 2026-11-08: Python formats (market cap, revenue and volume with a scale word to 2 decimals, ratios and percentages to 2 decimals, prices as the source wrote them); the narrator sees only the displayed value; the allowlist is exactly the displayed values WITH scale (core/numbers.py, scale-aware: "4.51 billion" for a 4.51 trillion cap is rejected, as is the raw unshown 4,506,796,080,000). qualify/scorers.py deliberately unchanged because it is in the narrator qualification fingerprint. Follow-ups: the instruction now comes after the reports (it was ignored before them) and asks for at most two numbers; GATE_CHECK records follow_up and restated_numbers (diagnostic only, never a rejection). tests/test_readable_numbers.py (6). This also covers part of "numeric provenance: scale" in workstream 1; units, dates and signs are still open.
- [x] Direction (sign) check 2026-11-08. Live finding with readable numbers: "last price 186.64 USD (down from 181.6 USD)" passed every check; the move was up. Now Python computes change_from_previous_session_pct and shows it as a word plus a size ("up 2.78"), and core/claims.py direction_conflicts rejects a clause that names the previous close (or says "previous session") with the opposite move, for the Data Analyst and the Lead (checked against the structured facts, not model text). tests/test_readable_numbers.py TestDirection (3). Claim eval unchanged: 10/10 wrong caught, 0/6 real rejected.
- [x] Bug I introduced and caught the same hour: the new guard module was named core/numbers.py. core/hymemory_worker.py runs as a script, so core/ is first on its import path; numpy imported my file instead of the stdlib numbers module and the worker died at startup (live Hy-Memory tests 0/2; the dashboard would have silently fallen back to keyword). Renamed to core/numeric_guard.py; live tests 2/2 again. New guard test: no core/ module may share a stdlib name. It also found core/trace.py (stdlib trace): pre-existing, harmless so far, kept as a named exception. TODO: rename core/trace.py when that module is next touched.
- [x] Strict follow-ups 2026-11-08 (approved): an accepted follow-up that repeats more than two figures from the earlier answer gets ONE retry with a stricter last instruction (TeamLeadOrchestrator.STRICT_FOLLOW_UP, max 160 tokens). The retry passes the same number, scale, wording and direction guards and is used only if it is faithful and repeats fewer figures; otherwise the first answer stays. Cost: at most one extra narrator call, only on restating follow-ups. GATE_CHECK records follow_up_retry (first_restated, retry_restated, retry_passed_guards, used). Run map: shows the candidate that was used, explains the retry and direction rejections, and reads scale words like the backend (it would have shown "4.51 trillion" as an unmatched 4.51). Tests: tests/test_readable_numbers.py TestStrictFollowUps (4), run-map.test.ts +3.
- [ ] Improve evidence-only narrator prompts so metadata/labels do not cause needless validation failures.
- [ ] Verify factual claim support beyond numeric matching before trusting generated synthesis.
  - [x] Risks 2026-11-08 (Brain's choice, Baris said decide freely). Offline eval over every saved real Lead narration (data/_risk_eval.py): 25 of 27 name a risk and 21 of those name one the evidence does not support (short interest, pullback toward the low, execution gaps, supply chain, overvaluation, concentration; and NVDA's "lack of fundamental data" leaking into Apple answers). The 4 that pass are genuinely supported. Judge and rule author are the same (Brain), so indicative only. Fix: core/risk_cues.py builds a closed, digit-free risk list from tool facts (drawdown of at least 10 percent from the all-time or one-year high, down since the previous close, missing fundamentals, crypto Fear and Greed above 75 or below 25, annualized volatility of at least 60 percent, bearish headlines, news that is only issuer announcements); the Lead sees it and may name only those; every and/or item of a risk phrase must contain an anchor of a listed risk, or the clause must be a negation. One strict retry, then the specialist reports (which name no risk). Lead wording evidence now includes the risk list. GATE_CHECK records unsupported_risks and risk_retry; the run map explains both. At most one Lead retry per answer (risk retry first, then follow-up retry). tests/test_risk_cues.py (9), run-map.test.ts +1. Known limits: a supported risk phrased with none of its anchors is rejected (conservative); a negated clause is never checked ("the risk is not only X but Y" slips through); thresholds 10/60/75/25 are judgment calls, not calibrated.
  - [x] Feedback retry 2026-11-08 (found live: a turn named the listed risk correctly, then fell back on the word "ATH"). The one Lead retry now covers unsupported risks, unsupported words, unsupported numbers and direction conflicts, and its last instruction names exactly what was rejected ("Remove: supply-chain disruption.", "do not use them: Blackwell"); fed-back risk text drops leading filler. GATE_CHECK records guard_retry {reason, rejected, retry_passed_guards, used, retry_reason, retry_rejected}; the run map explains it. Still at most one Lead retry per answer (a guard retry blocks the follow-up retry). "ATH" now counts as the all-time high when the evidence has one (core/claims.py EQUIVALENT, evidence side only). tests/test_risk_cues.py (11). Full suite 304 OK (17 skipped), live Hy-Memory 2/2.
  - [x] Live 3-turn run 22:17 (Istanbul): NVDA thesis accepted first try with "ATH"; follow-up retry used again (4 restated figures to 0). Apple turn fell back on "Researcher" (the Lead naming our own News Researcher), and the feedback retry repeated it. Fixed: agent role names (the prompt's own section headers) are now wording evidence for the Lead. Targeted tests 70 OK. Live re-run 22:56: 3/3 accepted, no fallback; the Apple answer ("The News Researcher report states...") passed. New false positive seen: the Apple first draft was rejected for the risk item "matches the listed" (the Lead talking about the list itself); the feedback retry passed, so cost was time, not accuracy. Fix candidate: skip risk clauses that refer to the list ("listed", "the risk list").
- [x] Delete and Clear all on the Conversations panel (asked by Baris 2026-11-08). ChatService.delete_conversation / clear_conversations, POST /api/sessions/delete and /api/sessions/clear (outside the service lock, like memory actions; idempotent per request id). Order: forget all (tombstone, delete copies, sweep vector store), scrub tombstone text, delete messages, runs and session in one transaction, delete run trace files. Refused while a run or its memory write (self.threads) is in flight; a conversation being deleted refuses new runs, notes and forgets. secure_delete on chat_v2.db and memory_v2.db connections: found by the test that reads the file bytes, a tombstone UPDATE had left the question text in free page space. UI: hover trash icon per row with inline confirm (focus on Cancel, Escape cancels), Clear all dialog. tests/test_delete_conversations.py (6); browser check on an isolated temp server (data/_conversations_ui_check.py): hover, confirm, Escape, delete, clear, 0 traces left, watchlist kept, no console errors. Not done: the mobile conversation picker has no delete yet; Hy-Memory's own files are not byte-checked (its delete is verified by API only).
- [x] Router speed probe run 2026-11-08 23:10 (data/_router_speed_probe.py, rule fixed before timing; 24 queries: 6 real, 18 synthetic written by Brain). One pass (classification + entities in one schema) median 942 ms vs two passes 1365 ms (cut 31%, passes), but tickers identical on 23/24 only: "nvda news" got NO ticker in one pass (two passes found NVDA), and "how is nvidia doing today" flipped intent price_snapshot to thesis_review. Rule needs 100% tickers, so REJECTED: routing keeps two calls. No-entity option (reported only): 22/24, also loses "nvda news". Router is about 1.4 s of a run; the narrator is the larger cost.
- [ ] Speed, next: measure narrator time per stage from traces (Data, News, Lead, plus retries), then decide what to cut. Each Lead feedback/follow-up retry costs one more narration (about 6 s seen live).
- [ ] Evaluate a GPU backend only when measured CPU latency warrants the extra runtime burden.

### 5. Memory: not implemented as originally planned

- [x] New v2 session storage is isolated from legacy databases.
- [x] Simple follow-ups reuse assets only within their own conversation.
- [x] Verify Hy-Memory's exact license and lite-mode behavior: Apache-2.0, no extra restrictions. Lite = raw embedding writes + vector search, no LLM calls, but it needs an OpenAI-compatible embedder (default hosted) and a vector store (chroma/qdrant/faiss).
- [x] MemoryPort (core/memory.py) with a local SQLite FTS5 backend: "chat" is the only writable namespace, recall is per conversation, idempotent per run, provenance (run id, score) in the trace and payload. Recalled text is context only; its numbers never enter an allowlist. tests/test_memory_port.py.
- [x] Hy-Memory lite adapter (core/hymemory_port.py + core/hymemory_worker.py), approved 2026-11-08: Qwen3-Embedding-0.6B-Q8_0.gguf (639,150,592 bytes, SHA-256 06507c7b...c3e439) on the managed llama-server, hy-memory==2.0.14 in the isolated venv data/hymemory/venv (lock: data/hymemory/requirements.lock.txt). Main Python untouched.
- [x] Network proof: trap proxy caught 5 tiktoken download attempts (openaipublic.blob.core.windows.net) on the first run; tiktoken is now blocked in the worker (SDK falls back to a character estimate). Re-run: zero outbound attempts; SDK reports zero LLM calls. Worker LLM endpoint is a closed local port; all non-local traffic goes to a closed proxy.
- [x] Provenance: lite search does not return caller meta, so data/hymemory/links.db maps memory_id to run id, tickers, question. Unknown or other-conversation ids are dropped.
- [x] Comparison (data/_memory_compare.py, 20 follow-ups, written by the assistant, 12 paraphrases): keyword 15/20 top-2, Hy-Memory 17/20 (+2). Below the agreed +3 bar, so KEYWORD STAYS DEFAULT. Hy-Memory returned nothing on 2 keyword wins (min_score 0.40); not re-tuned on this set.
- [x] Opt-in: COFINANCE_MEMORY=hymemory. Keyword is always written too; if Hy-Memory fails, recall falls back to keyword and the trace shows "keyword fallback". Tests: tests/test_hymemory_port.py (live round-trip with COFINANCE_HYMEMORY_TESTS=1).
- [x] Hybrid recall (core/memory.py HybridMemory): keyword + Hy-Memory lite fused by reciprocal rank (k=60, untuned), ties to the better semantic rank, both sides written, keyword first. Each hit records found_by (keyword, semantic or both) in the trace and payload. Opt-in: COFINANCE_MEMORY=hybrid. Tests: tests/test_hybrid_memory.py.
- [x] New set (data/_memory_compare_v2.py, 24 answerable + 4 no-answer, rule pre-registered in the script): keyword 17/24 top-2, Hy-Memory 22/24, hybrid 22/24 (top-1 22 vs 21 for Hy alone). Paraphrase 6/13 keyword vs 11/13 both others; lexical 11/11 all. False recalls 0/4 keyword, 1/4 Hy and hybrid ("mining difficulty" pulled two crypto turns). Rule PASSED. Bias: written by the assistant, 13/24 computed paraphrases, synthetic turns. Hybrid latency in this run (13 ms median) is flattered by the embedding cache from the Hy-only pass; expect about 0.1 s.
- [x] Live bug found with hybrid on, fixed: turn 1 showed complete, but the server still held the run slot while the first Hy-Memory write started the embedder, so the next question got a 409. Memory is now written after the slot is released (still tracked so shutdown waits for it); writes are serialized per store so a racing backfill cannot duplicate a turn. Regression: tests/test_memory_port.py test_slow_memory_write_does_not_hold_the_run_slot.
- [x] Default decision 2026-11-08: Baris approved. HYBRID IS NOW THE DEFAULT when Hy-Memory is installed; keyword otherwise. COFINANCE_MEMORY=keyword or =hymemory overrides. Cost: embedder starts on the first memory write (about 4 s, 640 MB model). Revisit if real conversations show no gain over keyword. tests/test_hybrid_memory.py TestMemoryDefault.
- [x] Caveat (from tips.md review): the 5-0 win on the synthetic set is thin evidence (exact McNemar two-sided p = 0.0625). So hybrid is validated on real use next.
- [x] Real-chat comparison (shadow): every hybrid recall also records what keyword alone would return (trace field shadow_keyword, never context). Run map says "keyword alone would differ (comparison only)". tests/test_hybrid_memory.py TestShadowKeyword.
- [ ] Decide after 30 real follow-ups (rule pre-registered in data/_memory_shadow_report.py): judge disagreements blind (sheet data/memory_shadow/judging.csv, A/B/both/neither); keep hybrid only if hybrid wins - keyword wins >= 3. Test questions and traces before 2026-11-08 13:30 are excluded. Judge is ClickUp Brain unless Baris fills verdicts.
- [x] Explicit user memory writes ("remember this") and a delete/forget control: BUILT 2026-11-08, steps 1 to 6 below. Spec: docs/designs/remember_forget_draft.md, reviewed 2026-11-08 by two fresh-context reviewers (elephant-goldfish pattern, same model family, so conversation bias removed, not model bias). Round 1 found 4 blockers (no item identity, no tombstone filter on any path, post-run write could undo a forget, no worker delete op); all resolved in the spec. Build order and riskiest step (Hy-Memory delete) recorded there.
  - [x] Steps 1 to 4 built 2026-11-08 (approved): ledger core/memory_items.py (one id and state per item, tombstones, request ids, one mutation lock); one tombstone filter on every recall path (selected backend, keyword fallback, keyword shadow, sync, previous answer, delayed post-run write); notes (500 chars, deterministic ticker scan, 50 per conversation, untrusted delimited block in context); forget with tombstone-first, delete_pending and retry; Hy-Memory worker delete (kind=chat). Verified by reading: number allowlist and wording check use only accepted reports, never context. tests/test_memory_items.py (10, plus a live Hy-Memory delete test that passes with COFINANCE_HYMEMORY_TESTS=1).
  - [x] Regression caught by the suite while building, fixed: importing the ledger's ACTIVE shadowed the job-status ACTIVE in chat_service, which silently broke Stop. Now aliased (ITEM_ACTIVE).
  - [x] Steps 5 and 6 built 2026-11-08: forget-all (one conversation only, same tombstone-first order, idempotent per request id, reaches turns the ledger never saw); API GET /api/memory, POST /api/memory/note and /api/memory/forget (item_id "*" = all), same-origin + CSRF, outside the service-wide lock; UI: brain button in the chat top bar opens the Memory panel (list with state per item, Remember a note, Forget, Forget all with a confirm step), "Forget from memory" under each answer, run map and answer footer count notes apart from turns. Tests: tests/test_memory_items.py now 23 (forget-all, view, API round trip, bad requests, refusal during a run, duplicate concurrent notes, semantic outage), frontend/src/lib/memory.test.ts (6).
  - [x] Acceptance tests 2 and 9 run end to end with a control: a note holding instructions, a company name and a number reaches the narrator only in the untrusted block; its number fails the number guard and its company name fails the wording check, while the same sentence without them is accepted. (First version of that test had no control and would have passed for the wrong reason: the stub evidence never contains the ticker, so every reply failed. Caught and fixed.)
  - [x] Browser check in real Chrome (data/_memory_ui_check.py, desktop 1440 and mobile 390): add a note, forget it, forget all; server state verified after each step; chat history unchanged (6 messages before and after). Found and fixed: "Forget all" rendered at 12.8px (14px floor) and the dialog close button was 28px on mobile (now 44px). Live follow-up in the fully forgotten conversation: data/_forgotten_followup_check.py.
  - [x] Fresh-reviewer CODE review 2026-11-08 (two research-mode reviewers with no conversation context, same model family; they read excerpts only and ran nothing). 13 findings, deduplicated. FIXED with tests (TestCodeReviewFindings, 7): forget-all now reaches keyword rows and vector links the ledger never saw (older than the visible message window) by adopting them as ledger items, and reopens a forgotten item whose copy survived; request ids are refused when reused for another conversation or action, and the repeat check is re-done inside the lock (concurrent twins); a repeated note request replays its stored note even after a run started; the same note text twice is one note; Hy-Memory run-id lookup is scoped to the conversation; item ids are validated. NOT ADOPTED, with reasons: forget racing a run that starts just after the check (a turn answered after the forget is new memory, and an answer finished before it is registered from history, so nothing forgotten is revived); zero deleted_count counted as failure (a retry after a crash between the worker delete and the link delete would stay pending forever, so zero is treated as already gone; live semantics of a second delete not verified); prefix routing (unknown /api/memory paths already return 404). ACCEPTED LIMITS: a crash in the microseconds between the worker add and the links insert can leave an unlinked vector copy of a note (turns are found by run id); slow backend calls run under one ledger lock, so another conversation's note waits (single local user); a visible turn the ledger has not registered yet shows as active until the next question registers it.
  - [x] Fix pass 2026-11-08 (later). Notes are now PINNED: kept in the ledger only (no search copy, so a note never starts the embedder), added to every question about their assets, or to every question when they name none (asset-free notes now allowed), newest first, at most 5 and 600 characters. Turn vector writes run outside the ledger lock with a conditional commit; a forget that lands mid-write deletes the fresh copy. Forget-all also sweeps the vector store for that one conversation (worker delete_session, refuses an empty id); a failed sweep is stored, shown in the Memory panel and retried. LIVE finding: a repeat Hy-Memory delete returns success False, deleted_count 0, so a retry after a crash would have failed forever; delete now asks the worker (new exists op, get() returns None when gone) and finishes when the copy is gone. Live tests (COFINANCE_HYMEMORY_TESTS=1): the sweep removes linked and unlinked copies of s1 and leaves s2; the crash-retry completes. Favicon inline (CSP already allows data: images). Tests: test_memory_items.py +5, live +1, memory.test.ts +2.
  - [x] Apple news fallback FIXED at the cause: all 5 live "latest Apple news" runs fell back on wording, not numbers ("Q4" and "earnings" for the headline "Apple reports fourth quarter results"). core/claims.py now accepts standard shorthand only when looking up the evidence (Q1-Q4 for written quarters, earnings for a results headline); revenue, profit, guidance and beat stay separate claims. data/_claim_eval.py still 10/10 wrong caught, 0/6 real rejected. Not yet confirmed on a live narrator run.
  - [x] Pre-gate speculation (core/pregate.py speculation_reason, Clock 1): prediction-market sources, market-odds text, and yes/no title questions with a deadline or price line become IGNORE "speculation:..." with a run count; reported question headlines still go to the judge. Probe: fixture Polymarket item and "Will NVDA close above $200 on Friday?" flagged; "Can Apple iPhone revive sales? Analysts weigh in", "Will Nvidia China ban hit earnings?" and the corpus speculation trap not flagged. Only 1 of 131 real headlines is a question, so real-data evidence is thin. Dedicated unit tests still to add.
  - [ ] Known limits (old line, mostly resolved above; still true: repair of a missing vector copy waits for the next turn): no asset-free notes (an asset must be named in the note or already in the conversation); the first note of a session can take several seconds while the embedder starts (the UI retries with the same request id, so it never doubles); repair of a missing semantic copy happens on the conversation's next turn, there is no manual repair button; /favicon.ico returns 404 (cosmetic, predates this work).
  - [x] README.md rewritten with a v2 section (what it is, rules, run, two clocks, models, memory, what is not done, checks, layout); the v1 Streamlit text is kept below as an archive. frontend/README.md corrected (it still said Hy-Memory was unfinished) and given the memory routes.
- [x] New-source probe 2026-11-08 (data/_social_probe.py, approved): last30days preflight clean (cookies off, no local writes). Reddit keyless: "auth-failed", 0 rows. StockTwits keyless: HTTP 403. FAIL, not adopted. Enabling them would need logins or browser cookies, which we do not use. Trap gap (18/20) still open.
- [ ] Keep full conversational retrieval distinct from the current asset-only follow-up behavior.
- [ ] Verify deletion/export/recovery before any migration of legacy history.

### 6. Interactive product: visual execution verified, broader integration partial

- [x] Chat, new conversations, saved history, run status, Stop, evidence links, and execution links.
- [x] Model picker separates the routing executive from the shared base narrator.
- [x] Download, integrity, qualification, load/unload, and failure states are separate.
- [x] Deliver a clickable run map covering routing, dispatch, tools, agents, narrator, guards, and accepted output.
- [x] Explain Spidermonkey/Jev as an adopted gating pattern, not a running external service.
- [x] Show rejected numeric tokens, candidate output, and the fallback actually used.
- [x] Show absent, not-dispatched, failed, fixture, and replay components without invented activity.
- [x] Recheck keyboard/focus, small widths, polling state, and source guards after the visual changes.
- [x] Run full regression and real-browser acceptance on the final running build; the known legacy watchlist-test failure remains separately tracked below.
- [x] Isolate the legacy memory test and repair the new-chat test dependencies; the isolated full suite passes without further legacy database changes.
- [x] After explicit approval, back up the legacy memory database and remove only the five newly appended synthetic rows; verify all other rows and schema unchanged.

## This iteration's acceptance gate

A user can open a saved answer, expand its process map, select the decision model,
see the classified intent and score, select the narrator role, compare candidate
output with the guard decision, and identify the accepted fallback. A news replay
shows its own source-to-signal path, never a fake operational chat pipeline.

Run-map statuses must come from actual spans. An absent component is labelled as
absent, not as success. No private chain-of-thought is exposed or invented.

## Next recommended sequence

1. Preserve visual execution and cancellation regressions while closing the remaining integration gaps.
2. Complete operational news and missing data-provider coverage.
3. Human labels, held-out calibration, and stronger narrator/provenance gates.
4. Hy-Memory lite adapter and explicit memory UX.
5. Release/migration decision only after evidence supports the original gates.

Do not copy unsafe v1 behavior back for feature parity: arbitrary model-authored
code execution, dummy-price fallbacks, silent watchlist writes, or hosted LLM
fallbacks are not part of the v2 goal.

## Run and verify locally

Start: double-click `run-dashboard.cmd`, or `python -E -B -m ui.dashboard --port 8878`.
The interactive runtime needs installed ML packages; do not use `-S` for that server.

Frontend: `cd frontend`, then `npm test`, `npm run build`, and `npm audit`.
Backend checks live in `tests/`; browser tests use installed Chrome/Playwright and
require `COFINANCE_BROWSER_TESTS=1`. Real-model smoke runs are separate from fixtures.

Legacy UI and databases stay preserved. Nothing here authorizes Git actions.

## Verification note for this iteration

The v2 regression suite, browser map interactions, final frontend build, package audit,
small-width layout, focus/disclosure preservation, and real saved-run map acceptance
were exercised. The pre-existing new-chat test now patches the dependencies actually
used by the UI, and the memory roundtrip test uses a temporary database. The isolated
full legacy suite passes; its opt-in browser tests are covered by the separate browser run.

The cancellation startup race found during regression was repaired with monotonic
job transitions and cancellation/final-result serialization. The first full legacy run appended synthetic test rows to the old memory database.
The isolated rerun left both legacy database checksums unchanged. After explicit approval,
a verified backup was saved under data/backups and only the five new synthetic rows
were removed. All earlier rows, schema, and the watchlist were verified unchanged;
the local receipt is data/legacy-test-cleanup-receipt.json. Numerical validation remains conservative and may reject dates
or label numbers; the map now makes those rejected tokens visible instead of hiding
the fallback. No financial-accuracy or complete-product claim follows from these checks.

## Progress log, 2026-11-08

- [x] Fixed: Yahoo `chartPreviousClose` (close before the 1y window) was shown as "previous close". Now `close_before_window`, never narrated; `previous_session_close` comes from the daily series.
- [x] Narrator input is labelled facts only (no timestamps, sources, status strings, digit-bearing labels). Allowlist limited to values shown to the narrator.
- [x] Readable deterministic fallback instead of `key=value`.
- [x] Clock 1 operational collection over the registered official feeds (Apple, NVIDIA): manual only, traced, writes `data/news.db`, no scheduler or notifications. `python -m core.clock1 --feeds` or Dataset > Collect now.
- [x] Chat answers carry a deterministic news coverage note and structured report parts.
- [x] Linear pass: one contextual sidebar list, single chat scroll region, docked composer, facts block, quiet sources, news panel, hover-only chart toolbar.
- [x] Acceptance run 2026-11-08 04:37 (live dashboard API, LFM3 narrator): official-feed collection (40 headlines: 16 digest, 24 ignored, 0 alerts; re-run deduped all 40), NVDA thesis + follow-up, reload, traces. Second run: 6 of 6 narrations passed the number check, 0 fallbacks. First run found two bugs, fixed: news narration cut off at its token limit; follow-up never saw the question.
- [x] Wording check (core/claims.py): names and claim words must appear in the evidence each narrator saw. Eval (data/_claim_eval.py): 10/10 hand-written wrong cases caught (written knowing the rules, so optimistic); run-2 narrations 3/6 rejected, all for real unsupported claims ("chips" x2, "high volume"); after prompt steering, run-3 and run-4 narrations 12/12 passed. Known limit: paraphrase without new names or claim words.
- [x] Follow-ups answer the question (run 4: two sentences, no restated prices).
- [x] Memory run 2026-11-08 05:09 (live API, LFM3): NVDA thesis -> Apple news -> "Back to NVDA, summarize your earlier view". Turn 3 recalled turn 1 and skipped the latest turn; answer matched the turn-1 view; number and wording checks passed. Turn 3 still restated prices.
- [x] Bug found by that run, fixed: "latest Apple news" got no NER entity and silently reused NVDA. Python now backstops with a whole-word alias scan (tests/test_router_aliases.py), and reusing conversation assets is a visible trace decision. Live re-run 05:46: Apple turn routed to AAPL, 3/3 narrations accepted, turn 3 recalled turn 1.
- [x] Found by the re-run, fixed: the Apple answer borrowed NVDA's "execution gaps" risk from the previous answer. Memory now passes context only from turns about the same assets (tests/test_memory_port.py).
- [x] Labels workbench (Dataset view, qualify/label_store.py): blind (no model scores), writes the same label.csv that finalize validates, same rules, atomic writes, one-time backup label.before-ui.csv, read-only if the corpus hash changes. Browser check: desktop and mobile, no overflow, 14px floor.
- [x] Bigger pool 2026-11-08 06:03 (data/_collect_corpus_v2.py): data/news_corpus_20261108, 131 candidates (91 HN via pinned last30days, HN only, no web backend or cookies, 30-day cap; 40 official feed items). 89/91 HN rows name the company. Below the 150 target I set; the dashboard now opens the newest corpus. Old corpus untouched.
- [x] All 131 labelled 2026-11-08 by ClickUp Brain (Claude Opus 5.5) at Baris's request: MODEL labels, not human. Blind (no GLiNER scores; labels written before scoring). Rubric and provenance in data/news_corpus_20261108/label_provenance.json; script data/_brain_labels_20261108.py. Result: 26 material, 13 urgent, 18 traps, split 91/40. Gate fails only on traps (18 < 20); no trap was stretched to pass.
- [x] Finalize and calibration record label provenance; model labels are never reported as human. Rows a person saves in the Labels view are marked [human reviewed].
- [x] Calibration tool (qualify/calibrate.py, tests/test_calibrate.py): blind GLiNER scoring offline (131 rows, 178 s, revision 9d1bfb8), thresholds from train only, one held-out look per labels+scores hash. Provisional run 2026-11-08 (model labels, 18 traps), report in data/calibration_20261108/report.json.
- [x] Finding: calibrated thresholds (digest 0.55, alert 0.55 + urgent 0.20) FAILED held-out: digest F1 0.00 vs current 0.40 (held-out has only 5 material, 1 urgent). Not adopted; GateConfig unchanged.
- [x] Finding (train only): material score ranks reasonably (AUC 0.79) but is compressed to 0.15-0.66, so the current alert line (material >= 0.85) can never fire. The urgency head is inverted against the labels (AUC 0.37). Traps score below the median (0.34), so they are not inflating materiality here.
- [x] Rule urgency candidate (core/urgency.py): results/guidance, market-value moves, production stops/recalls, trade blocks; scheduling notices excluded; HN comment text ignored; each hit names its rule and matched words. Train split (optimistic, rules written after reading these headlines): 11/12 urgent caught, 0 false alarms vs 0/12 for the current GLiNER path (data/calibration_20261108/urgency_rules_train.json). Unseen hand-written probe: 9/9 urgent, 7/7 routine (tests/test_urgency.py). Known miss kept: "The Tesla Cybertruck Is Dead".
- [x] Shadow mode: every judged headline in a news collection records shadow_rule_urgency (would_be_urgent, rule, matched words, material score) on its gate event, plus a run total; actions are unchanged (tests/test_news_service.py). Run map gate stage shows "shadow rules: N would be urgent, no action changed". Live check 06:5x: works; 0 judged because all 40 feed items were already seen. Aggregate: python data/_shadow_report.py.
- [x] Wired live (2026-11-08): `GateConfig.urgency_source="rules"` is the Clock 1 default. A rule hit alerts at material >= digest_p (0.50) and still digests below it (`rule_urgent_low_material:<rule>`); cooldown and cap unchanged. Model mode stays available for comparison. Found while wiring: rule mode bypassed the cascade's "adjudicator down cannot alert" cap (it only lowered `urgent`), so `Judgment.alert_allowed` now enforces it in both modes (tests/test_signals.py TestRuleUrgency, 8 tests).
- [ ] Held-out check of the live rules (shipped before this check because the model path could never alert). Fresh held-out test: on or after 2026-11-15, collect again with data/_collect_corpus_v2.py into a new folder, drop IDs already in news_corpus_20261108, label blind, evaluate rules once. Pass: recall >= 0.7 and <= 1 false alarm per 10 urgent-labelled rows.
- [ ] Human spot-check of model labels (a random 20 rows would bound label error).
- [ ] 2 more real traps (needed for a final, non-provisional calibration).
- [ ] Calibration script: thresholds on train only, one held-out evaluation, refuse a second held-out run on the same split.
- [x] Fixed: memory connections were never closed (Windows file locks); now one closed connection per operation.
- [x] Narrator choice survives restarts (core/local_models.py restore_choice): stored as `preferred`, restored only if the model file has the pinned size and qualification still matches the fingerprint; otherwise tool-only with a visible reason. Restores the choice, never a loaded process; load() re-verifies SHA-256 on the next run. A user Unload still switches to tool-only. tests/test_narrator_persistence.py (7). Live 2026-11-08: select LFM3 via API, restart, state shows LFM3 selected, not loaded, "Restored from the last session".
- [ ] Bitcoin and regulatory news sources (need approval). Ongoing scheduler (needs approval).
- [ ] MemoryPort / Hy-Memory lite, labels and calibration, fundamentals coverage.
