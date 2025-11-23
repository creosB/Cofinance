from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None  # type: ignore

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # pragma: no cover
    SentenceTransformer = None  # type: ignore


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    return v / n


class Embeddings:
    def __init__(self) -> None:
        self.model = None
        if SentenceTransformer is not None:
            try:
                self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            except Exception:
                self.model = None

    def embed(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        if self.model is not None:
            vecs = np.array(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)
            return vecs
        # Fallback: hashing-based embedding
        dim = 384
        arr = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in (t or "").lower().split():
                h = int(hashlib.sha1(tok.encode("utf-8")).hexdigest(), 16)
                idx = h % dim
                arr[i, idx] += 1.0
        return _normalize(arr)


def get_vector_env_status() -> dict:
    emb = Embeddings()
    return {
        "embeddings": "sentence-transformers" if emb.model else "hash-fallback",
        "faiss": faiss is not None,
        "dim": 384,
    }


@dataclass
class VectorItem:
    text: str
    kind: str  # "message" or "fact"
    score: float = 0.0


class Retriever:
    def __init__(self) -> None:
        self.emb = Embeddings()
        self._use_faiss = faiss is not None
        self._index = None
        self._items: List[VectorItem] = []
        self._dim = 384

    def build(self, texts: List[str], kinds: List[str]) -> None:
        if not texts:
            self._items = []
            self._index = None
            return
        vecs = self.emb.embed(texts)
        self._items = [VectorItem(text=t, kind=k) for t, k in zip(texts, kinds)]

        if self._use_faiss:
            try:
                self._index = faiss.IndexFlatIP(self._dim)
                self._index.add(vecs)
                return
            except Exception:
                self._use_faiss = False
                self._index = None
        # Fallback: store normalized vectors for cosine search
        self._index = vecs

    def search(self, query: str, top_k: int = 5) -> List[VectorItem]:
        if not self._items or self._index is None:
            return []
        q = self.emb.embed([query])
        if self._use_faiss and hasattr(self._index, "search"):
            scores, idxs = self._index.search(q, min(top_k, len(self._items)))
            results: List[VectorItem] = []
            for s, i in zip(scores[0], idxs[0]):
                if i == -1:
                    continue
                it = self._items[int(i)]
                results.append(VectorItem(text=it.text, kind=it.kind, score=float(s)))
            return results
        # Fallback cosine similarity
        mat = self._index  # type: ignore
        sims = (mat @ q.T).ravel()
        order = np.argsort(-sims)[: min(top_k, len(self._items))]
        return [VectorItem(text=self._items[i].text, kind=self._items[i].kind, score=float(sims[i])) for i in order]
