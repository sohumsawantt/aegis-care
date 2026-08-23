"""Receiver-scoped latent recovery sketches.

Section 5.4 and the "latent design decision" box constrain this component
tightly: a sketch is a *compressed, receiver-scoped candidate-discovery signal*.
It is never a trusted instruction, a clinical fact, or proof of causality, and
no hidden state or KV cache is ever transported.

Pipeline:
    text -> frozen hashing encoder (512d) -> receiver-keyed random projection
         -> L2 normalise -> int8 quantisation (64d)

The projection is keyed by (recipient, purpose, incident) so that the same
memory produces a different sketch for a different recipient. That is what
limits cross-recipient linkability by an honest-but-curious coordinator.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..config import SketchConfig
from ..util.crypto import hkdf

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    """Word unigrams/bigrams plus character 4-grams. Character grams matter
    because MRNs and patient ids are the discriminative surface form here."""
    lowered = text.lower()
    words = _TOKEN_RE.findall(lowered)
    grams = list(words)
    grams += [f"{a}_{b}" for a, b in zip(words, words[1:])]
    compact = re.sub(r"\s+", " ", lowered)
    grams += [compact[i:i + 4] for i in range(0, max(0, len(compact) - 3))]
    return grams


class SketchEncoder:
    """Frozen encoder + receiver-scoped projection.

    Deterministic and dependency-free by design: the proposal only requires "a
    frozen sentence embedding model with random projection and quantization",
    and a frozen hashing encoder gives byte-identical reproducibility across
    machines, which a downloaded transformer does not.
    """

    def __init__(self, config: Optional[SketchConfig] = None) -> None:
        self.config = config or SketchConfig()
        self._projections: Dict[str, np.ndarray] = {}

    # ------------------------------------------------------------------
    def encode_dense(self, text: str) -> np.ndarray:
        """Frozen hashing encoder with sublinear term weighting."""
        dim = self.config.hash_dim
        vec = np.zeros(dim, dtype=np.float64)
        counts: Dict[int, float] = {}
        for gram in _tokens(text):
            h = hash_gram(gram)
            idx = h % dim
            sign = 1.0 if (h >> 32) & 1 else -1.0
            counts[idx] = counts.get(idx, 0.0) + sign
        for idx, val in counts.items():
            vec[idx] = np.sign(val) * np.log1p(abs(val))
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    # ------------------------------------------------------------------
    def _projection(self, scope_key: str) -> np.ndarray:
        """Receiver-scoped random projection matrix, derived deterministically
        from the scope key so both sides compute the same matrix without
        transmitting it."""
        if scope_key not in self._projections:
            seed_bytes = hkdf(
                str(self.config.projection_seed).encode("utf-8"),
                f"projection|{scope_key}".encode("utf-8"),
                32,
            )
            seed = int.from_bytes(seed_bytes[:8], "big") % (2**32)
            rng = np.random.default_rng(seed)
            mat = rng.normal(0.0, 1.0 / np.sqrt(self.config.sketch_dim),
                             size=(self.config.hash_dim, self.config.sketch_dim))
            self._projections[scope_key] = mat
        return self._projections[scope_key]

    @staticmethod
    def scope_key(recipient: str, purpose: str, incident_id: str = "global") -> str:
        return f"{recipient}|{purpose}|{incident_id}"

    # ------------------------------------------------------------------
    def sketch(self, text: str, *, recipient: str, purpose: str = "incident_recovery",
               incident_id: str = "global") -> List[int]:
        """Produce the quantised, receiver-scoped sketch that may travel."""
        dense = self.encode_dense(text)
        projected = dense @ self._projection(self.scope_key(recipient, purpose, incident_id))
        norm = np.linalg.norm(projected)
        if norm > 0:
            projected = projected / norm
        scale = (2 ** (self.config.quant_bits - 1)) - 1
        return np.clip(np.rint(projected * scale), -scale, scale).astype(int).tolist()

    def local_sketch(self, text: str, owner: str) -> List[int]:
        """A runtime's own index sketch. Scoped to the owner so a stolen index
        is not directly comparable against another runtime's index."""
        return self.sketch(text, recipient=owner, purpose="local_index", incident_id="local")

    # ------------------------------------------------------------------
    @staticmethod
    def similarity(a: Sequence[int], b: Sequence[int]) -> float:
        """Cosine similarity mapped to [0, 1]."""
        va = np.asarray(a, dtype=np.float64)
        vb = np.asarray(b, dtype=np.float64)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float((np.dot(va, vb) / (na * nb) + 1.0) / 2.0)

    def bytes_per_sketch(self) -> int:
        """Capsule overhead accounting (Section 10, "capsule bytes")."""
        return self.config.sketch_dim * (self.config.quant_bits // 8)


def hash_gram(gram: str) -> int:
    """Stable 64-bit hash. Python's builtin hash() is salted per process, so a
    fixed FNV-1a is used to keep sketches reproducible across runs."""
    h = 0xCBF29CE484222325
    for byte in gram.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


class SketchIndex:
    """A runtime's local index of write-context sketches.

    Kept entirely inside the owning runtime: candidate search runs locally and
    only counts and opaque ids leave (Section 6.2).
    """

    def __init__(self, encoder: SketchEncoder, owner: str) -> None:
        self.encoder = encoder
        self.owner = owner
        self._entries: Dict[str, np.ndarray] = {}
        self.query_count = 0

    def add(self, key: str, text: str) -> List[int]:
        sketch = self.encoder.local_sketch(text, self.owner)
        self._entries[key] = np.asarray(sketch, dtype=np.float64)
        return sketch

    def remove(self, key: str) -> None:
        self._entries.pop(key, None)

    def query(self, text: str, top_k: int = 32) -> List[Tuple[str, float]]:
        """Rank local memories against a probe text. Returns (key, similarity)."""
        self.query_count += 1
        probe = np.asarray(self.encoder.local_sketch(text, self.owner), dtype=np.float64)
        scored = [
            (key, self.encoder.similarity(probe, vec))
            for key, vec in self._entries.items()
        ]
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:top_k]

    def query_vector(self, probe: Sequence[int], top_k: int = 32) -> List[Tuple[str, float]]:
        self.query_count += 1
        pv = np.asarray(probe, dtype=np.float64)
        scored = [(k, self.encoder.similarity(pv, v)) for k, v in self._entries.items()]
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["SketchEncoder", "SketchIndex", "hash_gram"]
