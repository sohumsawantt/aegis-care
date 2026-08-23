"""Frozen configuration for AEGIS-Care.

Every threshold and weight referenced by the proposal lives here so that the
"freeze thresholds on development families, then run the test families" protocol
(Section 9.3) is a single auditable object rather than scattered constants.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"


@dataclass(frozen=True)
class CandidateScoringConfig:
    """Weights for score(s, v) = a*explicit + b*sim + c*compat  (Section 6.2)."""

    a_explicit: float = 1.0
    b_similarity: float = 0.55
    c_compatibility: float = 0.45
    tau_c: float = 0.42          # candidate threshold
    max_candidates: int = 24     # candidate budget per runtime per frontier round


@dataclass(frozen=True)
class InfluenceConfig:
    """Weights for I(s -> v) = w1*semantic + w2*patient + w3*resource/action (Section 6.3)."""

    w1_semantic: float = 0.34
    w2_patient: float = 0.46
    w3_resource: float = 0.20
    tau_i: float = 0.30          # influence confirmation threshold
    # A change in a deterministic safety predicate (selected patient / resource)
    # confirms influence regardless of tau_i.
    hard_predicate_confirms: bool = True


@dataclass(frozen=True)
class RepairConfig:
    tau_r: float = 0.75          # repair-confidence floor; below this -> quarantine
    require_identity_check: bool = True
    require_schema_check: bool = True
    require_task_predicate_check: bool = True


@dataclass(frozen=True)
class SketchConfig:
    """Receiver-scoped latent sketch (Section 5.4 / scope-cut item 3)."""

    hash_dim: int = 512          # frozen hashing-encoder width
    sketch_dim: int = 64         # projected sketch width
    quant_bits: int = 8          # int8 quantisation
    projection_seed: int = 20260729


@dataclass(frozen=True)
class ObjectiveWeights:
    """J = ls*RWH + lu*(1-BSR) + lp*UER + lr*DRR + lc*Cost   (Section 6.4)."""

    lambda_s: float = 0.40
    lambda_u: float = 0.25
    lambda_p: float = 0.20
    lambda_r: float = 0.10
    lambda_c: float = 0.05


@dataclass(frozen=True)
class CapsuleConfig:
    default_expiry_seconds: int = 900
    max_query_budget: int = 64   # sketch queries a recipient may answer per incident


@dataclass(frozen=True)
class AegisConfig:
    seed: int = 20260729
    candidate: CandidateScoringConfig = field(default_factory=CandidateScoringConfig)
    influence: InfluenceConfig = field(default_factory=InfluenceConfig)
    repair: RepairConfig = field(default_factory=RepairConfig)
    sketch: SketchConfig = field(default_factory=SketchConfig)
    objective: ObjectiveWeights = field(default_factory=ObjectiveWeights)
    capsule: CapsuleConfig = field(default_factory=CapsuleConfig)

    # Environment size (Section 8.3 committed dataset size).
    n_patients: int = 100
    n_base_tasks: int = 24
    propagation_depths: tuple = (1, 2, 3, 4)

    model_name: str = "aegis-deterministic-clinical-v1"
    embedding_name: str = "aegis-frozen-hash-encoder-v1"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=list)


CONFIG = AegisConfig()

__all__ = [
    "AegisConfig", "CONFIG", "CandidateScoringConfig", "InfluenceConfig",
    "RepairConfig", "SketchConfig", "ObjectiveWeights", "CapsuleConfig",
    "PROJECT_ROOT", "DATA_DIR", "RESULTS_DIR",
]
