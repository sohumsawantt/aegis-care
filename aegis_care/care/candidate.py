"""CARE stage C: local candidate discovery (proposal Section 5.5.1, 6.2).

Each runtime first traverses exact parent commitments. Where a lineage edge is
missing or withheld it searches its local sketch index using the receiver-scoped
seed sketch, then applies temporal, artifact-type, patient-token, and workflow
compatibility filters.

    score(s,v) = a*explicit_edge(s,v) + b*sim(z_s, z_v) + c*compat(...)

The latent component produces a *ranked candidate set only*. Nothing here
quarantines or repairs anything - that is the whole point of the design
(Section 4.2 "latent design decision").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ..config import CandidateScoringConfig
from ..memory.models import ArtifactType, MemoryArtifact
from ..memory.sketch import SketchEncoder
from ..policy.rbac import Role
from ..util.crypto import receiver_scoped_token
from .capsule import RecoveryCapsule, artifact_type_band, time_band

#: Which workflow stages may legitimately derive from which. Used by compat();
#: an identity-stage seed cannot have produced an earlier identity-stage artifact.
STAGE_ORDER = {"identity_stage": 0, "handover_stage": 1, "summary_stage": 2}


@dataclass
class Candidate:
    memory_key: str
    memory_commitment: str
    runtime: str
    score: float
    explicit: bool
    similarity: float
    compatibility: float
    reasons: List[str] = field(default_factory=list)

    def to_public_dict(self) -> Dict[str, Any]:
        """Coordinator-visible projection: opaque id and score only."""
        return {
            "memory_commitment": self.memory_commitment,
            "runtime": self.runtime,
            "score": round(self.score, 4),
            "explicit": self.explicit,
        }


class CandidateDiscoverer:
    """Runs entirely inside one runtime. Never returns content."""

    def __init__(self, encoder: SketchEncoder,
                 config: Optional[CandidateScoringConfig] = None) -> None:
        self.encoder = encoder
        self.config = config or CandidateScoringConfig()

    # ------------------------------------------------------------------
    def discover(
        self,
        runtime,
        capsule: RecoveryCapsule,
        *,
        incident_key: bytes,
        known_bad_commitments: Set[str],
        use_sketch: bool = True,
        use_explicit: bool = True,
        already_seen: Optional[Set[str]] = None,
    ) -> List[Candidate]:
        """Return ranked candidate descendants of the capsule's seed."""
        already_seen = already_seen or set()
        candidates: Dict[str, Candidate] = {}

        # --- exact lineage traversal ----------------------------------
        if use_explicit:
            for artifact in runtime.vault.all():
                if artifact.key in already_seen:
                    continue
                hits = set(artifact.explicit_parent_commitments) & known_bad_commitments
                if hits:
                    candidates[artifact.key] = Candidate(
                        memory_key=artifact.key,
                        memory_commitment=artifact.commitment(),
                        runtime=runtime.role.value,
                        score=self.config.a_explicit,
                        explicit=True,
                        similarity=1.0,
                        compatibility=1.0,
                        reasons=["explicit_parent_commitment"],
                    )

        # --- latent sketch candidates ---------------------------------
        if use_sketch:
            for artifact in runtime.vault.all():
                if artifact.key in already_seen or artifact.key in candidates:
                    continue
                sim = self._similarity(runtime, artifact, capsule)
                compat = self.compatibility(artifact, capsule, incident_key)
                score = (self.config.b_similarity * sim
                         + self.config.c_compatibility * compat)
                if compat == 0.0:
                    continue  # structurally impossible relationship
                if score >= self.config.tau_c:
                    candidates[artifact.key] = Candidate(
                        memory_key=artifact.key,
                        memory_commitment=artifact.commitment(),
                        runtime=runtime.role.value,
                        score=score,
                        explicit=False,
                        similarity=sim,
                        compatibility=compat,
                        reasons=["sketch_similarity", "compat_filter"],
                    )

        ranked = sorted(candidates.values(), key=lambda c: (-c.score, c.memory_key))
        return ranked[: self.config.max_candidates]

    # ------------------------------------------------------------------
    def _similarity(self, runtime, artifact: MemoryArtifact,
                    capsule: RecoveryCapsule) -> float:
        """Project the local artifact into the capsule's recipient scope so the
        two sketches are comparable. The projection is derived, never shipped."""
        local = self.encoder.sketch(
            artifact.content,
            recipient=capsule.recipient,
            purpose=capsule.purpose,
            incident_id=capsule.incident_id,
        )
        return self.encoder.similarity(local, capsule.sketch)

    def compatibility(self, artifact: MemoryArtifact, capsule: RecoveryCapsule,
                      incident_key: bytes) -> float:
        """compat(time, type, patient, workflow), evaluated inside the local
        policy boundary. Returns 0.0 for structurally impossible relations,
        which prunes them before any replay cost is paid."""
        score, checks = 0.0, 0

        # Temporal: a descendant cannot predate its ancestor's band.
        checks += 1
        seed_band = capsule.time_band
        art_band = time_band(artifact.created_at)
        if seed_band == "unknown" or art_band == "unknown":
            score += 0.5
        elif art_band >= seed_band:
            score += 1.0
        else:
            return 0.0

        # Workflow stage ordering: derivation flows identity -> handover -> summary.
        checks += 1
        seed_stage = STAGE_ORDER.get(capsule.artifact_type_band, 0)
        art_stage = STAGE_ORDER.get(artifact_type_band(artifact.artifact_type), 0)
        if art_stage > seed_stage:
            score += 1.0
        elif art_stage == seed_stage:
            score += 0.6   # same stage can still derive (cue -> refined cue)
        else:
            return 0.0

        # Patient token: local match against the receiver-scoped pseudonym.
        checks += 1
        if artifact.patient_scope:
            local_token = receiver_scoped_token(
                artifact.patient_scope, capsule.recipient, incident_key)
            score += 1.0 if local_token == capsule.patient_token else 0.15
        else:
            score += 0.5

        return score / checks


__all__ = ["CandidateDiscoverer", "Candidate", "STAGE_ORDER"]
