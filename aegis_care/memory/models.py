"""The versioned memory artifact (proposal Section 5.3).

Treating agent memory as a versioned dataflow program means every durable write
carries: stable identity, parent commitments, a policy boundary, a sketch, a
replay recipe, a lifecycle state, and a signature.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from ..policy.rbac import Role
from ..util.crypto import commit, commit_text


class MemoryState(str, Enum):
    """Section 6.1: q(v) in {active, suspected, quarantined, superseded,
    repaired, tombstoned}."""

    ACTIVE = "active"
    SUSPECTED = "suspected"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"
    REPAIRED = "repaired"
    TOMBSTONED = "tombstoned"


#: States that may still be served to a task. Everything else is non-servable.
SERVABLE_STATES = frozenset({MemoryState.ACTIVE, MemoryState.REPAIRED})

#: Ordering used to enforce the monotone incident frontier (Section 6.6). An
#: artifact may only move forward through this ordering inside one transaction.
_STATE_RANK = {
    MemoryState.ACTIVE: 0,
    MemoryState.SUSPECTED: 1,
    MemoryState.QUARANTINED: 2,
    MemoryState.SUPERSEDED: 2,
    MemoryState.REPAIRED: 3,
    MemoryState.TOMBSTONED: 4,
}


def state_transition_allowed(current: MemoryState, target: MemoryState) -> bool:
    """A repaired artifact is published as a *new* version, so an existing
    artifact can never slide backwards to ACTIVE within an incident."""
    if current == target:
        return True
    return _STATE_RANK[target] > _STATE_RANK[current]


class ArtifactType(str, Enum):
    IDENTITY_HINT = "identity_hint"        # resolved patient alias / lookup cue
    LOOKUP_STRATEGY = "lookup_strategy"    # prior search approach
    OBSERVATION_SUMMARY = "observation_summary"
    HANDOVER = "handover"
    CLINICAL_SUMMARY = "clinical_summary"
    AGGREGATE = "aggregate"
    WORKFLOW_NOTE = "workflow_note"
    PROCEDURE = "procedure"                # promoted experience -> procedure


@dataclass
class ReplayRecipe:
    """Everything needed to deterministically rebuild the artifact
    (Section 5.3 / 5.5.3): model + prompt + tool version, input commitments,
    output schema, and the task predicate that must hold afterwards."""

    task_id: str
    task_kind: str
    model_name: str
    prompt_version: str
    tool_schema_version: str
    input_commitments: Dict[str, str] = field(default_factory=dict)
    output_schema: str = "aegis.memory.v1"
    task_predicate: Dict[str, Any] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return commit(asdict(self), domain="replay-recipe")


@dataclass
class MemoryArtifact:
    memory_id: str
    version: int
    owner: Role
    artifact_type: ArtifactType

    # Content stays local. `content_ref` is what leaves the runtime; `content`
    # is held in the owning vault only.
    content: str
    content_ref: str = ""

    explicit_parent_commitments: List[str] = field(default_factory=list)
    # Ground-truth parents, recorded by benchmark instrumentation only
    # (Section 8.4). Recovery code must never read this field.
    _true_parents: List[str] = field(default_factory=list)

    patient_scope: Optional[str] = None
    role_scope: Optional[Role] = None
    purpose: str = ""

    write_context_sketch: Optional[List[int]] = None
    replay_recipe: Optional[ReplayRecipe] = None

    state: MemoryState = MemoryState.ACTIVE
    created_at: str = ""
    updated_at: str = ""
    signature: str = ""
    signed_by: str = ""

    # Provenance of a repair: which version this one supersedes.
    supersedes: Optional[str] = None
    quarantine_reason: Optional[str] = None
    session_id: str = ""

    # Structured facts the artifact asserts. These are the deterministic
    # predicates counterfactual replay compares (Section 6.3).
    structured_facts: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_ref:
            self.content_ref = commit_text(self.content)
        stamp = _dt.datetime.now(_dt.timezone.utc).isoformat()
        self.created_at = self.created_at or stamp
        self.updated_at = self.updated_at or stamp

    # ------------------------------------------------------------------
    @property
    def key(self) -> str:
        """Version-qualified identity used by tombstones and commitments."""
        return f"{self.memory_id}@v{self.version}"

    def commitment(self) -> str:
        """Opaque commitment binding identity + version + content ref."""
        return commit(
            {"memory_id": self.memory_id, "version": self.version, "content_ref": self.content_ref},
            domain="memory",
        )

    def is_servable(self) -> bool:
        return self.state in SERVABLE_STATES

    def signable_payload(self) -> Dict[str, Any]:
        """Immutable creation facts only.

        `state` is deliberately excluded: an artifact's lifecycle moves
        (active -> suspected -> quarantined ...) during recovery, and a
        signature that broke on every transition could not attest to the
        artifact's origin afterwards. State changes are separately recorded as
        signed ledger events, which is what the audit trail actually needs.
        """
        return {
            "memory_id": self.memory_id,
            "version": self.version,
            "owner": self.owner.value,
            "artifact_type": self.artifact_type.value,
            "content_ref": self.content_ref,
            "explicit_parent_commitments": sorted(self.explicit_parent_commitments),
            "patient_scope": self.patient_scope,
            "purpose": self.purpose,
            "recipe": self.replay_recipe.fingerprint() if self.replay_recipe else None,
            "supersedes": self.supersedes,
        }

    def to_public_dict(self) -> Dict[str, Any]:
        """Policy-safe projection: what may appear in a coordinator-visible log.
        Raw content and true parents are structurally absent."""
        return {
            "memory_id": self.memory_id,
            "version": self.version,
            "owner": self.owner.value,
            "artifact_type": self.artifact_type.value,
            "commitment": self.commitment(),
            "state": self.state.value,
            "created_at": self.created_at,
            "purpose": self.purpose,
            "supersedes": self.supersedes,
            "quarantine_reason": self.quarantine_reason,
        }

    def to_local_dict(self) -> Dict[str, Any]:
        """Full view, available only inside the owning runtime and to the UI
        acting on the owner's behalf."""
        data = self.to_public_dict()
        data.update({
            "content": self.content,
            "content_ref": self.content_ref,
            "patient_scope": self.patient_scope,
            "explicit_parent_commitments": self.explicit_parent_commitments,
            "structured_facts": self.structured_facts,
            "session_id": self.session_id,
            "has_sketch": self.write_context_sketch is not None,
            "recipe": asdict(self.replay_recipe) if self.replay_recipe else None,
        })
        return data


__all__ = [
    "MemoryArtifact", "MemoryState", "ArtifactType", "ReplayRecipe",
    "SERVABLE_STATES", "state_transition_allowed",
]
