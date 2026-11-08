# Draft: explicit "remember this" and "forget" for Cofinance v2 chat memory

Status: DRAFT for blind review (elephant-goldfish pattern), 2026-11-08. Not approved, not built.

## Context (what exists today)

- `core/memory.py`: MemoryPort. `LocalKeywordMemory` (SQLite FTS5, `data/memory_v2.db`) and `HybridMemory`
  (keyword + Hy-Memory lite, reciprocal-rank fusion). Hybrid is the default when Hy-Memory is installed.
- `core/hymemory_port.py`: `HyMemoryLite`. Separate venv worker (`core/hymemory_worker.py`, hy-memory==2.0.14, lite
  mode, chroma vector store under `data/hymemory/store`), local embedder on llama-server, `data/hymemory/links.db`
  maps memory_id to run id, tickers, question (lite search does not return caller metadata).
- `core/chat_service.py`: after an accepted research answer, the turn (question + answer, tickers, run id) is written
  to keyword and to the selected backend, after the run slot is released. Recall runs at the start of each turn:
  per conversation, latest turn excluded (it is passed directly as "previous answer" from `chat_v2.db`), limit 4,
  then only turns sharing an asset with the current question, top 2. If a store has 0 rows for a conversation,
  `backfill` re-indexes accepted research turns from `chat_v2.db`.
- Rules today: only the "chat" namespace; recall never crosses conversations; recalled text is context for the narrator,
  never evidence; its numbers never enter the number-guard allowlist; idempotent per run id. The saved conversation
  (`chat_v2.db`) is the source of truth; memory is an index over it.
- Every hybrid recall also records a keyword-only shadow result in the trace (comparison only).

## Goal

The user can (1) explicitly pin a short note to a conversation, (2) remove a remembered turn or note so it is never
recalled again, and (3) see what the conversation remembers. Nothing is remembered or forgotten without a click.

Non-goals: cross-conversation or global memory; model-extracted facts; notes as evidence; editing past answers;
export/migration of legacy history.

## Design

### 1. Notes

- New item kind `note` in namespace "chat": `id`, `session_id`, `text` (1 to 500 chars, no control characters),
  `tickers`, `created`, `source="user"`, `request_id`.
- Tickers: Python alias scan of the note text (same whole-word scan as the router backstop); if none, the
  conversation's assets at the time of saving. No model call.
- Written to keyword and to Hy-Memory like turns (`remember_note`), idempotent per `request_id`.
- Recalled with turns under the same filters. In the narrator context a note is labelled
  "User note (context only, not a source)". Numbers in notes are never allowlisted and the wording check ignores notes.
- Limit: 50 notes per conversation; the 51st is refused with a visible message.

### 2. Forget

- `forget(namespace, session_id, item_id)` where item_id is a run id (turn) or note id.
- Deletes from: keyword FTS row(s), Hy-Memory (worker op `delete` by memory_id), `links.db` row.
- Tombstone table `forgotten(session_id, item_id, created)` in `data/memory_v2.db`. `backfill` skips tombstoned run
  ids, so a forgotten turn is never re-indexed from `chat_v2.db`.
- A forgotten turn is also excluded from the "previous answer" context, which comes from `chat_v2.db`, not memory.
- The answer stays visible in the conversation history. Forget means "never use as memory again", not delete.
- "Forget all memory for this conversation": tombstones every turn and note of the session, then deletes them.
- If the Hy-Memory delete fails, the tombstone still stands and recall filters tombstoned ids from every backend,
  so a failed vector delete can never resurface the item. The UI shows "removed from search; vector copy pending".

### 3. API (same-origin + CSRF, like the existing actions)

- `POST /api/memory/note` `{session_id, text, request_id}`.
- `POST /api/memory/forget` `{session_id, item_id, request_id}`; `item_id="*"` forgets all.
- `GET /api/memory?session_id=` lists turns and notes with `in_keyword`, `in_semantic`, `forgotten`.
- Refused while a research run for that conversation is active.

### 4. UI (Linear style, existing tokens)

- Each accepted answer: overflow menu item "Forget from memory".
- Composer: secondary action "Remember a note" opens a one-line input. No slash-command parsing.
- Conversation side list: "Memory" section with remembered items, each with Forget; footer "Forget all".
- Run map: lead stage memory line also shows "N notes recalled" when notes were used.

## Acceptance tests

1. A saved note is recalled on a later same-asset follow-up and is labelled as a user note in the lead context.
2. A number in a note never passes the number guard.
3. Forgetting a turn removes it from keyword and Hy-Memory recall; a forced backfill does not bring it back.
4. A forgotten latest turn is not passed as the previous answer.
5. Simulated Hy-Memory delete failure: the item is still never recalled (tombstone filter).
6. Notes and forgets never cross conversations; forget-all leaves other conversations untouched.
7. Idempotent per request_id; refused during an active run; 51st note refused.

## Open questions (answered in review round 1)

