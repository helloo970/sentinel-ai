"""Local embeddings for the semantic memory search (CPU).

Default: hash vectors from character n-grams (no download, robust against typos and
word forms). Optional: fastembed with a small multilingual model
(`pip install -e ".[embeddings]"`, the model is loaded on first start).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

DIM = 512


class Embedder(Protocol):
    kind: str
    min_similarity: float

    def embed(self, text: str) -> np.ndarray: ...

    def from_bytes(self, raw: bytes) -> np.ndarray: ...


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class HashEmbedder:
    """Character trigrams and words, folded into a fixed vector by hashing."""

    kind = "hash3"
    min_similarity = 0.18

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(DIM, dtype=np.float32)
        words = re.findall(r"\w+", text.lower())
        for word in words:
            padded = f"  {word} "
            features = [padded[i : i + 3] for i in range(len(padded) - 2)] + [f"w:{word}"]
            for feature in features:
                digest = hashlib.blake2b(feature.encode(), digest_size=4).digest()
                index = int.from_bytes(digest[:2], "little") % DIM
                sign = 1.0 if digest[2] & 1 else -1.0
                vec[index] += sign * (2.0 if feature.startswith("w:") else 1.0)
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def from_bytes(self, raw: bytes) -> np.ndarray:
        return np.frombuffer(raw, dtype=np.float32)


class FastEmbedder:
    kind = "fastembed-mml6"
    min_similarity = 0.35

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    def embed(self, text: str) -> np.ndarray:
        return np.asarray(next(iter(self._model.embed([text]))), dtype=np.float32)

    def from_bytes(self, raw: bytes) -> np.ndarray:
        return np.frombuffer(raw, dtype=np.float32)


def create_embedder() -> Embedder:
    if os.environ.get("SENTINEL_EMBEDDINGS", "hash") == "fastembed":
        try:
            return FastEmbedder()
        except Exception as exc:  # package or model missing
            log.warning("fastembed unavailable (%s), using hash embeddings", exc)
    return HashEmbedder()
