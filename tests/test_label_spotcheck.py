import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from qualify import label_spotcheck as ls
from qualify.news_dataset import FIELDS, FORMAT as CORPUS_FORMAT, digest


def _make_corpus(folder: Path, n: int = 24) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    candidates = []
    label_rows = []
    for i in range(n):
        rid = f"row-{i:03d}"
        candidates.append({
            "id": rid,
            "ticker": "NVDA" if i % 2 == 0 else "AAPL",
            "title": f"Headline number {i}",
            "text": f"Body text for headline {i}",
            "url": f"https://example.com/{i}",
            "timestamp_kind": "published",
            "timestamp": "2026-11-08T00:00:00Z",
            "source": "company_release",
        })
        if i % 4 == 0:
            mat, urg, d, tr = "false", "false", "neutral", "denial"
        elif i % 4 == 1:
            mat, urg, d, tr = "true", "true", "bullish", "none"
        elif i % 4 == 2:
            mat, urg, d, tr = "true", "false", "bearish", "none"
        else:
            mat, urg, d, tr = "false", "false", "neutral", "none"
        label_rows.append({
            "id": rid,
            "ticker": candidates[-1]["ticker"],
            "title": candidates[-1]["title"],
            "text": candidates[-1]["text"],
            "url": candidates[-1]["url"],
            "timestamp_kind": "published",
            "timestamp": "2026-11-08T00:00:00Z",
            "material": mat,
            "urgent": urg,
            "direction": d,
            "trap": tr,
            "event_group": f"ev-{i}",
            "notes": f"SECRET_MODEL_NOTE_{i}",
        })
    cand_bytes = "".join(json.dumps(c) + "\n" for c in candidates).encode("utf-8")
    (folder / "candidates.jsonl").write_bytes(cand_bytes)
    (folder / "manifest.json").write_text(
        json.dumps({"format": CORPUS_FORMAT, "candidate_sha256": digest(cand_bytes), "rows": n}),
        encoding="utf-8",
    )
    with (folder / "label.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(label_rows)


class TestLabelSpotcheck(unittest.TestCase):
    def test_prepare_is_blind_stratified_and_score_computes_kappa_and_disagreements(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "corpus"
            _make_corpus(cdir, n=24)

            prep = ls.prepare_spotcheck(cdir, sample_size=20, seed=20261126)
            self.assertEqual(prep["sample_size"], 20)
            self.assertEqual(prep["strata_counts"], {
                "clean_non_material": 5,
                "non_urgent_material": 5,
                "trap": 5,
                "urgent_material": 5,
            })

            with self.assertRaises(FileExistsError):
                ls.prepare_spotcheck(cdir, sample_size=20, overwrite=False)

            blind_raw = (cdir / ls.BLIND_FILENAME).read_text(encoding="utf-8-sig")
            self.assertNotIn("SECRET_MODEL_NOTE_", blind_raw)
            blind_rows = list(csv.DictReader(io.StringIO(blind_raw)))
            self.assertEqual(len(blind_rows), 20)
            for r in blind_rows:
                for lf in ("material", "urgent", "direction", "trap", "event_group", "notes"):
                    self.assertEqual(r[lf], "")

            with self.assertRaisesRegex(ValueError, "Spot-check incomplete"):
                ls.score_spotcheck(cdir)

            key = json.loads((cdir / ls.KEY_FILENAME).read_text(encoding="utf-8"))
            filled = []
            for idx, r in enumerate(blind_rows):
                m = dict(key["reference_labels"][r["id"]]["model_label"])
                if idx == 0:
                    m["material"] = "false" if m["material"] == "true" else "true"
                    m["urgent"] = "false"
                filled.append({**r, **m})

            with (cdir / ls.BLIND_FILENAME).open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(filled)

            scored = ls.score_spotcheck(cdir)
            self.assertEqual(scored["n"], 20)
            self.assertEqual(scored["human_completed"], 20)
            self.assertEqual(scored["metrics"]["material"]["matches"], 19)
            self.assertEqual(scored["metrics"]["material"]["agreement"], 0.95)
            self.assertGreater(scored["metrics"]["material"]["cohens_kappa"], 0.85)
            self.assertEqual(scored["disagreement_count"], 1)

            tampered = list(filled)
            tampered[0] = {**tampered[0], "id": "forged-id"}
            with (cdir / ls.BLIND_FILENAME).open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(tampered)
            with self.assertRaisesRegex(ValueError, "IDs do not match"):
                ls.score_spotcheck(cdir)


if __name__ == "__main__":
    unittest.main()
