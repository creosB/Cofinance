import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from core import gliner_onnx as go
from core import router_worker as rw


class FakeEncoding:
    def __init__(self, ids):
        self.ids = list(ids)


class FakeTokenizer:
    def __init__(self, *, omit=()):
        self.vocab = {
            "[PAD]": 0,
            "[CLS]": 1,
            "[SEP]": 2,
            go.SEP_STRUCT: 101,
            go.SEP_TEXT: 102,
            go.P_TOKEN: 103,
            go.C_TOKEN: 104,
            go.E_TOKEN: 105,
            go.R_TOKEN: 106,
            go.L_TOKEN: 107,
            go.EXAMPLE_TOKEN: 108,
            go.OUTPUT_TOKEN: 109,
            go.DESC_TOKEN: 110,
        }
        for tok in omit:
            self.vocab.pop(tok, None)

    def token_to_id(self, tok):
        return self.vocab.get(tok)

    def encode(self, text, add_special_tokens=False):
        if text in self.vocab:
            return FakeEncoding([self.vocab[text]])
        pieces = text.split()
        if not pieces:
            return FakeEncoding([])
        return FakeEncoding([200 + (sum(ord(c) for c in p) % 50) for p in pieces])


def _synthetic_heads(hidden: int = 4, *, count_target: int = 1):
    heads = {}
    for key in go.HEAD_KEYS:
        if key == "classifier.0.weight":
            w = np.zeros((8, hidden), dtype=np.float32)
            w[:hidden, :hidden] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key == "classifier.0.bias":
            heads[key] = np.zeros((8,), dtype=np.float32)
        elif key == "classifier.2.weight":
            w = np.zeros((1, 8), dtype=np.float32)
            w[0, 0] = 1.0
            heads[key] = w
        elif key == "classifier.2.bias":
            heads[key] = np.zeros((1,), dtype=np.float32)
        elif key == "count_pred.0.weight":
            heads[key] = np.zeros((8, hidden), dtype=np.float32)
        elif key == "count_pred.0.bias":
            heads[key] = np.zeros((8,), dtype=np.float32)
        elif key == "count_pred.2.weight":
            heads[key] = np.zeros((20, 8), dtype=np.float32)
        elif key == "count_pred.2.bias":
            b = np.full((20,), -5.0, dtype=np.float32)
            b[count_target] = 5.0
            heads[key] = b
        elif key == "count_embed.pos_embedding.weight":
            heads[key] = np.zeros((20, hidden), dtype=np.float32)
        elif key.startswith("count_embed.gru.weight_"):
            heads[key] = np.zeros((3 * hidden, hidden), dtype=np.float32)
        elif key.startswith("count_embed.gru.bias_"):
            heads[key] = np.zeros((3 * hidden,), dtype=np.float32)
        elif key == "count_embed.projector.0.weight":
            w = np.zeros((4 * hidden, 2 * hidden), dtype=np.float32)
            w[:hidden, hidden:] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key == "count_embed.projector.0.bias":
            heads[key] = np.zeros((4 * hidden,), dtype=np.float32)
        elif key == "count_embed.projector.2.weight":
            w = np.zeros((hidden, 4 * hidden), dtype=np.float32)
            w[:hidden, :hidden] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key == "count_embed.projector.2.bias":
            heads[key] = np.zeros((hidden,), dtype=np.float32)
        elif key.endswith("project_start.0.weight") or key.endswith("project_end.0.weight"):
            w = np.zeros((4 * hidden, hidden), dtype=np.float32)
            w[:hidden, :hidden] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key.endswith("project_start.0.bias") or key.endswith("project_end.0.bias"):
            heads[key] = np.zeros((4 * hidden,), dtype=np.float32)
        elif key.endswith("project_start.3.weight") or key.endswith("project_end.3.weight"):
            w = np.zeros((hidden, 4 * hidden), dtype=np.float32)
            w[:hidden, :hidden] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key.endswith("project_start.3.bias") or key.endswith("project_end.3.bias"):
            heads[key] = np.zeros((hidden,), dtype=np.float32)
        elif key == "span_rep.span_rep_layer.out_project.0.weight":
            w = np.zeros((4 * hidden, 2 * hidden), dtype=np.float32)
            w[:hidden, :hidden] = np.eye(hidden, dtype=np.float32)
            w[:hidden, hidden:] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key == "span_rep.span_rep_layer.out_project.0.bias":
            heads[key] = np.zeros((4 * hidden,), dtype=np.float32)
        elif key == "span_rep.span_rep_layer.out_project.3.weight":
            w = np.zeros((hidden, 4 * hidden), dtype=np.float32)
            w[:hidden, :hidden] = np.eye(hidden, dtype=np.float32)
            heads[key] = w
        elif key == "span_rep.span_rep_layer.out_project.3.bias":
            heads[key] = np.zeros((hidden,), dtype=np.float32)
    return heads