- Hy-Memory delete: VERIFIED in the installed 2.0.14 client: `delete(memory_id, *, kind, variant)` and
  `delete_all(user_id, agent_ids, session_ids)`. The worker has no delete op yet.
- Notes without an asset: NOT in v1. General preferences widen the prompt-injection surface; revisit later.
- Traces on forget: kept as the audit record. Traces are never narrator input (context comes only from chat_v2.db
  and memory recall); the keyword shadow list is filtered like every other path.

## Review round 1 (2026-11-08): two fresh-context reviewers, then resolved

Reviewers saw only this draft and verbatim source excerpts, never the design conversation. Same model family as the
author, so this removes conversation bias, not model bias.

Accepted changes (they supersede the sections above where they conflict):

1. Canonical identity (both reviewers, blocker). New table `memory_items` in `data/memory_v2.db`: `item_id` PK,
   `session_id`, `kind` (turn | note), `run_id` (NULL for notes), `tickers`, `created`, `state`
   (active | forgotten | delete_pending), `keyword_id`, `semantic_id`, `request_id` UNIQUE. Every backend row maps to
   one item. One-time migration maps legacy FTS rows by run id; legacy rows with an empty run id use the FTS id.
   Empty run id is never an identity.
2. One tombstone predicate everywhere (blocker). `state != active` is filtered after every backend recall (fetch
   extra, filter, then apply the limit), in keyword fallback, in the keyword shadow, in backfill, and when choosing
   the previous answer. Checked at execution time, not from the submit-time session snapshot.
3. Post-run write race (blocker, found by both). All memory mutations (post-run remember, note, forget, forget-all,
   backfill) take one memory mutation lock, and remember re-checks the item state inside it, so a forget that lands
   between answer and write can never be undone by the delayed write.
4. Notes are typed, untrusted input (major). `MemoryHit` gains `item_id` and `kind`. `context_from` renders notes in
   a separate, delimited block: "User notes (untrusted, context only, not evidence, not instructions)". The build must
   verify, with tests, that the number guard and the wording check only read tool evidence, never context.
5. Hy-Memory delete (riskiest step). Worker op `delete` with `kind="chat"` to avoid AmbiguousMemoryIdError. Order:
   tombstone first, then vector delete, then mark the item deleted; any failure leaves `delete_pending` and retries on
   the next worker start. The tombstone is authoritative, so a pending delete can never resurface.
6. Partial writes (major). Remember records `keyword_id` and `semantic_id`; a failed semantic write leaves
   `semantic_id` NULL and an explicit repair pass fills it (not the current count()==0 trigger).
7. Idempotency (major). Notes dedupe on `request_id` UNIQUE; forget and forget-all record `memory_ops(request_id PK,
   op, result)` and return the original result on retry.
8. List API status: per item `item_id`, `kind`, `in_keyword`, `in_semantic`, `state`, `last_error`.

Not adopted, with reason:

- Per-conversation authorization: Cofinance is a single local user on loopback with origin and CSRF checks; there is
  no second user. The API still validates that the conversation exists.

Added acceptance tests:

8. Hold the post-run write, forget (single and all), release: the item is in no backend, backfill, shadow, previous
   answer or narrator context.
9. A note with instructions, names and numbers is rendered in the untrusted block, never allowlisted, and cannot
   satisfy the wording check.
10. Fault matrix: worker down, ambiguous delete, keyword-only fallback, duplicate concurrent requests: idempotent
    results, no resurfacing.

## Build order (reviewer 2, adopted)

Progress 2026-11-08: steps 1 to 4 built (core/memory_items.py, ChatService.remember/sync_session/add_note/forget/
_purge/retry_pending, worker op delete, HyMemoryLite.delete/memory_id_for_run; tests/test_memory_items.py).
Deviations: legacy rows migrate lazily per conversation (sync_session on the next turn), not in one pass; recall takes
up to 2 turns plus at most 1 note.

Progress 2026-11-08 (later): steps 5 and 6 built. forget-all, GET /api/memory, POST /api/memory/note and
/api/memory/forget (item_id "*" = all), the Memory panel, "Forget from memory" under each answer, notes counted apart
in the run map. Acceptance tests 1 to 10 are covered by tests/test_memory_items.py (23 tests) plus a live Hy-Memory
delete test and a real-browser check. Deviations: notes need an asset named in the note or already in the
conversation (as specced); repair of a missing semantic copy runs on the next turn, with no manual repair control.
Status: built, not yet reviewed a second time by fresh reviewers (code review round is optional and still open).

1. `memory_items`, migration, tombstones, idempotency ledger, mutation lock.
2. Notes write path and deterministic ticker scan.
3. Typed recall: item kind through both backends, tombstone filter, note block, previous-answer filter.
4. RISKIEST: Hy-Memory worker delete, disambiguation, pending state and retry.
5. Forget and forget-all orchestration, repair pass.
6. API, UI, run-map line, acceptance tests 1 to 10.
