# Cofinance v1 (Archived)

This folder holds the archived Streamlit v1 prototype (`app.py`, `agents/`, `tools/`, `utils/`, `ui/`, `screenshots/`, and its legacy `tests/`).

Active Cofinance v2 code lives at the repository root (`core/`, `ui/`, `frontend/`, `qualify/`, `tests/`). v2 never imports anything from `v1/` (enforced by `tests/test_invariants.py`).

To run or test the archived v1 code directly, work from inside this directory so its relative imports and icon paths resolve unchanged:

```text
cd v1
streamlit run app.py
```
