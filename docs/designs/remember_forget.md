# Explicit "remember this" and "forget" for Cofinance v2 chat memory

Historical design record. Explicit remember/forget shipped. For current controls, see the [dashboard guide](../../frontend/README.md); this page preserves the storage contract and acceptance criteria, not a backlog.


Status: **built and live** (2026-11-08, steps 1 to 6 below; tests in `tests/test_memory_items.py`). This page began as a draft for blind review (elephant-goldfish pattern); the review round and the build log are kept below as the design record. Where the review round conflicts with the first sections, the review round wins. A second fresh review of the built code has not been done.

## Context at design time

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
