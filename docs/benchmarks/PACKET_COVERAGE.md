# Can a search result certify the whole supplied packet?

> [!WARNING]
> Experimental. Twelve authored contrast packets in two related families, cash floors and service uptime. This is a mechanism test, not general research accuracy or a head-to-head test of a live answer model. [Compact results](packet-coverage-v1.json).

**Decision:** use search to find a starting point, not to define the scope of a conclusion. In a bounded packet, checking every supplied page can overturn an apparently well-cited answer. If the scope cannot be checked, return a coverage limitation instead of claiming that the documents lack evidence.

## Comparison

The [frozen runner](../../qualify/packet_boundary_lab.py) used the actual document ranker and the same research receipt verifier for all three arms. Twelve questions each had four short documents; six perturbation types per family included an unrelated record, a withdrawal, a second signed value, a pending qualification, an amendment or limited time period, and missing matching scope. A duplicate policy creates a realistic ranking distraction. Expected states and decisive reference requirements were frozen before scoring. No model, provider, or user document was involved.

| Scope given to verifier | Expected status + required source IDs | Unsupported `VERIFIED` | Not evaluated |
| --- | ---: | ---: | ---: |
| Treat the three ranked pages as the entire packet | 6/12 | 6 | 0 |
| Keep the four-page inventory but provide only three pages | 0/12 | 0 | 12 |
| Check the four supplied pages against the same inventory | 12/12 | 0 | 0 |

The first row is a deliberately unsafe comparison condition, **not a measurement of the live dashboard**. The middle row did not solve any decision: silence is not a correct answer. The whole-packet score includes four warranted `UNINTERPRETED` outcomes; eight outputs made a supported evidence claim. Source IDs and all cited spans were checked against the complete fixture pages. The score does not assess prose quality.

On the Aspen fixture, the signed record was below the floor. The lower-ranked fourth document explicitly superseded it with an amount above the floor. Checking only the first three gave **missed**; checking all four gave **met** with both the amendment and superseded record. On Iris and Holly, the omitted item was a withdrawal; the three-page result incorrectly stayed definitive. Six of twelve shortlist results failed in the frozen input order. Reordering four documents in all 24 ways left all 288 complete-packet results identical. This post hoc stress check does not turn permutations into independent cases.

## Guardrails and limits

An inventory pins document identity, names, page count and the hash and order of each complete extracted page. It must be made from the authoritative supplied collection, never reconstructed from search results. Changed, missing, duplicated or unavailable pages and exhausted bounds (default 24 pages or 120,000 characters) cannot yield a packet-wide evidence conclusion. `NOT_EVALUATED` describes coverage of the request, not a fifth claim about what happened in the world.

The complete arm had access to more pages; this is not a compute-matched model test. The screen does not prove sources are authentic, that nothing relevant exists outside the supplied packet, that an index of a large collection is complete, or that complex layouts are semantically understood. Seven unit tests include stale hashes, ordering and both budget limits. Five deliberately broken checks failed as expected. Frozen code `a0ff45c`, protocol prefix `d7d17171`. Use a new directory with `python -B -m qualify.packet_boundary_lab freeze <new_dir>`, then `run <new_dir>`.

## PDF evidence prerequisite

The local upload implementation now retains bounded original extracted text in addition to the existing normalized search representation. On three independently authored, locally installed software manuals, all six selected excerpts mapped exactly back to the original extracted page. All six pages rendered; a five-word midpoint phrase matched the independent Poppler word stream on five. The mismatch was a spatial diagram with a different reading order. This is not a pixel-level proof of every quote. Separate controlled PDFs tested a linked withdrawal footnote, an actual amendment on another page, a cross-page contradiction and an unbound table that must not get a verdict. Older normalized-only or over-limit pages are not upgraded into exact witnesses. No original PDF bytes, new dependency, OCR, or external service is needed by the fix.
