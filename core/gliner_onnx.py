"""Standalone ONNX FP32 + NumPy runtime for fastino/GLiNER2.5-Decide.

Runs classify_text, batch_classify_text, and extract_entities without importing
torch, transformers, or gliner2 at startup. Requires a SHA-256 verified,
parity-gated pre-optimized ONNX cache built from the pinned local snapshot.
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import re
import sys
import threading
import unicodedata
import zipfile
from collections import OrderedDict
from collections.abc import Mapping as MappingABC
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

REVISION = "9d1bfb848cd16d93ee56ccbc59cd92bf0f284d90"
STANDALONE_FORMAT = "standalone_fp32"
STANDALONE_PROVIDER = "CPUExecutionProvider"
STANDALONE_MAX_ABS_ERR = 1e-3
STANDALONE_MAX_PROB_DIFF = 1e-4
STANDALONE_HEAD_ALIGN = 64
MAX_WIDTH = 8
MAX_ENCODER_TOKENS = 512

SEP_STRUCT = "[SEP_STRUCT]"
SEP_TEXT = "[SEP_TEXT]"
P_TOKEN = "[P]"
C_TOKEN = "[C]"
E_TOKEN = "[E]"
R_TOKEN = "[R]"
L_TOKEN = "[L]"
EXAMPLE_TOKEN = "[EXAMPLE]"
OUTPUT_TOKEN = "[OUTPUT]"
DESC_TOKEN = "[DESCRIPTION]"

REQUIRED_SPECIAL_TOKENS = (
    SEP_STRUCT,
    SEP_TEXT,
    P_TOKEN,
    E_TOKEN,
    L_TOKEN,
    DESC_TOKEN,
)

HEAD_KEYS = (
    "classifier.0.bias",
    "classifier.0.weight",
    "classifier.2.bias",
    "classifier.2.weight",
    "count_embed.gru.bias_hh_l0",
    "count_embed.gru.bias_ih_l0",
    "count_embed.gru.weight_hh_l0",
    "count_embed.gru.weight_ih_l0",
    "count_embed.pos_embedding.weight",
    "count_embed.projector.0.bias",
    "count_embed.projector.0.weight",
    "count_embed.projector.2.bias",
    "count_embed.projector.2.weight",
    "count_pred.0.bias",
    "count_pred.0.weight",
    "count_pred.2.bias",
    "count_pred.2.weight",
    "span_rep.span_rep_layer.out_project.0.bias",
    "span_rep.span_rep_layer.out_project.0.weight",
    "span_rep.span_rep_layer.out_project.3.bias",
    "span_rep.span_rep_layer.out_project.3.weight",
    "span_rep.span_rep_layer.project_end.0.bias",
    "span_rep.span_rep_layer.project_end.0.weight",
    "span_rep.span_rep_layer.project_end.3.bias",
    "span_rep.span_rep_layer.project_end.3.weight",
    "span_rep.span_rep_layer.project_start.0.bias",
    "span_rep.span_rep_layer.project_start.0.weight",
    "span_rep.span_rep_layer.project_start.3.bias",
    "span_rep.span_rep_layer.project_start.3.weight",
)

_COMBINING_MARKS = "".join(
    chr(code) for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)).startswith("M")
)
_WORD = rf"[\w{_COMBINING_MARKS}\u200c\u200d]"
_WORD_PATTERN = re.compile(
    rf"""(?:https?://[^\s]+|www\.[^\s]+)
    |[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{{2,}}
    |@[a-z0-9_]+
    |{_WORD}+(?:[-_]{_WORD}+)*
    |\S""",
    re.VERBOSE | re.IGNORECASE,
)


def snapshot_dir(revision: str = REVISION) -> Path:
    return Path.home() / ".cache" / "huggingface" / "hub" / "models--fastino--GLiNER2.5-Decide" / "snapshots" / revision


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def standalone_paths(cache_dir: Path) -> Tuple[Path, Path, Path]:
    base = Path(cache_dir) / f"gliner_standalone_fp32_{REVISION[:12]}"
    return (
        base.with_suffix(".onnx"),
        base.with_suffix(".heads.npz"),
        base.with_suffix(".meta.json"),
    )


def standalone_mmap_head_paths(cache_dir: Path) -> Tuple[Path, Path]:
    base = Path(cache_dir) / f"gliner_standalone_fp32_{REVISION[:12]}"
    return (
        base.with_suffix(".heads.bin"),
        base.with_suffix(".heads.index.json"),
    )


def standalone_external_data_name() -> str:
    return f"gliner_standalone_fp32_{REVISION[:12]}.onnx.data"


def _ort_version() -> Optional[str]:
    try:
        import onnxruntime as ort
        return getattr(ort, "__version__", None)
    except Exception:
        return None


def _prepacking_disabled(disable_prepacking: Optional[bool]) -> bool:
    if disable_prepacking is not None:
        return bool(disable_prepacking)
    raw = os.environ.get("COFINANCE_ROUTER_PREPACK", "on").strip().lower()
    return raw in {"0", "off", "false", "no"}


def build_ort_session_options(*, threads: int = 6, disable_prepacking: Optional[bool] = None):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, int(threads))
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    opts.enable_cpu_mem_arena = False
    opts.enable_mem_pattern = False
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.disable_prepacking", "1" if _prepacking_disabled(disable_prepacking) else "0")
    return opts


def verify_mmap_heads(
    bin_path: Path,
    index_path: Path,
    *,
    expected_source_sha256: str,
) -> Optional[Dict[str, Any]]:
    bin_path = Path(bin_path)
    index_path = Path(index_path)
    if not bin_path.is_file() or not index_path.is_file():
        return None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict):
        return None
    if index.get("revision") != REVISION or index.get("align") != STANDALONE_HEAD_ALIGN:
        return None
    if not expected_source_sha256 or index.get("source_heads_sha256") != expected_source_sha256:
        return None
    try:
        bin_size = bin_path.stat().st_size
    except OSError:
        return None
    if bin_size != index.get("bin_bytes") or bin_size <= 0:
        return None
    tensors = index.get("tensors")
    if not isinstance(tensors, dict):
        return None
    for key in HEAD_KEYS:
        spec = tensors.get(key)
        if not isinstance(spec, dict) or spec.get("dtype") != "float32":
            return None
        offset = spec.get("offset")
        nbytes = spec.get("nbytes")
        shape = spec.get("shape")
        if (
            not isinstance(offset, int)
            or not isinstance(nbytes, int)
            or not isinstance(shape, list)
            or offset < 0
            or nbytes <= 0
            or offset % STANDALONE_HEAD_ALIGN != 0
            or offset + nbytes > bin_size
        ):
            return None
        expected_elems = 1
        for dim in shape:
            if not isinstance(dim, int) or dim <= 0:
                return None
            expected_elems *= dim
        if expected_elems * 4 != nbytes:
            return None
    if file_sha256(bin_path) != index.get("bin_sha256"):
        return None
    return index


def build_mmap_heads(
    heads_npz_path: Path,
    *,
    bin_path: Optional[Path] = None,
    index_path: Optional[Path] = None,
    source_heads_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    import numpy as np

    heads_npz_path = Path(heads_npz_path)
    if bin_path is None or index_path is None:
        default_bin, default_index = standalone_mmap_head_paths(heads_npz_path.parent)
        bin_path = default_bin if bin_path is None else Path(bin_path)
        index_path = default_index if index_path is None else Path(index_path)
    else:
        bin_path = Path(bin_path)
        index_path = Path(index_path)

    src_sha = source_heads_sha256 or file_sha256(heads_npz_path)
    bin_part = bin_path.with_suffix(".bin.part")
    index_part = index_path.with_suffix(".json.part")
    tensors: Dict[str, Dict[str, Any]] = {}
    offset = 0
    try:
        with np.load(str(heads_npz_path), allow_pickle=False) as archive, bin_part.open("wb") as out:
            missing = [k for k in HEAD_KEYS if k not in archive.files]
            if missing:
                raise ValueError(f"Missing standalone GLiNER head weights: {missing[:4]}")
            for key in HEAD_KEYS:
                arr = np.ascontiguousarray(archive[key], dtype=np.float32)
                raw = arr.tobytes()
                pad = (-offset) % STANDALONE_HEAD_ALIGN
                if pad:
                    out.write(b"\x00" * pad)
                    offset += pad
                out.write(raw)
                tensors[key] = {
                    "offset": offset,
                    "nbytes": len(raw),
                    "shape": [int(d) for d in arr.shape],
                    "dtype": "float32",
                }
                offset += len(raw)
        os.replace(bin_part, bin_path)
        index = {
            "revision": REVISION,
            "align": STANDALONE_HEAD_ALIGN,
            "source_heads_sha256": src_sha,
            "bin_bytes": bin_path.stat().st_size,
            "bin_sha256": file_sha256(bin_path),
            "tensors": tensors,
        }
        index_part.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(index_part, index_path)
        return index
    except BaseException:
        bin_part.unlink(missing_ok=True)
        index_part.unlink(missing_ok=True)
        raise


class LazyHeadStore(MappingABC):
    """On-demand head tensor mapping supporting zero-commit read-only mmap and lazy .npz loading."""

    def __init__(
        self,
        keys: Sequence[str],
        *,
        mode: str,
        npz_path: Optional[Path] = None,
        bin_path: Optional[Path] = None,
        index: Optional[Mapping[str, Any]] = None,
    ):
        self._keys = tuple(keys)
        self._key_set = frozenset(self._keys)
        self.mode = mode
        self._npz_path = Path(npz_path) if npz_path is not None else None
        self._bin_path = Path(bin_path) if bin_path is not None else None
        self._specs = dict(index["tensors"]) if index is not None else {}
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._fh = None
        self._mm = None
        self._closed = False
        if self.mode == "mmap" and self._bin_path is not None:
            self._fh = self._bin_path.open("rb")
            self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)

    @classmethod
    def from_npz(cls, npz_path: Path) -> "LazyHeadStore":
        npz_path = Path(npz_path)
        with zipfile.ZipFile(npz_path, "r") as zf:
            names = [name[:-4] if name.endswith(".npy") else name for name in zf.namelist()]
        return cls(names, mode="lazy_npz", npz_path=npz_path)

    @classmethod
    def from_mmap(cls, bin_path: Path, index: Mapping[str, Any]) -> "LazyHeadStore":
        return cls(list(index["tensors"].keys()), mode="mmap", bin_path=bin_path, index=index)

    def __contains__(self, key: object) -> bool:
        return key in self._key_set

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def loaded_keys(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._cache.keys())

    def __getitem__(self, key: str):
        if key not in self._key_set:
            raise KeyError(key)
        with self._lock:
            if self._closed:
                raise RuntimeError("Head store is closed")
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            import numpy as np
            if self.mode == "mmap":
                spec = self._specs[key]
                arr = np.ndarray(
                    shape=tuple(spec["shape"]),
                    dtype=np.float32,
                    buffer=self._mm,
                    offset=int(spec["offset"]),
                )
                arr.flags.writeable = False
                self._cache[key] = arr
                return arr
            with np.load(str(self._npz_path), allow_pickle=False) as archive:
                arr = np.ascontiguousarray(archive[key], dtype=np.float32)
            self._cache[key] = arr
            return arr

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cache.clear()
            if self._mm is not None:
                try:
                    self._mm.close()
                except OSError:
                    pass
                self._mm = None
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


def verify_standalone_onnx_cache(
    cache_dir: Path,
    tokenizer_path: Optional[Path] = None,
    *,
    ort_version: Optional[str] = None,
) -> Optional[Tuple[Path, Path, Path]]:
    onnx_path, heads_path, meta_path = standalone_paths(cache_dir)
    tok_path = Path(tokenizer_path) if tokenizer_path is not None else snapshot_dir() / "tokenizer.json"
    if not onnx_path.is_file() or not heads_path.is_file() or not meta_path.is_file() or not tok_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("revision") != REVISION or meta.get("format") != STANDALONE_FORMAT:
        return None
    if meta.get("provider") != STANDALONE_PROVIDER:
        return None
    expected_ort = ort_version if ort_version is not None else _ort_version()
    if not expected_ort or meta.get("ort_version") != expected_ort:
        return None
    max_err = meta.get("max_abs_err")
    if not isinstance(max_err, (int, float)) or max_err > STANDALONE_MAX_ABS_ERR:
        return None
    max_prob = meta.get("max_prob_diff")
    if not isinstance(max_prob, (int, float)) or max_prob > STANDALONE_MAX_PROB_DIFF:
        return None
    if onnx_path.stat().st_size != meta.get("onnx_bytes"):
        return None
    if heads_path.stat().st_size != meta.get("heads_bytes"):
        return None
    data_name = meta.get("onnx_data_file")
    if data_name is not None:
        if not isinstance(data_name, str) or not data_name or Path(data_name).name != data_name:
            return None
        data_path = Path(cache_dir) / data_name
        if not data_path.is_file() or data_path.stat().st_size != meta.get("onnx_data_bytes"):
            return None
        if file_sha256(data_path) != meta.get("onnx_data_sha256"):
            return None
    if file_sha256(tok_path) != meta.get("tokenizer_sha256"):
        return None
    if file_sha256(heads_path) != meta.get("heads_sha256"):
        return None
    if file_sha256(onnx_path) != meta.get("onnx_sha256"):
        return None
    return onnx_path, heads_path, tok_path


def split_words(text: str, lower: bool = True) -> Tuple[List[str], List[int], List[int]]:
    tokens: List[str] = []
    starts: List[int] = []
    ends: List[int] = []
    for m in _WORD_PATTERN.finditer(text or ""):
        tok = m.group()
        tokens.append(tok.lower() if lower else tok)
        starts.append(m.start())
        ends.append(m.end())
    return tokens, starts, ends


def _normalize_text(text: str) -> str:
    if not text:
        return "."
    if not text.endswith((".", "!", "?")):
        return text + "."
    return text


def _sigmoid(x):
    import numpy as np
    arr = np.asarray(x, dtype=np.float32)
    clipped = np.clip(arr, -88.0, 88.0)
    out = np.where(clipped >= 0, 1.0 / (1.0 + np.exp(-clipped)), np.exp(clipped) / (1.0 + np.exp(clipped)))
    return np.where(np.isneginf(arr), 0.0, out)


def _softmax(x):
    import numpy as np
    shifted = x - np.max(x, axis=-1, keepdims=True)
    ex = np.exp(shifted)
    return ex / np.sum(ex, axis=-1, keepdims=True)


def _relu(x):
    import numpy as np
    return np.maximum(x, 0.0)


def _linear(x, weight, bias=None):
    out = x @ weight.T
    if bias is not None:
        out = out + bias
    return out


def _normalize_classification_tasks(tasks: Mapping[str, Any]) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for name, raw_cfg in tasks.items():
        if isinstance(raw_cfg, Mapping) and "labels" in raw_cfg:
            raw_labels = raw_cfg["labels"]
            multi_label = bool(raw_cfg.get("multi_label", False))
            cls_threshold = float(raw_cfg.get("cls_threshold", 0.5))
            prompt = raw_cfg.get("prompt")
            class_act = str(raw_cfg.get("class_act", "auto"))
            top_k = raw_cfg.get("top_k")
            examples = list(raw_cfg.get("examples") or [])
        else:
            raw_labels = raw_cfg
            multi_label = False
            cls_threshold = 0.5
            prompt = None
            class_act = "auto"
            top_k = None
            examples = []
        if isinstance(raw_labels, Mapping):
            labels = list(raw_labels.keys())
            descriptions = {str(k): str(v) for k, v in raw_labels.items()}
        else:
            labels = [str(x) for x in raw_labels]
            descriptions = {}
        specs.append({
            "task": str(name),
            "labels": labels,
            "label_descriptions": descriptions,
            "multi_label": multi_label,
            "cls_threshold": cls_threshold,
            "prompt": prompt,
            "class_act": class_act,
            "top_k": top_k,
            "examples": examples,
        })
    return specs


def _transform_schema(
    parent: str,
    fields: Sequence[str],
    child_prefix: str,
    *,
    prompt: Optional[str] = None,
    examples: Sequence[Tuple[str, Any]] = (),
    label_descriptions: Optional[Mapping[str, str]] = None,
) -> List[str]:
    prompt_str = f"{parent}: {prompt}" if prompt else parent
    if label_descriptions:
        for label, desc in label_descriptions.items():
            if label in fields:
                prompt_str += f" {DESC_TOKEN} {label}: {desc}"
    if examples:
        for inp, out in examples:
            if out in fields:
                out_str = out if isinstance(out, str) else ", ".join(out)
                prompt_str += f" {EXAMPLE_TOKEN} {inp} {OUTPUT_TOKEN} {out_str}"
    tokens = ["(", P_TOKEN, prompt_str, "("]
    for field_name in fields:
        tokens.extend([child_prefix, field_name])
    tokens.extend([")", ")"])
    return tokens


def _finalize_greedy_spans(spans: List[Tuple[str, float, int, int]]) -> List[Tuple[str, float, int, int]]:
    ranked = sorted(spans, key=lambda s: (-s[1], s[2], s[3]))
    selected: List[Tuple[str, float, int, int]] = []
    for cand in ranked:
        _, _, c_start, c_end = cand
        if any(not (c_end <= s_start or c_start >= s_end) for _, _, s_start, s_end in selected):
            continue
        selected.append(cand)
    return selected


def _make_piece_encoder(tokenizer, maxsize: int = 8_192):
    @lru_cache(maxsize=maxsize)
    def encode_piece(piece: str) -> Tuple[int, ...]:
        return tuple(tokenizer.encode(piece, add_special_tokens=False).ids)
    return encode_piece


class StandaloneGlinerOnnx:
    """Pure ONNX FP32 + NumPy GLiNER2.5-Decide backend with zero torch/transformers imports."""

    def __init__(self, session, tokenizer, heads: Mapping[str, Any], *, max_width: int = MAX_WIDTH):
        self.session = session
        self.tokenizer = tokenizer
        self.heads = heads if isinstance(heads, LazyHeadStore) else dict(heads)
        self.max_width = max_width
        missing = [k for k in HEAD_KEYS if k not in self.heads]
        if missing:
            if isinstance(self.heads, LazyHeadStore):
                self.heads.close()
            raise ValueError(f"Missing standalone GLiNER head weights: {missing[:4]}")
        for tok in REQUIRED_SPECIAL_TOKENS:
            if self.tokenizer.token_to_id(tok) is None:
                if isinstance(self.heads, LazyHeadStore):
                    self.heads.close()
                raise ValueError(f"Tokenizer missing required special token {tok!r}")
        pad_id = self.tokenizer.token_to_id("[PAD]")
        if pad_id is None:
            if isinstance(self.heads, LazyHeadStore):
                self.heads.close()
            raise ValueError("Tokenizer missing [PAD] token id")
        self.pad_id = int(pad_id)
        self._special_id_cache: Dict[str, List[int]] = {}
        for tok in (SEP_STRUCT, SEP_TEXT, P_TOKEN, C_TOKEN, E_TOKEN, R_TOKEN, L_TOKEN, EXAMPLE_TOKEN, OUTPUT_TOKEN, DESC_TOKEN):
            tid = self.tokenizer.token_to_id(tok)
            if tid is not None:
                self._special_id_cache[tok] = [int(tid)]
        self._encode_piece = _make_piece_encoder(self.tokenizer)
        self._schema_prefix_lock = threading.Lock()
        self._schema_prefix_cache: Dict[Tuple[Tuple[str, ...], ...], Tuple[Tuple[int, ...], Tuple[Tuple[int, ...], ...]]] = {}

    def close(self) -> None:
        self._encode_piece.cache_clear()
        with self._schema_prefix_lock:
            self._schema_prefix_cache.clear()
        if isinstance(self.heads, LazyHeadStore):
            self.heads.close()
        else:
            self.heads.clear()
        self.session = None

    def _ids_for_token(self, token: str) -> Sequence[int]:
        cached = self._special_id_cache.get(token)
        if cached is not None:
            return cached
        return self._encode_piece(token)

    def _compile_schema_prefix(
        self,
        schema_tokens_list: List[List[str]],
    ) -> Tuple[Tuple[int, ...], Tuple[Tuple[int, ...], ...]]:
        cache_key = tuple(tuple(struct) for struct in schema_tokens_list)
        with self._schema_prefix_lock:
            cached = self._schema_prefix_cache.get(cache_key)
        if cached is not None:
            return cached
        combined: List[str] = []
        for struct in schema_tokens_list:
            combined.extend(struct)
            combined.append(SEP_STRUCT)
        if combined:
            combined.pop()
        combined.append(SEP_TEXT)

        schema_marker_orig_indices = set()
        offset = 0
        for struct in schema_tokens_list:
            if len(struct) > 1:
                schema_marker_orig_indices.add(offset + 1)
            schema_marker_orig_indices.update(range(offset + 4, offset + len(struct) - 2, 2))
            offset += len(struct) + 1

        prefix_ids: List[int] = []
        schema_special_positions: List[List[int]] = [[] for _ in range(len(schema_tokens_list))]
        current_schema = 0
        for orig_idx, token in enumerate(combined):
            subword_pos = len(prefix_ids)
            prefix_ids.extend(self._ids_for_token(token))
            if token == SEP_TEXT:
                continue
            schema_idx = current_schema
            if token == SEP_STRUCT:
                current_schema += 1
            if orig_idx in schema_marker_orig_indices:
                schema_special_positions[schema_idx].append(subword_pos)

        compiled = (tuple(prefix_ids), tuple(tuple(pos) for pos in schema_special_positions))
        with self._schema_prefix_lock:
            if len(self._schema_prefix_cache) >= 64:
                self._schema_prefix_cache.clear()
            self._schema_prefix_cache[cache_key] = compiled
        return compiled

    def _format_input(
        self,
        schema_tokens_list: List[List[str]],
        text_tokens: List[str],
    ) -> Tuple[List[int], List[int], List[List[int]]]:
        prefix_ids, prefix_positions = self._compile_schema_prefix(schema_tokens_list)
        input_ids = list(prefix_ids)
        text_word_first_positions: List[int] = []
        for token in text_tokens:
            text_word_first_positions.append(len(input_ids))
            input_ids.extend(self._ids_for_token(token))
        return input_ids, text_word_first_positions, [list(pos) for pos in prefix_positions]

    def _run_encoder_batch(self, id_lists: List[List[int]]):
        import numpy as np
        batch_size = len(id_lists)
        max_len = max((len(ids) for ids in id_lists), default=1)
        max_len = max(1, min(max_len, MAX_ENCODER_TOKENS))
        ids_np = np.full((batch_size, max_len), self.pad_id, dtype=np.int64)
        mask_np = np.zeros((batch_size, max_len), dtype=np.int64)
        for i, ids in enumerate(id_lists):
            n = min(len(ids), max_len)
            if n > 0:
                ids_np[i, :n] = ids[:n]
                mask_np[i, :n] = 1
        (lhs,) = self.session.run(
            ["last_hidden_state"],
            {"input_ids": ids_np, "attention_mask": mask_np},
        )
        return lhs

    def _score_classifier(self, cls_embeds):
        h = _relu(_linear(cls_embeds, self.heads["classifier.0.weight"], self.heads["classifier.0.bias"]))
        return _linear(h, self.heads["classifier.2.weight"], self.heads["classifier.2.bias"]).squeeze(-1)

    def _score_count_pred(self, p_emb):
        h = _relu(_linear(p_emb, self.heads["count_pred.0.weight"], self.heads["count_pred.0.bias"]))
        return _linear(h, self.heads["count_pred.2.weight"], self.heads["count_pred.2.bias"])

    def _count_embed_step0(self, field_embs):
        import numpy as np
        pos0 = self.heads["count_embed.pos_embedding.weight"][0]
        x = np.broadcast_to(pos0, field_embs.shape)
        gi = _linear(x, self.heads["count_embed.gru.weight_ih_l0"], self.heads["count_embed.gru.bias_ih_l0"])
        gh = _linear(field_embs, self.heads["count_embed.gru.weight_hh_l0"], self.heads["count_embed.gru.bias_hh_l0"])
        i_r, i_z, i_n = np.split(gi, 3, axis=-1)
        h_r, h_z, h_n = np.split(gh, 3, axis=-1)
        r = _sigmoid(i_r + h_r)
        z = _sigmoid(i_z + h_z)
        n = np.tanh(i_n + r * h_n)
        h = (1.0 - z) * n + z * field_embs
        cat = np.concatenate([h, field_embs], axis=-1)
        proj = _relu(_linear(cat, self.heads["count_embed.projector.0.weight"], self.heads["count_embed.projector.0.bias"]))
        return _linear(proj, self.heads["count_embed.projector.2.weight"], self.heads["count_embed.projector.2.bias"])

    def _span_logits(self, token_embs, queries):
        import numpy as np
        length, hidden = token_embs.shape
        if length == 0:
            return np.zeros((queries.shape[0], 0, self.max_width), dtype=np.float32)

        def proj(x, prefix):
            h = _relu(_linear(x, self.heads[f"{prefix}.0.weight"], self.heads[f"{prefix}.0.bias"]))
            return _linear(h, self.heads[f"{prefix}.3.weight"], self.heads[f"{prefix}.3.bias"])

        start_rep = _relu(proj(token_embs, "span_rep.span_rep_layer.project_start"))
        end_rep = _relu(proj(token_embs, "span_rep.span_rep_layer.project_end"))

        w1 = self.heads["span_rep.span_rep_layer.out_project.0.weight"]
        b1 = self.heads["span_rep.span_rep_layer.out_project.0.bias"]
        w2 = self.heads["span_rep.span_rep_layer.out_project.3.weight"]
        b2 = self.heads["span_rep.span_rep_layer.out_project.3.bias"]

        start_hidden = start_rep @ w1[:, :hidden].T
        end_hidden = end_rep @ w1[:, hidden:].T

        starts = np.arange(length)[:, None]
        widths = np.arange(self.max_width)[None, :]
        ends = starts + widths
        valid = ends < length
        safe_ends = np.where(valid, ends, 0)

        pair_hidden = _relu(start_hidden[:, None, :] + end_hidden[safe_ends] + b1)
        span_vecs = pair_hidden @ w2.T + b2
        logits = np.einsum("lwh,qh->qlw", span_vecs, queries, optimize=True)
        return np.where(valid[None, :, :], logits, -np.inf)

    def batch_classify_text(
        self,
        texts: Sequence[str],
        tasks: Mapping[str, Any],
        *,
        batch_size: int = 8,
        include_confidence: bool = False,
        max_len: Optional[int] = None,
        temperature: float = 1.0,
        **_ignored,
    ) -> List[Dict[str, Any]]:
        import numpy as np
        if temperature <= 0:
            raise ValueError("classification temperature must be > 0")
        items = list(texts)
        if not items:
            return []
        specs = _normalize_classification_tasks(tasks)
        schema_tokens_list = [
            _transform_schema(
                spec["task"],
                spec["labels"],
                L_TOKEN,
                prompt=spec["prompt"],
                examples=spec["examples"],
                label_descriptions=spec["label_descriptions"],
            )
            for spec in specs
        ]
        prepared = []
        for text in items:
            norm_text = _normalize_text(text or "")
            text_tokens, _, _ = split_words(norm_text, lower=True)
            if max_len is not None and len(text_tokens) > max_len:
                text_tokens = text_tokens[:max_len]
            input_ids, _, schema_pos = self._format_input(schema_tokens_list, text_tokens)
            prepared.append((input_ids, schema_pos))

        step = max(1, int(batch_size))
        out: List[Dict[str, Any]] = []
        for start in range(0, len(prepared), step):
            chunk = prepared[start:start + step]
            lhs = self._run_encoder_batch([row[0] for row in chunk])
            seq_limit = lhs.shape[1]
            for i, (_, schema_pos) in enumerate(chunk):
                sample_out: Dict[str, Any] = {}
                for spec, positions in zip(specs, schema_pos):
                    cls_pos = positions[1:]
                    if not cls_pos or any(p >= seq_limit for p in cls_pos):
                        raise ValueError(f"Classification marker positions out of bounds for task {spec['task']!r}")
                    cls_embeds = lhs[i, cls_pos, :]
                    logits = self._score_classifier(cls_embeds) / float(temperature)
                    act = spec["class_act"]
                    is_multi = spec["multi_label"]
                    if act == "sigmoid" or (act == "auto" and is_multi):
                        probs = _sigmoid(logits)
                    else:
                        probs = _softmax(logits)
                    labels = spec["labels"]
                    if is_multi:
                        chosen = [
                            (labels[j], float(probs[j]))
                            for j in range(len(labels))
                            if float(probs[j]) >= spec["cls_threshold"]
                        ]
                        if not chosen:
                            best = int(np.argmax(probs))
                            chosen = [(labels[best], float(probs[best]))]
                        if spec["top_k"] is not None:
                            chosen = sorted(chosen, key=lambda pair: pair[1], reverse=True)[: int(spec["top_k"])]
                        if include_confidence:
                            sample_out[spec["task"]] = [{"label": l, "confidence": c} for l, c in chosen]
                        else:
                            sample_out[spec["task"]] = [l for l, _ in chosen]
                    else:
                        best = int(np.argmax(probs))
                        label, conf = labels[best], float(probs[best])
                        sample_out[spec["task"]] = {"label": label, "confidence": conf} if include_confidence else label
                out.append(sample_out)
        return out

    def classify_text(
        self,
        text: str,
        tasks: Mapping[str, Any],
        **kwargs,
    ) -> Dict[str, Any]:
        return self.batch_classify_text([text], tasks, batch_size=1, **kwargs)[0]

    def extract_entities(
        self,
        text: str,
        entity_types: Sequence[str] | Mapping[str, str],
        *,
        threshold: float = 0.5,
        include_confidence: bool = False,
        max_len: Optional[int] = None,
        **_ignored,
    ) -> Dict[str, Any]:
        import numpy as np
        if isinstance(entity_types, Mapping):
            entity_names = [str(k) for k in entity_types.keys()]
            descriptions = {str(k): str(v) for k, v in entity_types.items() if v}
        else:
            entity_names = [str(e) for e in entity_types]
            descriptions = {}
        empty = {"entities": {name: [] for name in entity_names}}
        if not entity_names:
            return empty
        if not (text or "").strip():
            return empty
        norm_text = _normalize_text(text)
        text_tokens, start_map, end_map = split_words(norm_text, lower=True)
        if max_len is not None and len(text_tokens) > max_len:
            text_tokens = text_tokens[:max_len]
            start_map = start_map[:max_len]
            end_map = end_map[:max_len]
        if not text_tokens:
            return empty

        schema_tokens_list = [_transform_schema("entities", entity_names, E_TOKEN, label_descriptions=descriptions or None)]
        input_ids, text_pos, schema_pos = self._format_input(schema_tokens_list, text_tokens)
        lhs = self._run_encoder_batch([input_ids])[0]
        seq_limit = lhs.shape[0]
        positions = schema_pos[0]
        if len(positions) < 1 + len(entity_names) or any(p >= seq_limit for p in positions):
            raise ValueError("Entity schema marker positions out of bounds")

        kept_word_positions = [p for p in text_pos if p < seq_limit]
        text_len = len(kept_word_positions)
        if text_len == 0:
            return empty

        p_emb = lhs[positions[0]]
        pred_count = int(np.argmax(self._score_count_pred(p_emb)))
        if pred_count <= 0:
            return empty

        field_embs = lhs[positions[1:1 + len(entity_names)]]
        queries = self._count_embed_step0(field_embs)
        token_embs = lhs[kept_word_positions]
        raw_logits = self._span_logits(token_embs, queries)
        span_scores = _sigmoid(raw_logits)

        formatted: Dict[str, List[Any]] = OrderedDict()
        for idx, name in enumerate(entity_names):
            scores = span_scores[idx]
            starts, widths = np.where(scores >= float(threshold))
            raw_spans: List[Tuple[str, float, int, int]] = []
            for s_idx, w_idx in zip(starts.tolist(), widths.tolist()):
                e_idx = s_idx + w_idx + 1
                if 0 <= s_idx < text_len and e_idx <= text_len:
                    char_start = start_map[s_idx]
                    char_end = end_map[e_idx - 1]
                    piece = norm_text[char_start:char_end].strip()
                    if piece:
                        raw_spans.append((piece, float(scores[s_idx, w_idx]), char_start, char_end))
            chosen = _finalize_greedy_spans(raw_spans)
            unique: List[Any] = []
            seen = set()
            for piece, conf, char_start, char_end in chosen:
                key = (piece.lower(), char_start, char_end)
                if key not in seen:
                    seen.add(key)
                    unique.append({"text": piece, "confidence": conf} if include_confidence else piece)
            formatted[name] = unique
        return {"entities": dict(formatted)}


def load_standalone_onnx(
    cache_dir: Path,
    *,
    tokenizer_path: Optional[Path] = None,
    threads: int = 6,
    disable_prepacking: Optional[bool] = None,
) -> Optional[StandaloneGlinerOnnx]:
    verified = verify_standalone_onnx_cache(cache_dir, tokenizer_path=tokenizer_path)
    if verified is None:
        return None
    onnx_path, heads_path, tok_path = verified
    _, _, meta_path = standalone_paths(cache_dir)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    import onnxruntime as ort
    from tokenizers import Tokenizer

    opts = build_ort_session_options(threads=threads, disable_prepacking=disable_prepacking)
    session = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=[STANDALONE_PROVIDER])
    tokenizer = Tokenizer.from_file(str(tok_path))
    bin_path, index_path = standalone_mmap_head_paths(cache_dir)
    mmap_index = verify_mmap_heads(bin_path, index_path, expected_source_sha256=str(meta.get("heads_sha256") or ""))
    heads = LazyHeadStore.from_mmap(bin_path, mmap_index) if mmap_index is not None else LazyHeadStore.from_npz(heads_path)
    return StandaloneGlinerOnnx(session, tokenizer, heads)


def convert_standalone_to_external_data(
    cache_dir: Path,
    *,
    tokenizer_path: Optional[Path] = None,
    ort_version: Optional[str] = None,
    size_threshold: int = 1024,
) -> Dict[str, Any]:
    """Repack a verified standalone ONNX + heads cache into external-data ONNX and 64B-aligned mmap heads."""
    import onnx

    cache_dir = Path(cache_dir)
    verified = verify_standalone_onnx_cache(cache_dir, tokenizer_path=tokenizer_path, ort_version=ort_version)
    if verified is None:
        raise ValueError("Verified standalone ONNX cache is missing or failed integrity verification.")
    onnx_path, heads_path, _ = verified
    _, _, meta_path = standalone_paths(cache_dir)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    data_name = standalone_external_data_name()
    data_path = cache_dir / data_name
    onnx_part = onnx_path.with_suffix(".onnx.extpart")
    meta_part = meta_path.with_suffix(".json.part")
    wrote_data = False
    try:
        if meta.get("onnx_data_file") != data_name or not data_path.is_file():
            model = onnx.load(str(onnx_path))
            data_path.unlink(missing_ok=True)
            onnx.save_model(
                model,
                str(onnx_part),
                save_as_external_data=True,
                all_tensors_to_one_file=True,
                location=data_name,
                size_threshold=int(size_threshold),
            )
            wrote_data = True
            del model
            if not data_path.is_file():
                raise ValueError("External data file was not written by onnx.save_model.")
            os.replace(onnx_part, onnx_path)
            meta["onnx_bytes"] = onnx_path.stat().st_size
            meta["onnx_sha256"] = file_sha256(onnx_path)
            meta["onnx_data_file"] = data_name
            meta["onnx_data_bytes"] = data_path.stat().st_size
            meta["onnx_data_sha256"] = file_sha256(data_path)
            meta_part.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(meta_part, meta_path)

        bin_path, index_path = standalone_mmap_head_paths(cache_dir)
        head_index = build_mmap_heads(
            heads_path,
            bin_path=bin_path,
            index_path=index_path,
            source_heads_sha256=str(meta["heads_sha256"]),
        )
        return {**meta, "heads_mmap_bytes": head_index["bin_bytes"], "heads_mmap_sha256": head_index["bin_sha256"]}
    except BaseException:
        onnx_part.unlink(missing_ok=True)
        if wrote_data and meta.get("onnx_data_file") != data_name:
            data_path.unlink(missing_ok=True)
        meta_part.unlink(missing_ok=True)
        raise


def export_standalone_onnx(
    cache_dir: Path,
    *,
    source_onnx: Optional[Path] = None,
    threads: int = 6,
) -> Dict[str, Any]:
    """Build pre-optimized ONNX + head archive and gate on full parity against AutoExtractor."""
    import numpy as np
    import onnxruntime as ort
    import torch
    from gliner2 import AutoExtractor
    from safetensors import safe_open
    from tokenizers import Tokenizer
    from .gliner_decide import SIGNAL_SCHEMA, USER_QUERY_SCHEMA
    from .local_models import ModelError

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    snap = snapshot_dir()
    tok_path = snap / "tokenizer.json"
    weights_path = snap / "model.safetensors"
    if not tok_path.is_file() or not weights_path.is_file():
        raise ModelError("Pinned GLiNER2.5-Decide snapshot files are missing.")

    onnx_path, heads_path, meta_path = standalone_paths(cache_dir)
    base_onnx = Path(source_onnx) if source_onnx is not None else cache_dir / f"gliner_encoder_fp32_{REVISION[:12]}.onnx"
    if not base_onnx.is_file():
        from .router_worker import export_onnx_encoder
        export_onnx_encoder(cache_dir, threads=threads)

    opt_part = onnx_path.with_suffix(".onnx.part")
    heads_part = heads_path.with_suffix(".npz.part")
    meta_part = meta_path.with_suffix(".json.part")

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.optimized_model_filepath = str(opt_part)
    opt_sess = ort.InferenceSession(str(base_onnx), sess_options=opts, providers=[STANDALONE_PROVIDER])
    del opt_sess

    heads: Dict[str, Any] = {}
    with safe_open(str(weights_path), framework="numpy") as sf:
        for key in HEAD_KEYS:
            heads[key] = np.ascontiguousarray(sf.get_tensor(key), dtype=np.float32)
    with heads_part.open("wb") as fh:
        np.savez(fh, **heads)

    load_opts = build_ort_session_options(threads=threads)
    standalone_sess = ort.InferenceSession(str(opt_part), sess_options=load_opts, providers=[STANDALONE_PROVIDER])
    standalone = StandaloneGlinerOnnx(standalone_sess, Tokenizer.from_file(str(tok_path)), heads)

    torch.set_num_threads(threads)
    ref_model = AutoExtractor.from_pretrained(str(snap), local_files_only=True).cpu().eval()

    max_abs_err = 0.0
    gen = torch.Generator().manual_seed(1729)
    with torch.no_grad():
        for seq_len in (24, 64, 160):
            ids = torch.randint(10, 5000, (1, seq_len), dtype=torch.long, generator=gen)
            mask = torch.ones((1, seq_len), dtype=torch.long)
            ref_lhs = ref_model.encoder(input_ids=ids, attention_mask=mask).last_hidden_state.numpy()
            (got_lhs,) = standalone_sess.run(
                ["last_hidden_state"],
                {"input_ids": ids.numpy().astype("int64"), "attention_mask": mask.numpy().astype("int64")},
            )
            max_abs_err = max(max_abs_err, float(np.max(np.abs(got_lhs - ref_lhs))))

    parity_queries = [
        "What is NVDA trading at right now?",
        "Any news on Tesla this week?",
        "Compare AMD and NVDA for the next month",
        "Add $SOL to my watchlist",
        "Give me the bull and bear case for Apple",
        "How is Bitcoin priced today?",
        "What are the latest catalysts for Microsoft stock?",
        "Ethereum vs Solana: which one looks stronger?",
        "Keep an eye on GOOGL for me",
        "Is Amazon a good long-term hold? Full thesis please",
    ]
    parity_headlines = [
        "[NVDA] NVIDIA announces next-generation Rubin architecture and raises quarterly revenue outlook",
        "[AAPL] Top 5 tech stocks retail investors are talking about this weekend",
        "[TSLA] NHTSA opens formal safety probe into Tesla Full Self-Driving incidents",
        "[BTC] Bitcoin ETF inflows hit a 3-month high as spot volume surges",
    ]

    max_prob_diff = 0.0
    route_exact = 0
    for q in parity_queries:
        ref_cls = ref_model.classify_text(q, USER_QUERY_SCHEMA, include_confidence=True)
        got_cls = standalone.classify_text(q, USER_QUERY_SCHEMA, include_confidence=True)
        same_labels = all(ref_cls[k]["label"] == got_cls[k]["label"] for k in ("intent", "horizon"))
        for k in ("intent", "horizon"):
            max_prob_diff = max(max_prob_diff, abs(float(ref_cls[k]["confidence"]) - float(got_cls[k]["confidence"])))
        ref_ner = ref_model.extract_entities(q, ["company", "cryptocurrency", "ticker"])
        got_ner = standalone.extract_entities(q, ["company", "cryptocurrency", "ticker"])
        if same_labels and ref_ner == got_ner:
            route_exact += 1

    ref_batch = ref_model.batch_classify_text(parity_headlines, SIGNAL_SCHEMA, batch_size=4, include_confidence=True)
    got_batch = standalone.batch_classify_text(parity_headlines, SIGNAL_SCHEMA, batch_size=4, include_confidence=True)
    headline_exact = 0
    for r_row, g_row in zip(ref_batch, got_batch):
        if all(r_row[k]["label"] == g_row[k]["label"] for k in ("materiality", "urgency", "direction")):
            headline_exact += 1
        for k in ("materiality", "urgency", "direction"):
            max_prob_diff = max(max_prob_diff, abs(float(r_row[k]["confidence"]) - float(g_row[k]["confidence"])))

    del standalone_sess, standalone, ref_model

    if (
        max_abs_err > STANDALONE_MAX_ABS_ERR
        or max_prob_diff > STANDALONE_MAX_PROB_DIFF
        or route_exact != len(parity_queries)
        or headline_exact != len(parity_headlines)
    ):
        opt_part.unlink(missing_ok=True)
        heads_part.unlink(missing_ok=True)
        raise ModelError(
            f"Standalone ONNX parity failed: max_abs_err={max_abs_err:.6f}, "
            f"max_prob_diff={max_prob_diff:.6f}, routes={route_exact}/{len(parity_queries)}, "
            f"headlines={headline_exact}/{len(parity_headlines)}"
        )

    os.replace(opt_part, onnx_path)
    os.replace(heads_part, heads_path)
    meta = {
        "revision": REVISION,
        "format": STANDALONE_FORMAT,
        "provider": STANDALONE_PROVIDER,
        "ort_version": _ort_version(),
        "tokenizer_sha256": file_sha256(tok_path),
        "onnx_bytes": onnx_path.stat().st_size,
        "onnx_sha256": file_sha256(onnx_path),
        "heads_bytes": heads_path.stat().st_size,
        "heads_sha256": file_sha256(heads_path),
        "max_abs_err": round(max_abs_err, 6),
        "max_prob_diff": round(max_prob_diff, 6),
        "route_exact": route_exact,
        "route_total": len(parity_queries),
        "headline_exact": headline_exact,
        "headline_total": len(parity_headlines),
    }
    meta_part.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(meta_part, meta_path)
    return meta
