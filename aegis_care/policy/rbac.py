"""Deterministic role/purpose/patient/operation policy.

Section 12.1 requires that authorisation be "deterministic RBAC/ABAC rules
adapted from EICU-AC", explicitly separated from LLM judgement. Nothing in this
module consults a model: a decision is a pure function of the request.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Set


class Role(str, Enum):
    REGISTRATION = "registration"          # administration / identity desk
    NURSING = "nursing"                    # handover and observations
    CLINICAL_SUMMARY = "clinical_summary"  # authorized cross-record summary
    COORDINATOR = "coordinator"            # recovery orchestration, no clinical read
    REVIEWER = "reviewer"                  # human-in-the-loop override


class Operation(str, Enum):
    READ = "read"
    SEARCH = "search"
    WRITE_MEMORY = "write_memory"
    READ_MEMORY = "read_memory"
    REPLAY = "replay"
    RECOMPILE = "recompile"


class FieldCategory(str, Enum):
    IDENTITY = "identity"              # name, MRN, DOB, gender
    DEMOGRAPHIC = "demographic"        # address, contact
    VITALS = "vitals"
    LABORATORY = "laboratory"
    CONDITION = "condition"
    NOTE = "note"
    RESTRICTED = "restricted"          # behavioural health, substance use
    MEDICATION = "medication"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    role: Role
    category: Optional[FieldCategory] = None
    purpose: Optional[str] = None

    def __bool__(self) -> bool:
        return self.allowed


# EICU-AC inspired matrix. Physician-only restricted fields are unavailable to
# every automated principal in this project, which is why access-scope
# laundering (family F3) is a genuine violation rather than a policy nuance.
ROLE_FIELD_MATRIX: Dict[Role, FrozenSet[FieldCategory]] = {
    Role.REGISTRATION: frozenset({
        FieldCategory.IDENTITY,
        FieldCategory.DEMOGRAPHIC,
    }),
    Role.NURSING: frozenset({
        FieldCategory.IDENTITY,
        FieldCategory.VITALS,
        FieldCategory.LABORATORY,
        FieldCategory.CONDITION,
        FieldCategory.NOTE,
    }),
    Role.CLINICAL_SUMMARY: frozenset({
        FieldCategory.IDENTITY,
        FieldCategory.VITALS,
        FieldCategory.LABORATORY,
        FieldCategory.CONDITION,
        FieldCategory.NOTE,
        FieldCategory.MEDICATION,
    }),
    # The coordinator is honest-but-curious and holds no clinical read rights at
    # all. This is the hard constraint behind objective J (Section 6.4).
    Role.COORDINATOR: frozenset(),
    Role.REVIEWER: frozenset({
        FieldCategory.IDENTITY,
        FieldCategory.VITALS,
        FieldCategory.LABORATORY,
        FieldCategory.CONDITION,
        FieldCategory.NOTE,
    }),
}

ROLE_OPERATIONS: Dict[Role, FrozenSet[Operation]] = {
    Role.REGISTRATION: frozenset({Operation.READ, Operation.SEARCH, Operation.WRITE_MEMORY,
                                  Operation.READ_MEMORY, Operation.REPLAY, Operation.RECOMPILE}),
    Role.NURSING: frozenset({Operation.READ, Operation.SEARCH, Operation.WRITE_MEMORY,
                             Operation.READ_MEMORY, Operation.REPLAY, Operation.RECOMPILE}),
    Role.CLINICAL_SUMMARY: frozenset({Operation.READ, Operation.SEARCH, Operation.WRITE_MEMORY,
                                      Operation.READ_MEMORY, Operation.REPLAY, Operation.RECOMPILE}),
    Role.COORDINATOR: frozenset(),
    Role.REVIEWER: frozenset({Operation.READ_MEMORY}),
}

VALID_PURPOSES = frozenset({
    "patient_registration",
    "shift_handover",
    "care_summary",
    "incident_recovery",
    "human_review",
})

# Which purposes a role may legitimately act under.
ROLE_PURPOSES: Dict[Role, FrozenSet[str]] = {
    Role.REGISTRATION: frozenset({"patient_registration", "incident_recovery"}),
    Role.NURSING: frozenset({"shift_handover", "incident_recovery"}),
    Role.CLINICAL_SUMMARY: frozenset({"care_summary", "incident_recovery"}),
    Role.COORDINATOR: frozenset({"incident_recovery"}),
    Role.REVIEWER: frozenset({"human_review"}),
}


def categorize_resource(resource: Dict) -> FieldCategory:
    """Map a FHIR resource onto the policy field category it belongs to."""
    if resource.get("_aegisRestricted"):
        return FieldCategory.RESTRICTED
    rtype = resource.get("resourceType")
    if rtype == "Patient":
        return FieldCategory.IDENTITY
    if rtype == "MedicationRequest":
        return FieldCategory.MEDICATION
    if rtype == "Condition":
        return FieldCategory.CONDITION
    if rtype == "Observation":
        for cat in resource.get("category", []):
            for coding in cat.get("coding", []):
                if coding.get("code") == "vital-signs":
                    return FieldCategory.VITALS
                if coding.get("code") == "laboratory":
                    return FieldCategory.LABORATORY
        return FieldCategory.LABORATORY
    return FieldCategory.NOTE


@dataclass
class PolicyEngine:
    """Evaluates every access in the system. Deliberately boring and total."""

    patient_scopes: Dict[Role, Set[str]] = field(default_factory=dict)
    denials: list = field(default_factory=list)

    def grant_patient_scope(self, role: Role, patient_ids: Set[str]) -> None:
        self.patient_scopes.setdefault(role, set()).update(patient_ids)

    def in_patient_scope(self, role: Role, patient_id: str) -> bool:
        scope = self.patient_scopes.get(role)
        # No declared scope means "no patient-level restriction beyond the role
        # matrix", which matches the benchmark's per-task authorisation model.
        return True if scope is None else patient_id in scope

    def check(
        self,
        role: Role,
        operation: Operation,
        category: FieldCategory,
        *,
        purpose: str,
        patient_id: Optional[str] = None,
    ) -> Decision:
        if operation not in ROLE_OPERATIONS.get(role, frozenset()):
            return self._deny(role, category, purpose,
                              f"role {role.value} may not perform {operation.value}")
        if purpose not in VALID_PURPOSES:
            return self._deny(role, category, purpose, f"unknown purpose {purpose}")
        if purpose not in ROLE_PURPOSES.get(role, frozenset()):
            return self._deny(role, category, purpose,
                              f"role {role.value} may not act under purpose {purpose}")
        if category not in ROLE_FIELD_MATRIX.get(role, frozenset()):
            return self._deny(role, category, purpose,
                              f"role {role.value} has no rights over {category.value}")
        if patient_id is not None and not self.in_patient_scope(role, patient_id):
            return self._deny(role, category, purpose,
                              f"patient {patient_id} outside declared scope for {role.value}")
        return Decision(True, "permitted", role, category, purpose)

    def filter_resources(self, role: Role, resources: list, *, purpose: str) -> list:
        """Drop every resource the role may not see. Used on each FHIR read so a
        runtime physically cannot hold data it is not entitled to."""
        allowed = []
        for res in resources:
            cat = categorize_resource(res)
            patient_ref = res.get("subject", {}).get("reference", "")
            pid = patient_ref.split("/")[-1] if patient_ref else res.get("id")
            if self.check(role, Operation.READ, cat, purpose=purpose, patient_id=pid):
                allowed.append(res)
        return allowed

    def _deny(self, role, category, purpose, reason) -> Decision:
        decision = Decision(False, reason, role, category, purpose)
        self.denials.append({
            "role": role.value,
            "category": category.value if category else None,
            "purpose": purpose,
            "reason": reason,
        })
        return decision


__all__ = [
    "Role", "Operation", "FieldCategory", "Decision", "PolicyEngine",
    "categorize_resource", "ROLE_FIELD_MATRIX", "ROLE_OPERATIONS",
    "ROLE_PURPOSES", "VALID_PURPOSES",
]
