"""Provenance masking (proposal Section 8.3, RQ1).

Conditions committed by the proposal:
  * complete
  * random 20 / 40 / 60% edge loss
  * targeted removal of cross-role and semantic-summary edges

RQ1's hypothesis is that *targeted* loss of cross-role and semantic-derivation
edges harms provenance-only recovery more than random loss does. Masking removes
the edge from `explicit_parent_commitments` (what the operational system can
see) while the ground-truth graph retains it for scoring.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from ..memory.models import ArtifactType, MemoryArtifact
from ..policy.rbac import Role

#: Artifact types whose incoming edge is a *semantic summarisation* step rather
#: than a simple identifier hand-off. These are the edges the proposal singles
#: out as uniquely important.
SUMMARY_TYPES = frozenset({
    ArtifactType.CLINICAL_SUMMARY,
    ArtifactType.OBSERVATION_SUMMARY,
    ArtifactType.AGGREGATE,
})


@dataclass
class MaskResult:
    condition: str
    edges_before: int
    edges_removed: int
    removed: List[Tuple[str, str]]   # (child_key, parent_commitment)

    @property
    def edges_after(self) -> int:
        return self.edges_before - self.edges_removed

    @property
    def loss_fraction(self) -> float:
        return self.edges_removed / self.edges_before if self.edges_before else 0.0


class ProvenanceMask:
    """Applies an edge-visibility mask to an environment in place."""

    CONDITIONS = ("complete", "random20", "random40", "random60", "targeted")

    def __init__(self, env, seed: int = 20260729) -> None:
        self.env = env
        self.seed = seed

    # ------------------------------------------------------------------
    def apply(self, condition: str, scope_keys: Optional[Set[str]] = None) -> MaskResult:
        if condition not in self.CONDITIONS:
            raise ValueError(f"unknown provenance condition {condition}")

        artifacts = [a for a in self.env.all_artifacts()
                     if scope_keys is None or a.key in scope_keys]
        edges: List[Tuple[MemoryArtifact, str]] = [
            (a, parent) for a in artifacts for parent in a.explicit_parent_commitments
        ]
        result = MaskResult(condition=condition, edges_before=len(edges),
                            edges_removed=0, removed=[])

        if condition == "complete":
            return result

        if condition.startswith("random"):
            fraction = int(condition.replace("random", "")) / 100.0
            rng = random.Random(self.seed)
            # Deterministic ordering before sampling keeps runs reproducible.
            ordered = sorted(edges, key=lambda ap: (ap[0].key, ap[1]))
            n_remove = int(round(len(ordered) * fraction))
            doomed = set(rng.sample(range(len(ordered)), n_remove)) if n_remove else set()
            targets = [ordered[i] for i in sorted(doomed)]
        else:
            targets = [(a, p) for a, p in edges if self._is_targeted(a, p)]

        for artifact, parent in targets:
            if parent in artifact.explicit_parent_commitments:
                artifact.explicit_parent_commitments.remove(parent)
                result.removed.append((artifact.key, parent))
                result.edges_removed += 1

        self.env.ledger.log_event(None, "benchmark", "provenance_masked", None,
                                  {"condition": condition,
                                   "removed": result.edges_removed,
                                   "before": result.edges_before})
        return result

    # ------------------------------------------------------------------
    def _is_targeted(self, artifact: MemoryArtifact, parent_commitment: str) -> bool:
        """Cross-role edges and semantic-summary edges."""
        if artifact.artifact_type in SUMMARY_TYPES:
            return True
        parent = self.env.artifact_by_commitment(parent_commitment)
        if parent is not None and parent.owner != artifact.owner:
            return True   # cross-role edge
        return False

    # ------------------------------------------------------------------
    @staticmethod
    def describe(condition: str) -> str:
        return {
            "complete": "All derivation edges observed.",
            "random20": "20% of edges removed uniformly at random.",
            "random40": "40% of edges removed uniformly at random.",
            "random60": "60% of edges removed uniformly at random.",
            "targeted": "All cross-role and semantic-summary edges removed.",
        }[condition]


__all__ = ["ProvenanceMask", "MaskResult", "SUMMARY_TYPES"]
