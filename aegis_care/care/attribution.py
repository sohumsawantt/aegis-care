"""CARE stage A: attribution through local counterfactual replay.

Sections 5.5.2 and 6.3. For each candidate child v the owning runtime replays
its creation *without* the suspected seed, and influence is confirmed when the
replay materially changes a protected field, the selected patient, a FHIR
resource, a structured fact, or a downstream action:

    I(s -> v) = w1*semantic_delta + w2*patient_change + w3*resource_or_action_change

confirmed when I > tau_i, or when an exact deterministic predicate changes.

This stage is what separates *causal inheritance* from *mere semantic
similarity* (RQ3), and it is the reason latent discovery can be used safely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from ..config import InfluenceConfig
from ..memory.models import MemoryArtifact
from ..memory.sketch import SketchEncoder
from ..util.crypto import KeyRing
from .capsule import SignedVerdict, band_for


@dataclass
class InfluenceReport:
    memory_key: str
    influence_score: float
    semantic_delta: float
    patient_change: float
    resource_change: float
    predicate_changed: bool
    confirmed: bool
    original_patient: Optional[str]
    counterfactual_patient: Optional[str]
    replay_available: bool
    detail: Dict[str, Any]


class AttributionEngine:
    """Local counterfactual replay. Runs only inside the owning runtime."""

    def __init__(self, encoder: SketchEncoder, keyring: KeyRing,
                 config: Optional[InfluenceConfig] = None) -> None:
        self.encoder = encoder
        self.keyring = keyring
        self.config = config or InfluenceConfig()

    # ------------------------------------------------------------------
    def assess(
        self,
        runtime,
        artifact: MemoryArtifact,
        task: Dict[str, Any],
        suspected: Set[str],
        message=None,
    ) -> InfluenceReport:
        """Replay `artifact` with `suspected` ancestors withheld and measure the
        change against the artifact as it actually exists."""
        if artifact.replay_recipe is None:
            # Fail-closed: a missing recipe cannot be confirmed *or* cleared,
            # so it is escalated rather than silently retained (Section 7.1).
            return InfluenceReport(
                memory_key=artifact.key, influence_score=1.0, semantic_delta=0.0,
                patient_change=0.0, resource_change=0.0, predicate_changed=False,
                confirmed=True, original_patient=artifact.patient_scope,
                counterfactual_patient=None, replay_available=False,
                detail={"reason": "missing_replay_recipe"},
            )

        counterfactual = runtime.replay(artifact, task, message=message, exclude=suspected)

        original_facts = artifact.structured_facts
        cf_facts = counterfactual.structured_facts

        # --- w2: safety-critical predicate change -----------------------
        # Section 5.5.2 lists five ways influence shows itself: a change to a
        # protected field, the selected patient, a FHIR resource, a structured
        # fact, or a downstream action. The patient and the protected-field
        # checks are the two that are categorically unsafe, so they share w2.
        orig_patient = original_facts.get("patient_id")
        cf_patient = cf_facts.get("patient_id")
        patient_change = 1.0 if orig_patient != cf_patient else 0.0

        protected_change = self._protected_field_change(original_facts, cf_facts)

        # --- w3: resource / structured-fact / action change --------------
        resource_change = max(
            self._resource_id_change(original_facts, cf_facts),
            self._structured_fact_change(original_facts, cf_facts),
        )

        # --- w1: semantic delta -----------------------------------------
        sim = self.encoder.similarity(
            self.encoder.local_sketch(artifact.content, runtime.role.value),
            self.encoder.local_sketch(counterfactual.content, runtime.role.value),
        )
        semantic_delta = max(0.0, 1.0 - sim)

        score = (self.config.w1_semantic * semantic_delta
                 + self.config.w2_patient * max(patient_change, protected_change)
                 + self.config.w3_resource * resource_change)

        # Deterministic predicates confirm regardless of the continuous score.
        # Section 5.5.2 enumerates five: a protected field, the selected
        # patient, a FHIR resource, a structured fact, or a downstream action.
        # All five are exact, so all five confirm; tau_i is the *additional*
        # soft path for a case where only the prose moved.
        predicate_changed = bool(
            patient_change > 0.0
            or protected_change > 0.0
            or resource_change > 0.0
            or artifact.replay_recipe.task_predicate.get("selected_patient") != cf_patient
        )

        confirmed = score >= self.config.tau_i or (
            self.config.hard_predicate_confirms and predicate_changed)

        orig_res = self._resource_ids(original_facts)
        cf_res = self._resource_ids(cf_facts)

        return InfluenceReport(
            memory_key=artifact.key,
            influence_score=round(score, 4),
            semantic_delta=round(semantic_delta, 4),
            patient_change=patient_change,
            resource_change=round(resource_change, 4),
            predicate_changed=predicate_changed,
            confirmed=confirmed,
            original_patient=orig_patient,
            counterfactual_patient=cf_patient,
            replay_available=True,
            detail={
                "counterfactual_route": counterfactual.route,
                "protected_field_change": protected_change,
                "resources_lost": sorted(orig_res - cf_res)[:8],
                "resources_gained": sorted(cf_res - orig_res)[:8],
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _resource_ids(facts: Dict[str, Any]) -> Set[str]:
        return set(facts.get("observation_ids", [])) | set(facts.get("condition_ids", []))

    @classmethod
    def _resource_id_change(cls, original: Dict[str, Any],
                            counterfactual: Dict[str, Any]) -> float:
        """Jaccard distance over the FHIR resources the artifact cites."""
        a, b = cls._resource_ids(original), cls._resource_ids(counterfactual)
        union = a | b
        if not union:
            return 0.0
        return 1.0 - (len(a & b) / len(union))

    @staticmethod
    def _structured_fact_change(original: Dict[str, Any],
                                counterfactual: Dict[str, Any]) -> float:
        """Fraction of asserted structured facts whose *value* changed.

        This is what catches family F4: after a record correction the resource
        ids are identical, but the values the memory asserts are stale. Without
        this term a stale-fact descendant looks uninfluenced.
        """
        a_values = original.get("values") or {}
        b_values = counterfactual.get("values") or {}
        keys = set(a_values) | set(b_values)
        if keys:
            differing = sum(1 for k in keys if a_values.get(k) != b_values.get(k))
            return differing / len(keys)
        # Scalar aggregates carry a single value instead of a dict.
        if "value" in original or "value" in counterfactual:
            return 0.0 if original.get("value") == counterfactual.get("value") else 1.0
        return 0.0

    @staticmethod
    def _protected_field_change(original: Dict[str, Any],
                                counterfactual: Dict[str, Any]) -> float:
        """Did the replay change which protected fields the artifact carries?

        This is what catches family F3: access-scope laundering changes neither
        the patient nor the resource set, but withholding the suspected context
        removes the physician-only material that was copied forward. Section
        5.5.2 names a protected-field change first among the confirming
        conditions, so it is treated as a hard predicate.
        """
        a_flag = bool(original.get("laundered_restricted"))
        b_flag = bool(counterfactual.get("laundered_restricted"))
        if a_flag != b_flag:
            return 1.0
        a_ids = set(original.get("restricted_ids", []))
        b_ids = set(counterfactual.get("restricted_ids", []))
        return 1.0 if a_ids != b_ids else 0.0

    # ------------------------------------------------------------------
    def sign_verdict(self, runtime, incident_id: str, artifact: MemoryArtifact,
                     report: InfluenceReport, disposition: str) -> SignedVerdict:
        """Package the verdict for the coordinator.

        Note what is *not* here: no original text, no counterfactual text, no
        patient identifier, no observation values. Only a band, a score, a
        boolean, and a disposition.
        """
        verdict = SignedVerdict(
            incident_id=incident_id,
            memory_commitment=artifact.commitment(),
            runtime=runtime.role.value,
            influence_band=band_for(report.influence_score, report.predicate_changed),
            influence_score=report.influence_score,
            predicate_changed=report.predicate_changed,
            disposition=disposition,
            evidence={
                "replay_available": report.replay_available,
                "semantic_delta_band": _delta_band(report.semantic_delta),
                "resource_change_band": _delta_band(report.resource_change),
            },
        )
        verdict.signature = self.keyring.sign(runtime.role.value, verdict.signable())
        return verdict


def _delta_band(value: float) -> str:
    """Coarsen a continuous delta so the coordinator learns magnitude, not detail."""
    if value >= 0.6:
        return "high"
    if value >= 0.3:
        return "medium"
    if value > 0.0:
        return "low"
    return "none"


__all__ = ["AttributionEngine", "InfluenceReport"]
