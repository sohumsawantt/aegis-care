"""CARE stage R: clean-room recompilation (proposal Section 5.5.3).

A confirmed descendant is rebuilt from trusted, currently authorized FHIR
resources and unaffected parent memories. The replay recipe pins the tool
schema, prompt version, output schema, and task predicate.

Fail-closed is the governing rule (Section 7.1): if required support is
unavailable, contradictory, or outside the role's access rights, the item is
quarantined for human review instead of being guessed. The system never
fabricates a replacement merely to preserve utility.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from ..config import RepairConfig
from ..memory.models import ArtifactType, MemoryArtifact, MemoryState
from ..policy.rbac import FieldCategory, Operation
from ..util.crypto import commit


@dataclass
class RepairOutcome:
    memory_key: str
    action: str                       # repaired | quarantined
    new_key: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    checks: Dict[str, bool] = None    # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.checks is None:
            self.checks = {}


class Recompiler:
    """Rebuilds confirmed descendants inside their owning runtime."""

    def __init__(self, config: Optional[RepairConfig] = None) -> None:
        self.config = config or RepairConfig()

    # ------------------------------------------------------------------
    def recompile(
        self,
        runtime,
        artifact: MemoryArtifact,
        task: Dict[str, Any],
        incident_id: str,
        suspected: Set[str],
        message=None,
    ) -> RepairOutcome:
        """Rebuild one artifact from trusted sources, or quarantine it."""
        if artifact.replay_recipe is None:
            return self._quarantine(runtime, artifact, incident_id,
                                    "no replay recipe available", 0.0, {})

        # Clean-room replay: the suspected ancestors are withheld, so every
        # deriver falls back to its trusted-FHIR path.
        try:
            rebuilt = runtime.replay(artifact, task, message=message, exclude=suspected)
        except PermissionError as exc:
            return self._quarantine(runtime, artifact, incident_id,
                                    f"policy denied during rebuild: {exc}", 0.0, {})

        checks: Dict[str, bool] = {}
        facts = rebuilt.structured_facts

        # -- source check: the rebuild must have used a trusted route --------
        checks["trusted_source"] = rebuilt.route in ("fhir_lookup", "fhir_rebuild",
                                                     "upstream_agent")
        checks["resolved"] = bool(facts.get("resolved")) and facts.get("patient_id") is not None

        # -- identity check ---------------------------------------------------
        if self.config.require_identity_check:
            pid = facts.get("patient_id")
            checks["identity_consistent"] = bool(
                pid and runtime.fhir.read("Patient", pid) is not None
                and facts.get("mrn") in (None, runtime.fhir.patient_mrn(pid))
            )

        # -- schema check -----------------------------------------------------
        if self.config.require_schema_check:
            checks["schema_valid"] = self._schema_ok(artifact.artifact_type, facts)

        # -- task predicate check --------------------------------------------
        if self.config.require_task_predicate_check:
            checks["predicate_satisfiable"] = facts.get("patient_id") is not None

        # -- policy check on the rebuilt content ------------------------------
        decision = runtime.policy.check(
            runtime.role, Operation.RECOMPILE, FieldCategory.IDENTITY,
            purpose="incident_recovery", patient_id=facts.get("patient_id"))
        checks["policy_ok"] = bool(decision)

        confidence = sum(1.0 for v in checks.values() if v) / max(1, len(checks))

        if confidence < self.config.tau_r or not all(
                checks.get(k, True) for k in ("resolved", "policy_ok")):
            failed = sorted(k for k, v in checks.items() if not v)
            return self._quarantine(
                runtime, artifact, incident_id,
                f"reconstruction confidence {confidence:.2f} below tau_r "
                f"({self.config.tau_r}); failed checks: {failed}",
                confidence, checks)

        # -- publish the repaired version -------------------------------------
        repair_task = dict(task)
        repair_task["purpose"] = artifact.purpose
        new_version = runtime.vault.next_version(artifact.memory_id)
        repaired = runtime.write_memory(
            artifact.memory_id,
            artifact.artifact_type,
            rebuilt,
            repair_task,
            session_id=f"recovery::{incident_id}",
            true_parents=list(rebuilt.parent_commitments),
            version=new_version,
            supersedes=artifact.key,
            state=MemoryState.REPAIRED,
        )

        # The old version is superseded, not deleted (Section 11.2).
        runtime.vault.set_state(artifact.key, MemoryState.SUPERSEDED, incident_id,
                                f"superseded by {repaired.key}")
        runtime.ledger.log_event(incident_id, runtime.role.value, "recompiled",
                                 artifact.key, {"new_key": repaired.key,
                                                "confidence": round(confidence, 3),
                                                "checks": checks})
        return RepairOutcome(memory_key=artifact.key, action="repaired",
                             new_key=repaired.key, confidence=confidence,
                             reason="rebuilt from trusted FHIR sources", checks=checks)

    # ------------------------------------------------------------------
    def _quarantine(self, runtime, artifact: MemoryArtifact, incident_id: str,
                    reason: str, confidence: float, checks: Dict[str, bool]) -> RepairOutcome:
        runtime.vault.set_state(artifact.key, MemoryState.QUARANTINED, incident_id, reason)
        runtime.ledger.log_event(incident_id, runtime.role.value, "quarantined",
                                 artifact.key, {"reason": reason})
        return RepairOutcome(memory_key=artifact.key, action="quarantined",
                             confidence=confidence, reason=reason, checks=checks)

    # ------------------------------------------------------------------
    @staticmethod
    def _schema_ok(artifact_type: ArtifactType, facts: Dict[str, Any]) -> bool:
        required = {
            ArtifactType.IDENTITY_HINT: ("patient_id", "query_key"),
            ArtifactType.LOOKUP_STRATEGY: ("patient_id",),
            ArtifactType.HANDOVER: ("patient_id", "observation_ids"),
            ArtifactType.OBSERVATION_SUMMARY: ("patient_id", "observation_ids"),
            ArtifactType.CLINICAL_SUMMARY: ("patient_id", "observation_ids"),
            ArtifactType.AGGREGATE: ("patient_id", "metric_code"),
        }.get(artifact_type, ("patient_id",))
        return all(key in facts for key in required)


__all__ = ["Recompiler", "RepairOutcome"]
