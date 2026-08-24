"""Guided presentation session.

The analyst console exposes every control at once, which is the wrong shape for
a live demonstration. This module drives the *same* system through a scripted
narrative instead: clean operation, poisoning, the failure of deletion, the cost
of reset, the CARE loop stage by stage, verification, comparison, and leakage.

Nothing here simulates or replays canned output. Every act calls the real
environment, the real coordinator, and the real metrics evaluator; the frozen
snapshot is restored between acts so each condition is scored on identical
state, exactly as the experiment runner does.
"""
from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from ..care.coordinator import CAREOptions, RecoveryCoordinator
from ..config import CONFIG
from ..environment import AegisEnvironment
from ..eval.baselines import CONDITION_INFO, BaselineRunner, ConditionOutcome
from ..eval.metrics import MetricsEvaluator
from ..eval.privacy import PrivacyAuditor
from ..incident.masks import ProvenanceMask
from ..incident.scenarios import FAMILY_INFO, ScenarioBuilder
from ..memory.models import MemoryState
from ..policy.rbac import ROLE_FIELD_MATRIX, Role

#: The acts, in order. The UI uses this to build its progress rail.
ACTS = [
    {"id": "intro", "title": "The Question",
     "subtitle": "One wrong memory, and everything that inherited it"},
    {"id": "system", "title": "The System",
     "subtitle": "Three role-separated agents over a FHIR sandbox"},
    {"id": "clean", "title": "Normal Operation",
     "subtitle": "The assistant resolves the right patient"},
    {"id": "poison", "title": "The Poisoning",
     "subtitle": "One corrupted association propagates"},
    {"id": "deletion", "title": "Why Deletion Fails",
     "subtitle": "Remove the seed; the descendants survive"},
    {"id": "reset", "title": "Why Reset Is Worse",
     "subtitle": "Safe, and it destroys everything useful"},
    {"id": "care", "title": "The CARE Loop",
     "subtitle": "Discover, confirm, rebuild, enforce"},
    {"id": "verify", "title": "Verification",
     "subtitle": "Certificate, probes, and safe resume"},
    {"id": "compare", "title": "All Nine Conditions",
     "subtitle": "Scored on identical frozen state"},
    {"id": "privacy", "title": "What Leaks",
     "subtitle": "We attack our own recovery interface"},
    {"id": "close", "title": "Contribution",
     "subtitle": "A third option between hoping and wiping"},
]


def _chain_from_trajectory(env: AegisEnvironment, trajectory, seed_key: Optional[str],
                           intended: str) -> List[Dict[str, Any]]:
    """Render one derivation chain with live artifact state."""
    nodes = []
    for node in trajectory.nodes:
        artifact = env.find_artifact(node.key)
        latest = None
        if artifact is not None:
            candidate = env.runtime(artifact.owner).vault.latest(artifact.memory_id)
            if candidate is not None and candidate.key != artifact.key:
                latest = candidate
        nodes.append({
            "key": node.key,
            "depth": node.depth,
            "role": node.role.value,
            "type": node.artifact_type.value,
            "patient": artifact.structured_facts.get("patient_id") if artifact else None,
            "intended_patient": intended,
            "wrong_patient": bool(
                artifact and artifact.structured_facts.get("patient_id") not in (None, intended)),
            "state": artifact.state.value if artifact else "missing",
            "servable": artifact.is_servable() if artifact else False,
            "is_seed": node.key == seed_key,
            "restricted": bool(
                artifact and artifact.structured_facts.get("laundered_restricted")),
            "content": (artifact.content if artifact else ""),
            "version": artifact.version if artifact else 0,
            "repaired_as": latest.key if latest is not None else None,
            "repaired_patient": (latest.structured_facts.get("patient_id")
                                 if latest is not None else None),
        })
    return nodes


