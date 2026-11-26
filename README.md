# Cofinance

Cofinance is an experimental, local research and decision-support system. Finance is its first domain: ask about an asset, inspect the evidence behind the answer, and see where the system had to fall back. The person makes the decision. Cofinance never trades.

**The research question:** with the same small model and the same evidence, does a structured investigation produce a better conclusion than a good direct answer? We have measured speed, numerical grounding, and several finance-specific tasks. We have **not** established that the overall workflow beats direct Q&A. Medical, academic and everyday research are possible applications of the idea, not supported or validated deployments.

[How it works and how we test it](docs/METHOD.md) | [What we tried and what happened](docs/SCORECARD.md) | [Dashboard setup and API](frontend/README.md)

## Start

On a configured Windows machine, run `run-dashboard.cmd` and open <http://127.0.0.1:8878>. The launcher builds the React dashboard and starts the Python service. In Models, download, verify and qualify the default AtomicChat LFM3 1.7B narrator, or choose the optional base model. Collect news explicitly, then ask a question. Open the run map under the answer to inspect the tools, model calls, checks and fallbacks. Chat does not silently refresh news.

This is not a verified fresh-install recipe. The launcher requires Node and Python; the inference packages and pinned GLiNER cache must already be configured. Model downloads and market/news providers need network access. Model inference runs locally with a qualified Vulkan GPU or CPU runtime, not a hosted fallback.

## How it works

1. **Gather evidence.** Python fetches market data and stores accepted news. GLiNER routes questions and classifies headlines. Users can also attach text PDFs and inspect cited pages. Conversation memory provides context, not evidence.
2. **Build reports.** The Data Analyst explains tool outputs using the local narrator. The News Researcher is deliberately Python: it quotes and groups headlines rather than asking a model to paraphrase them. A news-only question can be answered without a narrator call.
3. **Check the answer.** On synthesis routes, the Team Lead combines reports. Number, wording and risk checks can reject a draft, permit a bounded retry or return a deterministic fallback. The trace distinguishes steps that ran from steps that were skipped. A retry is not an independent debate; matching a number is not proof of its interpretation.

Python owns tool use, policy and writes. The optional Deep think setting changes the final narrator, not the headline judge. Crowd odds and the experimental headline signal are clearly separate cards, not evidence for the narrator. There are no orders, wallets or automated investments. Local state, models, conversations and raw traces stay outside the public repo.

## What we measured

| Question | Finding | Read the evidence |
| --- | --- | --- |
| Can the answers be inspected and their numbers traced? | In a 72-case replay, all 393 numbers in final answers matched a tool value or served headline. A matching number can still be used incorrectly. | [Grounding report](docs/benchmarks/grounding-2026-11-17.json) |
| Does more model review improve decisions? | At equal coverage, quote-backed review got 35/62 historical calls right versus 36/62 for the unchanged judge. The cases were reused. | [Reviewed headlines](docs/benchmarks/REVIEWED_HEADLINES.md) |
| Does looking separately for restrictions and corrections help? | On 20 authored packets it recovered one more caveat than direct reading, but both missed three of four restrictions. Neither result justifies filtering out the other passages. | [Counterevidence screen](docs/benchmarks/COUNTEREVIDENCE.md) |
| Can the 1.7B narrator link an unnamed restriction to the exact claim it limits? | Not reliably on 24 matched notices: two-stage linking recovered 0/12 restrictions (10/24 malformed), and direct reading mislinked all 4/4 valid two-claim restrictions across both notices. | [Restriction-link screen](docs/benchmarks/RESTRICTION_LINK.md) |
| Does the document workflow beat direct Q&A? | Not on the 12-question document screen: 3 fully supported, correctly cited decisions versus 6 for direct Q&A. The workflow avoided three confident errors, but returned nine excerpt-only answers. | [Document decision gate](docs/benchmarks/DOCUMENT_DECISION_GATE.md) |

The [scorecard](docs/SCORECARD.md) indexes the experiments, including unsuccessful ideas. Nothing here establishes predictive edge or readiness for consequential decisions.

## Development

`core/` holds the active research pipeline; `ui/` and `frontend/` serve the local dashboard. `qualify/` contains gates and experiments. `tests/` contains regression and integrity checks. The Streamlit v1 code is historical.

```text
python -m unittest discover -s tests -t .
cd frontend
npm ci --ignore-scripts
npm test
npm run build
```

[Dashboard operations and security](frontend/README.md) | [Preflight, backup and recovery](docs/OPERATIONS.md) | [State diagrams](docs/statecharts.md) | [Memory design](docs/designs/remember_forget.md)

## History and license

v1 was a Streamlit app on hosted models. Its README was removed from the tree on 2026-11-18 and stays in git history (`git show 21cee11:README.old.md`); its code is kept, not migrated.

Please contact the author first to share or make changes anywhere. This work retains its existing [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International license](http://creativecommons.org/licenses/by-nc-sa/4.0/). Model and runtime components have their own licenses; see their pinned sources and local license copies.
