# Cofinance Local Operations: Fresh Install, Preflight, Backup and Recovery

[Start here](../README.md) | [How it works](METHOD.md) | [Experiment index](SCORECARD.md) | [Dashboard setup](../frontend/README.md)

This guide covers reproducible local checks and state recovery without touching external hosts or hosted models. All state lives under ignored `data/`.

## Preflight checklist

Run before starting the dashboard or after changing Python, frontend, or model files:

```text
python dev.py preflight
python dev.py preflight --json
```

`dev.py preflight` runs offline and verifies:

1. Python 3.11+ and `zoneinfo` DST rules for `America/New_York`.
2. Frozen qualification SHA-256 hashes (`qualify/scorers.py`, `qualify/cases.jsonl`, `qualify/gates_narrator.json`).
3. `docs/feature-map.json` schema presence.
4. `PRAGMA integrity_check` on every existing SQLite database (`chat_v2.db`, `news.db`, `memory_v2.db`).
5. Pinned `GLiNER2.5-Decide` local cache presence, plus SHA-256 and parity verification for the optional `encoder_fp32` ONNX cache when present.
6. Installed ML packages, Node/npm availability, built `frontend/dist/index.html`, `local_models/state.json` validity, and loopback port `127.0.0.1:8878`.

Hard integrity failures return exit code `1`. Fresh-install warnings (such as not-yet-downloaded local models or unbuilt `frontend/dist`) return exit code `0` with explicit `WARN` rows.

## Fresh install steps

1. Install Python 3.11+ dependencies and Node.js.
2. Build the React bundle cleanly from the lockfile:
   `cd frontend && npm ci --ignore-scripts && npm test && npm run build`
3. Run the headless verification suite:
   `python dev.py test`
4. Run `python dev.py preflight` and resolve any `FAIL` rows (and any `WARN` rows needed for local model inference).
5. Launch the local dashboard with `run-dashboard.cmd` and open `http://127.0.0.1:8878`.

## Online SQLite backup

Use `dev.py backup` rather than copying raw `.db` files directly (WAL mode leaves uncheckpointed frames in `-wal` sidecars):

```text
python dev.py backup
python dev.py backup --dest data/_backups/manual_snapshot
```

`backup` uses SQLite's online `Connection.backup` API for `chat_v2.db`, `news.db`, and `memory_v2.db`, copies `local_models/state.json` and any `news_corpus_*/label.csv` files, verifies `PRAGMA integrity_check` on every backed-up SQLite file, and writes a SHA-256 `manifest.json` (`cofinance-backup/1`).

## Verified recovery

Always dry-run first to verify every SHA-256 hash and SQLite integrity check before writing:

```text
python dev.py restore data/_backups/<snapshot> --dry-run
```

To restore into `data/`:

1. Stop the running dashboard at `http://127.0.0.1:8878` (the command refuses to overwrite live `data/` while the dashboard responds).
2. Run:
   `python dev.py restore data/_backups/<snapshot> --force`
3. `restore` verifies the full manifest first, writes a pre-restore safety backup under `data/_backups/pre_restore_<timestamp>`, atomically replaces each target file, clears stale `-wal` and `-shm` sidecars, and re-verifies every restored file's SHA-256 and SQLite integrity.
4. Re-run `python dev.py preflight` before restarting the dashboard.

## Offline verification tools

All verification harnesses run manually, offline, and read-only on saved state (no scheduler):

```text
python -B -m qualify.parity_oracle self-check
python dev.py outer-loop --fail-on-mismatch
python dev.py memory-eval
python dev.py spotcheck prepare
python dev.py spotcheck score
```

- `parity_oracle self-check`: runs the 26-case pairwise inner-loop differential oracle with replayed model outputs, 6 ordered stage checkpoints, and privacy-preserving diff fingerprints.
- `outer-loop`: replays saved local chat traces via fail-closed `StrictReplayLLM` with a socket network fence and privacy-preserving diff fingerprints.
- `memory-eval`: checks whether `data/chat_v2.db` has reached 30 real follow-ups (`waiting_for_data` below 30), audits `shadow_keyword` trace agreement, and verifies `LocalKeywordMemory` vs `HybridMemory` synthetic plumbing.
- `spotcheck`: draws a sealed, stratified 20-row blind CSV (`spotcheck_20_blind.csv`) without model labels for human review, then computes agreement, Cohen's kappa, balanced accuracy, and per-row disagreements once completed.