class DemoSession:
    """One presentation run. Thread-safe because uvicorn serves concurrently."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.env: Optional[AegisEnvironment] = None
        self.incident = None
        self.snapshot: Optional[Dict[str, Any]] = None
        self.mask = None
        self.clean_chain: List[Dict[str, Any]] = []
        self.clean_probe: Dict[str, Any] = {}
        self.care_result = None
        self.settings: Dict[str, Any] = {}
        self.completed: List[str] = []

    # ==================================================================
    def configure(self, *, family: str = "F1", task_id: Optional[str] = None,
                  depth: int = 4, provenance: str = "targeted") -> Dict[str, Any]:
        """Reset everything and pin the scenario for this run."""
        with self.lock:
            env = AegisEnvironment()
            task = next((t for t in env.tasks if t["task_id"] == task_id), None) \
                if task_id else env.tasks[0]
            if task is None:
                raise ValueError(f"unknown task {task_id}")
            seed_depth = FAMILY_INFO[family]["seed_depth"]
            if depth < seed_depth + 1:
                raise ValueError(
                    f"family {family} seeds at depth {seed_depth}; needs depth >= {seed_depth + 1}")

            self.env = env
            self.incident = None
            self.snapshot = None
            self.mask = None
            self.clean_chain = []
            self.clean_probe = {}
            self.care_result = None
            self.completed = []
            self.settings = {
                "family": family, "task_id": task["task_id"], "depth": depth,
                "provenance": provenance, "intended_patient": task["patient_id"],
                "task_label": task["label"],
                "query": task["query"]["query_text"],
                "family_info": FAMILY_INFO[family],
            }
            return self.state()

    # ==================================================================
    def state(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "acts": ACTS,
                "completed": list(self.completed),
                "settings": dict(self.settings),
                "has_incident": self.incident is not None,
                "has_care": self.care_result is not None,
            }

    def _require_env(self) -> AegisEnvironment:
        if self.env is None:
            self.configure()
        return self.env  # type: ignore[return-value]

    def _mark(self, act: str) -> None:
        if act not in self.completed:
            self.completed.append(act)

    # ==================================================================
    def act_system(self) -> Dict[str, Any]:
        """Live figures for the system-overview act."""
        env = self._require_env()
        with self.lock:
            self._mark("system")
            return {
                "fhir": env.fhir.stats(),
                "tasks": len(env.tasks),
                "roles": [
                    {
                        "role": role.value,
                        "authorized": sorted(f.value for f in ROLE_FIELD_MATRIX[role]),
                    }
                    for role in (Role.REGISTRATION, Role.NURSING,
                                 Role.CLINICAL_SUMMARY, Role.COORDINATOR)
                ],
                "sketch": {
                    "dim": CONFIG.sketch.sketch_dim,
                    "bits": CONFIG.sketch.quant_bits,
                    "bytes": env.encoder.bytes_per_sketch(),
                },
                "settings": dict(self.settings),
            }

    # ==================================================================
    def act_clean(self) -> Dict[str, Any]:
        """Run the task with no contamination at all, in a throwaway sandbox."""
        with self.lock:
            settings = dict(self.settings)
            scratch = AegisEnvironment()
            task = next(t for t in scratch.tasks if t["task_id"] == settings["task_id"])
            trajectory = scratch.run_trajectory(task, depth=settings["depth"])
            chain = _chain_from_trajectory(scratch, trajectory, None, task["patient_id"])
            probe = scratch.run_followup_task(task, depth=settings["depth"])
            self.clean_chain = chain
            self.clean_probe = probe
            self._mark("clean")
            return {"chain": chain, "probe": probe,
                    "patient_display": scratch.fhir.patient_display(task["patient_id"]),
                    "mrn": scratch.fhir.patient_mrn(task["patient_id"])}

    # ==================================================================
    def act_poison(self) -> Dict[str, Any]:
        """Plant the seed, propagate it, mask provenance, and freeze."""
        env = self._require_env()
        with self.lock:
            settings = self.settings
            builder = ScenarioBuilder(env)
            task = next(t for t in env.tasks if t["task_id"] == settings["task_id"])
            incident = builder.build(settings["family"], task, depth=settings["depth"],
                                     n_controls=1)
            mask = ProvenanceMask(env, CONFIG.seed).apply(settings["provenance"])

            self.incident = incident
            self.mask = mask
            self.snapshot = env.snapshot()

            chain = _chain_from_trajectory(env, incident.contaminated,
                                           incident.seed_key, task["patient_id"])
            probe = env.run_followup_task(task, depth=settings["depth"])
            self._mark("poison")

            return {
                "incident_id": incident.incident_id,
                "family": incident.family,
                "family_info": FAMILY_INFO[incident.family],
                "seed_key": incident.seed_key,
                "intended_patient": task["patient_id"],
                "intended_display": env.fhir.patient_display(task["patient_id"]),
                "wrong_patient": incident.wrong_patient,
                "wrong_display": (env.fhir.patient_display(incident.wrong_patient)
                                  if incident.wrong_patient else None),
                "chain": chain,
                "probe": probe,
                "true_contaminated": sorted(incident.true_contaminated),
                "controls": [
                    _chain_from_trajectory(env, c, None, task["patient_id"])
                    for c in incident.controls
                ],
                "provenance": {
                    "condition": mask.condition,
                    "edges_before": mask.edges_before,
                    "edges_removed": mask.edges_removed,
                    "loss_fraction": round(mask.loss_fraction, 3),
                    "description": ProvenanceMask.describe(mask.condition),
                },
            }

    # ==================================================================
    def act_condition(self, condition: str) -> Dict[str, Any]:
        """Run one baseline on the frozen snapshot and score it."""
        env = self._require_env()
        with self.lock:
            if self.incident is None or self.snapshot is None:
                raise ValueError("run the poisoning act first")
            env.restore(self.snapshot)
            outcome = BaselineRunner(env).run(condition, self.incident,
                                              followup_tasks=[self.incident.task])
            metrics = MetricsEvaluator(env).evaluate(
                outcome, self.incident,
                provenance=self.mask.condition if self.mask else "complete",
                edge_loss=self.mask.loss_fraction if self.mask else 0.0,
                followup_tasks=[self.incident.task])
            chain = _chain_from_trajectory(env, self.incident.contaminated,
                                           self.incident.seed_key,
                                           self.incident.task["patient_id"])
            probe = env.run_followup_task(self.incident.task, depth=self.incident.depth)
            controls = [
                _chain_from_trajectory(env, c, None, self.incident.task["patient_id"])
                for c in self.incident.controls
            ]
            self._mark({"B": "deletion", "C": "reset"}.get(condition, "compare"))
            return {
                "condition": condition,
                "name": CONDITION_INFO[condition][0],
                "purpose": CONDITION_INFO[condition][1],
                "chain": chain,
                "controls": controls,
                "probe": probe,
                "metrics": metrics.to_row(),
            }

    # ==================================================================
    def act_care(self, options: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
        """Run the full CARE loop and expose it stage by stage."""
        env = self._require_env()
        with self.lock:
            if self.incident is None or self.snapshot is None:
                raise ValueError("run the poisoning act first")
            env.restore(self.snapshot)
            care_options = CAREOptions(**(options or {}))
            result = RecoveryCoordinator(env).recover(
                self.incident.incident_id, [self.incident.seed_key],
                options=care_options, followup_tasks=[self.incident.task])
            self.care_result = result

            repaired = {r["memory_key"] for r in result.repaired}
            quarantined = {q["memory_key"] for q in result.quarantined}
            outcome = ConditionOutcome(
                condition="I", incident_id=self.incident.incident_id,
                withdrawn={self.incident.seed_key} | repaired | quarantined,
                repaired=repaired,
                touched=repaired | quarantined | {self.incident.seed_key},
                cleared=set(result.cleared), overhead=dict(result.overhead),
                certificate=result.certificate, recovery=result)
            metrics = MetricsEvaluator(env).evaluate(
                outcome, self.incident,
                provenance=self.mask.condition if self.mask else "complete",
                edge_loss=self.mask.loss_fraction if self.mask else 0.0,
                followup_tasks=[self.incident.task])

            chain = _chain_from_trajectory(env, self.incident.contaminated,
                                           self.incident.seed_key,
                                           self.incident.task["patient_id"])
            controls = [
                _chain_from_trajectory(env, c, None, self.incident.task["patient_id"])
                for c in self.incident.controls
            ]
            probe = env.run_followup_task(self.incident.task, depth=self.incident.depth)
            self._mark("care")
            self._mark("verify")

            truth = set(self.incident.true_contaminated)
            return {
                "options": asdict(care_options),
                "stages": {
                    "C": {
                        "letter": "C", "name": "Candidate discovery",
                        "headline": len(result.candidates_considered),
                        "unit": "candidates ranked",
                        "detail": [
                            {**c, "is_true_descendant": c["memory_key"] in truth}
                            for c in result.candidates_considered
                        ],
                        "explain": "Exact lineage first; where edges were masked, the "
                                   "receiver-scoped sketch proposes candidates. This stage "
                                   "only ranks - it never quarantines or repairs.",
                    },
                    "A": {
                        "letter": "A", "name": "Attribution",
                        "headline": f"{len(result.confirmed)} / {len(result.cleared)}",
                        "unit": "confirmed / cleared by replay",
                        "detail": [
                            {
                                "memory_commitment": v.memory_commitment[:16],
                                "runtime": v.runtime,
                                "influence_band": v.influence_band,
                                "influence_score": v.influence_score,
                                "predicate_changed": v.predicate_changed,
                                "disposition": v.disposition,
                            }
                            for v in result.verdicts
                        ],
                        "explain": "Each candidate is replayed without the suspected "
                                   "ancestor inside its owning runtime. Similarity proposes; "
                                   "only a changed predicate confirms.",
                    },
                    "R": {
                        "letter": "R", "name": "Recompilation",
                        "headline": f"{len(result.repaired)} / {len(result.quarantined)}",
                        "unit": "repaired / quarantined",
                        "detail": result.repaired + [
                            {**q, "quarantined": True} for q in result.quarantined],
                        "explain": "Confirmed descendants are rebuilt from trusted FHIR "
                                   "resources. Anything that cannot be rebuilt safely is "
                                   "quarantined for review, never guessed.",
                    },
                    "E": {
                        "letter": "E", "name": "Enforcement",
                        "headline": result.enforcement.get("tombstones", 0),
                        "unit": "signed tombstones",
                        "detail": result.resurrection_probe.get("details", []),
                        "explain": "Withdrawn versions become signed tombstones and arm a "
                                   "firewall over later writes, so the repair cannot be "
                                   "undone by a future session.",
                    },
                },
                "rounds": result.rounds,
                "closure_reached": result.closure_reached,
                "chain": chain,
                "controls": controls,
                "probe": probe,
                "metrics": metrics.to_row(),
                "capsules": [
                    {**{k: v for k, v in asdict(c).items() if k != "sketch"},
                     "sketch_preview": c.sketch[:12],
                     "sketch_dim": len(c.sketch),
                     "size_bytes": c.size_bytes(),
                     "signature": c.signature[:20] + "..."}
                    for c in result.capsules[:3]
                ],
                "resurrection": result.resurrection_probe,
                "overhead": result.overhead,
                "certificate": asdict(result.certificate) if result.certificate else None,
                "certificate_text": result.certificate.to_text() if result.certificate else "",
            }

    # ==================================================================
    def act_compare(self) -> Dict[str, Any]:
        """Every condition on identical frozen state."""
        env = self._require_env()
        with self.lock:
            if self.incident is None or self.snapshot is None:
                raise ValueError("run the poisoning act first")
            evaluator = MetricsEvaluator(env)
            rows = []
            for condition in CONDITION_INFO:
                env.restore(self.snapshot)
                try:
                    outcome = BaselineRunner(env).run(
                        condition, self.incident, followup_tasks=[self.incident.task])
                except Exception as exc:
                    rows.append({"condition": condition, "error": str(exc)})
                    continue
                metrics = evaluator.evaluate(
                    outcome, self.incident,
                    provenance=self.mask.condition if self.mask else "complete",
                    edge_loss=self.mask.loss_fraction if self.mask else 0.0,
                    followup_tasks=[self.incident.task])
                row = metrics.to_row()
                row["name"] = CONDITION_INFO[condition][0]
                row["purpose"] = CONDITION_INFO[condition][1]
                rows.append(row)
            env.restore(self.snapshot)
            self._mark("compare")
            return {
                "results": rows,
                "provenance": self.mask.condition if self.mask else "complete",
                "incident_id": self.incident.incident_id,
            }

    # ==================================================================
    def act_privacy(self) -> Dict[str, Any]:
        """Attack our own recovery interface."""
        env = self._require_env()
        with self.lock:
            if self.incident is None:
                raise ValueError("run the poisoning act first")
            capsules = self.care_result.capsules if self.care_result else []
            auditor = PrivacyAuditor(env)
            audit = auditor.full_audit(self.incident, capsules)
            self._mark("privacy")
            return audit


#: One session per process is the right model for a live demonstration.
session = DemoSession()
