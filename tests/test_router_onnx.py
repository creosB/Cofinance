import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core import router_worker as rw
from core.local_models import ModelError


class TestRouterOnnxCache(unittest.TestCase):
    def _write_valid(self, root: Path, payload: bytes = b'fake-onnx-fp32-bytes', max_err: float = 0.0001):
        onnx_path, meta_path = rw.onnx_paths(root)
        onnx_path.write_bytes(payload)
        meta = {
            'revision': rw.REVISION,
            'format': rw.ONNX_FORMAT,
            'bytes': len(payload),
            'sha256': rw.file_sha256(onnx_path),
            'max_abs_err': max_err,
        }
        meta_path.write_text(json.dumps(meta), encoding='utf-8')
        return onnx_path, meta_path

    def test_verify_accepts_valid_manifest_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onnx_path, meta_path = self._write_valid(root)
            self.assertEqual(rw.verify_onnx_cache(root), onnx_path)

            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            meta['revision'] = 'different-revision'
            meta_path.write_text(json.dumps(meta), encoding='utf-8')
            self.assertIsNone(rw.verify_onnx_cache(root))

            self._write_valid(root, max_err=rw.ONNX_PARITY_MAX_ERR + 0.01)
            self.assertIsNone(rw.verify_onnx_cache(root))

            self._write_valid(root, payload=b'clean-bytes-1234')
            onnx_path.write_bytes(b'tampered-xx-1234')
            self.assertIsNone(rw.verify_onnx_cache(root))

            self._write_valid(root, payload=b'clean-bytes-1234')
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            meta['format'] = 'encoder_int8'
            meta_path.write_text(json.dumps(meta), encoding='utf-8')
            self.assertIsNone(rw.verify_onnx_cache(root))

    def test_maybe_attach_respects_mode_and_falls_back_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_valid(root)
            orig_encoder = SimpleNamespace(config={'hidden_size': 1024})
            model = SimpleNamespace(encoder=orig_encoder)

            loaded = SimpleNamespace(config=orig_encoder.config, marker='onnx')
            self.assertEqual(
                rw.maybe_attach_onnx_encoder(model, root, mode='off', loader=lambda p, cfg, t: loaded),
                'pytorch_fp32',
            )
            self.assertIs(model.encoder, orig_encoder)

            backend = rw.maybe_attach_onnx_encoder(
                model,
                root,
                mode='auto',
                loader=lambda p, cfg, t: loaded,
            )
            self.assertEqual(backend, 'encoder_fp32')
            self.assertIs(model.encoder, loaded)

            model.encoder = orig_encoder

            def boom(p, cfg, t):
                model.encoder = SimpleNamespace(corrupt=True)
                raise RuntimeError('ort failure')

            self.assertEqual(rw.maybe_attach_onnx_encoder(model, root, mode='auto', loader=boom), 'pytorch_fp32')
            self.assertIs(model.encoder, orig_encoder)

            with self.assertRaises(RuntimeError):
                rw.maybe_attach_onnx_encoder(model, root, mode='required', loader=boom)

        with tempfile.TemporaryDirectory() as empty_tmp:
            orig_encoder = SimpleNamespace(config={'hidden_size': 1024})
            model = SimpleNamespace(encoder=orig_encoder)
            self.assertEqual(rw.maybe_attach_onnx_encoder(model, Path(empty_tmp), mode='auto'), 'pytorch_fp32')
            with self.assertRaises(ModelError):
                rw.maybe_attach_onnx_encoder(model, Path(empty_tmp), mode='required')


if __name__ == '__main__':
    unittest.main()
