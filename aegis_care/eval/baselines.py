"""The nine recovery conditions (proposal Section 9).

  A  No recovery                   measures residual harm and ordinary utility
  B  Delete confirmed seed only    tests whether descendants survive local cleanup
  C  Full memory reset             safety-heavy fallback, lower bound on utility
  D  Explicit-lineage quarantine   strong privacy-respecting baseline (MemLineage-style)
  E  Explicit-lineage clean replay separates recompilation from latent discovery
  F  Sketch-only quarantine        false positives when similarity is treated as cause
  G  Central raw-content oracle    non-private upper comparator
  H  Complete private oracle graph unattainable provenance upper bound
  I  AEGIS-Care / full CARE        lineage + sketches + attribution + repair + enforcement

Every condition consumes the same frozen snapshot, the same seed, the same mask,
and the same follow-up tasks (Section 9.1 step 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from ..care.coordinator import CAREOptions, RecoveryCoordinator, RecoveryResult
from ..memory.models import MemoryState
from ..policy.rbac import Role

CONDITION_INFO = {
    "A": ("No recovery", "Measures residual harm and ordinary task utility."),
    "B": ("Delete confirmed seed only", "Tests whether descendants survive local cleanup."),
    "C": ("Full memory reset", "Safety-heavy fallback and lower bound on retained utility."),
    "D": ("Explicit-lineage quarantine",
          "Strong privacy-respecting baseline with no missing-edge reconstruction."),
    "E": ("Explicit-lineage clean replay",
          "Separates recompilation value from latent candidate discovery."),
    "F": ("Sketch-only quarantine",
          "Measures false positives when semantic candidates are treated as causality."),
    "G": ("Central raw-content oracle",
          "Non-private upper comparator for discovery and reconstruction."),
    "H": ("Complete private oracle graph", "Unattainable provenance upper bound."),
    "I": ("AEGIS-Care / full CARE",
          "Lineage + sketches + local attribution + recompilation + enforcement."),
}

CONDITIONS = tuple(CONDITION_INFO.keys())


@dataclass
class ConditionOutcome:
    """Uniform result shape so every condition can be scored identically."""

    condition: str
    incident_id: str
    withdrawn: Set[str] = field(default_factory=set)     # made non-servable
    repaired: Set[str] = field(default_factory=set)      # rebuilt to a new version
    touched: Set[str] = field(default_factory=set)       # destructively acted on
    cleared: Set[str] = field(default_factory=set)       # examined and retained
    overhead: Dict[str, Any] = field(default_factory=dict)
    certificate: Optional[Any] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    recovery: Optional[RecoveryResult] = None


class BaselineRunner:
    """Executes one recovery condition against a prepared incident."""

    def __init__(self, env) -> None:
        self.env = env

    # ------------------------------------------------------------------
    def run(self, condition: str, incident, *,
            followup_tasks: Optional[List[Dict[str, Any]]] = None) -> ConditionOutcome:
        if condition not in CONDITION_INFO:
            raise ValueError(f"unknown condition {condition}")
        self.env.reset_counters()
        handler: Callable = getattr(self, f"_run_{condition.lower()}")
        outcome = handler(incident, followup_tasks or [])
        outcome.overhead = {**self.env.overhead(), **outcome.overhead}
        return outcome

    # ------------------------------------------------------------------
    # A - no recovery
    # ------------------------------------------------------------------
    def _run_a(self, incident, followup) -> ConditionOutcome:
        return ConditionOutcome(condition="A", incident_id=incident.incident_id,
                                detail={"note": "no action taken"})

    # ------------------------------------------------------------------
    # B - delete the confirmed seed only
    # ------------------------------------------------------------------
    def _run_b(self, incident, followup) -> ConditionOutcome:
        seed = self.env.find_artifact(incident.seed_key)
        runtime = self.env.runtime(seed.owner)
        runtime.vault.set_state(seed.key, MemoryState.TOMBSTONED,
                                incident.incident_id, "seed deleted")
        return ConditionOutcome(
            condition="B", incident_id=incident.incident_id,
            withdrawn={seed.key}, touched={seed.key},
            detail={"note": "only the flagged seed removed; descendants untouched"})

    # ------------------------------------------------------------------
    # C - full memory reset
    # ------------------------------------------------------------------
    def _run_c(self, incident, followup) -> ConditionOutcome:
        withdrawn: Set[str] = set()
        for runtime in self.env.runtimes.values():
            for artifact in list(runtime.vault.all()):
                if artifact.is_servable():
                    runtime.vault.set_state(artifact.key, MemoryState.TOMBSTONED,
                                            incident.incident_id, "full memory reset")
                    withdrawn.add(artifact.key)
        return ConditionOutcome(
            condition="C", incident_id=incident.incident_id,
            withdrawn=withdrawn, touched=set(withdrawn),
            detail={"note": "all memory wiped, including clean state"})

    # ------------------------------------------------------------------
    # D / E / F / I - CARE with different stages enabled
    # ------------------------------------------------------------------
    def _run_care(self, incident, followup, condition: str,
                  options: CAREOptions) -> ConditionOutcome:
        coordinator = RecoveryCoordinator(self.env)
        result = coordinator.recover(incident.incident_id, [incident.seed_key],
                                     options=options, followup_tasks=followup)
        repaired = {r["memory_key"] for r in result.repaired}
        quarantined = {q["memory_key"] for q in result.quarantined}
        return ConditionOutcome(
            condition=condition, incident_id=incident.incident_id,
            withdrawn={incident.seed_key} | repaired | quarantined,
            repaired=repaired,
            touched=repaired | quarantined | {incident.seed_key},
            cleared=set(result.cleared),
            overhead=dict(result.overhead),
            certificate=result.certificate,
            recovery=result,
            detail={"rounds": result.rounds, "closure": result.closure_reached,
                    "candidates": len(result.candidates_considered)},
        )

    def _run_d(self, incident, followup) -> ConditionOutcome:
        """Explicit lineage, quarantine only: no sketches, no rebuild."""
        return self._run_care(incident, followup, "D", CAREOptions(
            use_sketch=False, use_explicit_lineage=True, use_counterfactual=True,
            use_recompilation=False, use_enforcement=True, use_scoping=True))

    def _run_e(self, incident, followup) -> ConditionOutcome:
        """Explicit lineage plus clean replay, still no latent discovery."""
        return self._run_care(incident, followup, "E", CAREOptions(
            use_sketch=False, use_explicit_lineage=True, use_counterfactual=True,
            use_recompilation=True, use_enforcement=True, use_scoping=True))

    def _run_f(self, incident, followup) -> ConditionOutcome:
        """Sketch-only quarantine: similarity treated as causality."""
        return self._run_care(incident, followup, "F", CAREOptions(
            use_sketch=True, use_explicit_lineage=False, use_counterfactual=False,
            use_recompilation=False, use_enforcement=True, use_scoping=True))

    def _run_i(self, incident, followup) -> ConditionOutcome:
        """Full CARE."""
        return self._run_care(incident, followup, "I", CAREOptions())

    # ------------------------------------------------------------------
    # G - central raw-content oracle
    # ------------------------------------------------------------------
    def _run_g(self, incident, followup) -> ConditionOutcome:
        """A central investigator reads every runtime's raw memory content.

        Accurate, and exactly the governance problem the proposal rejects: it is
        scored with a deliberately large unauthorized-exposure charge.
        """
        seed = self.env.find_artifact(incident.seed_key)
        exposed_fields: List[str] = []
        withdrawn: Set[str] = set()

        # Centralise every memory's raw content, then match on the seed's patient.
        centralised = []
        for runtime in self.env.runtimes.values():
            for artifact in runtime.vault.all():
                centralised.append((runtime, artifact))
                exposed_fields.append(f"{artifact.key}:content")

        target_patient = seed.structured_facts.get("patient_id")
        for runtime, artifact in centralised:
            if not artifact.is_servable() or artifact.key == seed.key:
                continue
            same_patient = artifact.structured_facts.get("patient_id") == target_patient
            derived = artifact.memory_id.rsplit("-", 1)[0] == seed.memory_id.rsplit("-", 1)[0]
            if same_patient and derived:
                runtime.vault.set_state(artifact.key, MemoryState.QUARANTINED,
                                        incident.incident_id, "oracle raw-content match")
                withdrawn.add(artifact.key)

        runtime = self.env.runtime(seed.owner)
        runtime.vault.set_state(seed.key, MemoryState.TOMBSTONED,
                                incident.incident_id, "oracle seed removal")
        withdrawn.add(seed.key)

        return ConditionOutcome(
            condition="G", incident_id=incident.incident_id,
            withdrawn=withdrawn, touched=set(withdrawn),
            detail={"note": "centralised raw clinical content",
                    "raw_memories_read": len(centralised),
                    "exposed_fields": len(exposed_fields)})

    # ------------------------------------------------------------------
    # H - complete private oracle graph
    # ------------------------------------------------------------------
    def _run_h(self, incident, followup) -> ConditionOutcome:
        """Uses the private ground-truth graph directly.

        Unattainable in operation; it defines oracle regret. It is the only
        condition permitted to read `env.truth`.
        """
        truth = self.env.truth
        targets = truth.true_contaminated_descendants(incident.seed_key)
        repaired: Set[str] = set()
        quarantined: Set[str] = set()

        coordinator = RecoveryCoordinator(self.env)
        seed = self.env.find_artifact(incident.seed_key)
        known_bad = {seed.commitment()}
        runtime_seed = self.env.runtime(seed.owner)
        runtime_seed.vault.set_state(seed.key, MemoryState.TOMBSTONED,
                                     incident.incident_id, "oracle seed removal")

        for key in coordinator._order_for_repair(
                [(self.env.runtime(self.env.find_artifact(k).owner), self.env.find_artifact(k))
                 for k in sorted(targets) if self.env.find_artifact(k)]):
            runtime, artifact = key
            known_bad.add(artifact.commitment())
            task = coordinator._task_for(artifact)
            outcome = coordinator.recompiler.recompile(
                runtime, artifact, task, incident.incident_id, known_bad,
                message=coordinator._clean_message_for(runtime, artifact, task, known_bad))
            (repaired if outcome.action == "repaired" else quarantined).add(outcome.memory_key)

        return ConditionOutcome(
            condition="H", incident_id=incident.incident_id,
            withdrawn={seed.key} | repaired | quarantined,
            repaired=repaired,
            touched={seed.key} | repaired | quarantined,
            detail={"note": "oracle used the complete private lineage graph",
                    "oracle_targets": len(targets)})


__all__ = ["BaselineRunner", "ConditionOutcome", "CONDITIONS", "CONDITION_INFO"]