class RecordingSession:
    def __init__(self, hidden: int = 4, *, boost_pos=None):
        self.hidden = hidden
        self.boost_pos = boost_pos or {}
        self.calls = []

    def run(self, outputs, feeds):
        ids = feeds["input_ids"]
        mask = feeds["attention_mask"]
        self.calls.append((ids.copy(), mask.copy()))
        batch, seq = ids.shape
        out = np.zeros((batch, seq, self.hidden), dtype=np.float32)
        for b in range(batch):
            for s in range(seq):
                if mask[b, s] == 1:
                    out[b, s, 0] = float(self.boost_pos.get((b, s), 0.1 * (s + 1)))
        return (out,)


class TestStandaloneGlinerOnnx(unittest.TestCase):
    def _write_valid_cache(self, root: Path, *, ort_ver: str = "1.20.1"):
        onnx_path, heads_path, meta_path = go.standalone_paths(root)
        tok_path = root / "tokenizer.json"
        onnx_path.write_bytes(b"fake-preopt-onnx-bytes")
        heads_path.write_bytes(b"fake-heads-npz-bytes")
        tok_path.write_text('{"version":"1.0"}', encoding="utf-8")
        meta = {
            "revision": go.REVISION,
            "format": go.STANDALONE_FORMAT,
            "provider": go.STANDALONE_PROVIDER,
            "ort_version": ort_ver,
            "tokenizer_sha256": go.file_sha256(tok_path),
            "onnx_bytes": onnx_path.stat().st_size,
            "onnx_sha256": go.file_sha256(onnx_path),
            "heads_bytes": heads_path.stat().st_size,
            "heads_sha256": go.file_sha256(heads_path),
            "max_abs_err": 0.00012,
            "max_prob_diff": 0.00001,
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return onnx_path, heads_path, meta_path, tok_path

    def test_verify_standalone_cache_integrity_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onnx_path, heads_path, meta_path, tok_path = self._write_valid_cache(root)
            self.assertEqual(
                go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.20.1"),
                (onnx_path, heads_path, tok_path),
            )
            self.assertIsNone(go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.21.0"))

            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["revision"] = "bad-rev"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            self.assertIsNone(go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.20.1"))

            self._write_valid_cache(root)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["max_prob_diff"] = go.STANDALONE_MAX_PROB_DIFF + 0.01
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            self.assertIsNone(go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.20.1"))

            self._write_valid_cache(root)
            onnx_path.write_bytes(b"tampered-preopt-xbytes")
            self.assertIsNone(go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.20.1"))

            self._write_valid_cache(root)
            heads_path.write_bytes(b"tampered-heads-bytes")
            self.assertIsNone(go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.20.1"))

            self._write_valid_cache(root)
            tok_path.write_text('{"version":"2.0"}', encoding="utf-8")
            self.assertIsNone(go.verify_standalone_onnx_cache(root, tokenizer_path=tok_path, ort_version="1.20.1"))

    def test_batch_classify_padding_attention_mask_and_empty_text(self):
        sess = RecordingSession(hidden=4)
        runner = go.StandaloneGlinerOnnx(sess, FakeTokenizer(), _synthetic_heads(4))
        schema = {
            "intent": {"labels": {"price_snapshot": "Price info", "news_catalysts": "Recent news"}},
            "horizon": ["intraday", "swing_week"],
        }
        res = runner.batch_classify_text(
            ["", "NVDA price right now please"],
            schema,
            batch_size=4,
            include_confidence=True,
        )
        self.assertEqual(len(res), 2)
        for row in res:
            self.assertIn(row["intent"]["label"], {"price_snapshot", "news_catalysts"})
            self.assertIn(row["horizon"]["label"], {"intraday", "swing_week"})
            self.assertGreaterEqual(row["intent"]["confidence"], 0.0)
            self.assertLessEqual(row["intent"]["confidence"], 1.0)
        ids, mask = sess.calls[0]
        self.assertEqual(ids.shape[0], 2)
        self.assertEqual(mask[0, -1], 0)
        self.assertEqual(ids[0, -1], 0)
        self.assertTrue(np.all(mask[1] == 1))

    def test_extract_entities_zero_and_positive_count_and_validation(self):
        sess_zero = RecordingSession(hidden=4)
        runner_zero = go.StandaloneGlinerOnnx(sess_zero, FakeTokenizer(), _synthetic_heads(4, count_target=0))
        self.assertEqual(runner_zero.extract_entities("NVDA rises", ["ticker"]), {"entities": {"ticker": []}})

        sess_pos = RecordingSession(hidden=4)
        runner_pos = go.StandaloneGlinerOnnx(sess_pos, FakeTokenizer(), _synthetic_heads(4, count_target=1))
        out = runner_pos.extract_entities("NVDA rises", ["ticker"], threshold=0.5)
        self.assertIn("entities", out)
        self.assertTrue(out["entities"]["ticker"])
        self.assertEqual(runner_pos.extract_entities("   ", ["ticker"]), {"entities": {"ticker": []}})

        with self.assertRaises(ValueError):
            go.StandaloneGlinerOnnx(sess_pos, FakeTokenizer(omit=(go.L_TOKEN,)), _synthetic_heads(4))
        bad_heads = _synthetic_heads(4)
        bad_heads.pop("classifier.0.weight")
        with self.assertRaises(ValueError):
            go.StandaloneGlinerOnnx(sess_pos, FakeTokenizer(), bad_heads)

    def test_worker_prefers_standalone_without_loading_torch(self):
        sentinel = object()
        with mock.patch("core.gliner_onnx.load_standalone_onnx", return_value=sentinel) as loader:
            model, backend = rw._load_worker_model(Path("."), "auto", threads=6)
            self.assertIs(model, sentinel)
            self.assertEqual(backend, go.STANDALONE_FORMAT)
            loader.assert_called_once()

        fake_model = SimpleNamespace(encoder=SimpleNamespace(config={}))
        fake_gliner2 = SimpleNamespace(AutoExtractor=SimpleNamespace(from_pretrained=mock.Mock(return_value=fake_model)))
        fake_torch = SimpleNamespace(set_num_threads=mock.Mock())
        with mock.patch.dict("sys.modules", {"gliner2": fake_gliner2, "torch": fake_torch}), \
             mock.patch("core.gliner_onnx.load_standalone_onnx", side_effect=AssertionError("must not call standalone")) as loader, \
             mock.patch("core.router_worker.maybe_attach_onnx_encoder", return_value="pytorch_fp32"):
            model, backend = rw._load_worker_model(Path("."), "off", threads=6)
            self.assertIs(model, fake_model)
            self.assertEqual(backend, "pytorch_fp32")
            loader.assert_not_called()

        with mock.patch("core.gliner_onnx.load_standalone_onnx", side_effect=RuntimeError("corrupt standalone")):
            with self.assertRaises(RuntimeError):
                rw._load_worker_model(Path("."), "required", threads=6)

    def test_standalone_module_never_imports_torch_or_gliner2(self):
        import subprocess
        import sys
        code = (
            "import sys, core.gliner_onnx, core.router_worker; "
            "bad=[m for m in ('torch','transformers','gliner2') if m in sys.modules]; "
            "assert not bad, bad"
        )
        res = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
