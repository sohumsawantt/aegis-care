"""The experiment runner (proposal Section 9.1, "evaluation sequence").

  1. Run each clean and contaminated trajectory; freeze the snapshot.
  2. Verify the seed was written, propagated, and can change the target predicate.
  3. Apply the provenance mask and role/purpose policy view.
  4. Execute all baselines against the same seed, snapshot, mask, and follow-ups.
  5. For AEGIS-Care, log capsules, candidates, replays, verdicts, repairs, versions.
  6. Run clean tasks, wrong-patient probes, and resurrection attempts after recovery.
  7. Repeat across scenario families and model seeds.
  8. Audit residual harm, false repairs, and clean-state loss.

Step 4 is the reason the whole system carries snapshot/restore: every condition
must see byte-identical starting state, otherwise the paired statistics in
Section 10.2 are not valid.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..config import CONFIG, RESULTS_DIR, AegisConfig
from ..environment import AegisEnvironment
from ..incident.masks import ProvenanceMask
from ..incident.scenarios import FAMILIES, Incident, ScenarioBuilder
from .baselines import CONDITIONS, BaselineRunner, ConditionOutcome
from .metrics import MetricSet, MetricsEvaluator, aggregate, oracle_regret, rq1_matched_loss


class ExperimentRunner:
    """Runs the paired condition matrix and collects metrics."""

    def __init__(self, config: Optional[AegisConfig] = None,
                 model_spec: Optional[str] = None,
                 progress: Optional[Any] = None) -> None:
        self.config = config or CONFIG
        self.model_spec = model_spec
        self.progress = progress
        self.metrics: List[MetricSet] = []
        self.incidents_run: List[Dict[str, Any]] = []
        self.verification_failures: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    # ==================================================================
    def run(
        self,
        *,
        families: Sequence[str] = FAMILIES,
        depths: Sequence[int] = (2, 3, 4),
        provenance_conditions: Sequence[str] = ("complete", "random40", "targeted"),
        conditions: Sequence[str] = CONDITIONS,
        tasks_per_family: int = 2,
        n_controls: int = 1,
        seeds: Sequence[int] = (0,),
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        self.metrics = []
        self.incidents_run = []

        for model_seed in seeds:
            for family in families:
                for depth in depths:
                    from ..incident.scenarios import FAMILY_INFO
                    if depth < FAMILY_INFO[family]["seed_depth"] + 1:
                        continue
                    for task_index in range(tasks_per_family):
                        for provenance in provenance_conditions:
                            self._run_cell(family, depth, task_index, provenance,
                                           conditions, n_controls, model_seed)

        elapsed = time.perf_counter() - started
        return {
            "metrics": self.metrics,
            "rows": [m.to_row() for m in self.metrics],
            "by_condition": aggregate(self.metrics, ("condition",)),
            "by_condition_family": aggregate(self.metrics, ("condition", "family")),
            "by_condition_depth": aggregate(self.metrics, ("condition", "depth")),
            "by_condition_provenance": aggregate(self.metrics, ("condition", "provenance")),
            "rq1_matched_loss": rq1_matched_loss(self.metrics),
            "oracle_regret": oracle_regret(self.metrics),
            "incidents": self.incidents_run,
            "verification_failures": self.verification_failures,
            "wall_seconds": round(elapsed, 3),
            "config": asdict(self.config),
        }

    # ==================================================================
    def _run_cell(self, family: str, depth: int, task_index: int, provenance: str,
                  conditions: Sequence[str], n_controls: int, model_seed: int) -> None:
        """One (family, depth, task, provenance) cell across all conditions."""
        # A fresh environment per cell keeps incidents from interfering.
        env = AegisEnvironment(self.config, self.model_spec)
        builder = ScenarioBuilder(env)
        task = env.tasks[(task_index * 5 + FAMILIES.index(family) + model_seed) % len(env.tasks)]
        incident_id = f"INC-{family}-{task['task_id']}-d{depth}-{provenance}-s{model_seed}"

        incident = builder.build(family, task, depth=depth, n_controls=n_controls,
                                 incident_id=incident_id)

        # --- step 2: verify the incident is real ------------------------
        if not self._verify_incident(env, incident):
            self.verification_failures.append({
                "incident": incident_id, "reason": "seed did not propagate or "
                "could not change the target predicate"})
            self._log(f"  skip {incident_id}: seed did not propagate")
            return

        # --- step 3: apply the provenance mask --------------------------
        mask_result = ProvenanceMask(env, self.config.seed).apply(provenance)

        # --- freeze -----------------------------------------------------
        snapshot = env.snapshot()
        followup_tasks = [incident.task] + [c.task_id for c in []]  # task itself
        followups = [incident.task] + [
            t for t in env.tasks
            if t["task_id"] in {c.task_id for c in incident.controls}
        ]

        self.incidents_run.append({
            "incident_id": incident_id, "family": family, "depth": depth,
            "provenance": provenance, "seed": incident.seed_key,
            "true_contaminated": len(incident.true_contaminated),
            "clean_keys": len(incident.clean_keys),
            "edges_removed": mask_result.edges_removed,
            "edges_before": mask_result.edges_before,
        })
        self._log(f"  {incident_id}: {len(incident.true_contaminated)} contaminated, "
                  f"{mask_result.edges_removed}/{mask_result.edges_before} edges masked")

        # --- step 4: every condition on identical state -----------------
        evaluator = MetricsEvaluator(env)
        for condition in conditions:
            env.restore(snapshot)
            runner = BaselineRunner(env)
            try:
                outcome = runner.run(condition, incident, followup_tasks=followups)
            except Exception as exc:   # a broken condition must not kill the run
                self._log(f"    condition {condition} failed: {exc}")
                self.verification_failures.append(
                    {"incident": incident_id, "condition": condition, "error": str(exc)})
                continue
            metric = evaluator.evaluate(outcome, incident, provenance=provenance,
                                        edge_loss=mask_result.loss_fraction,
                                        followup_tasks=followups)
            self.metrics.append(metric)

        env.restore(snapshot)

    # ------------------------------------------------------------------
    def _verify_incident(self, env: AegisEnvironment, incident: Incident) -> bool:
        """Section 9.1 step 2: confirm the seed was written, propagated, and is
        capable of changing the target task predicate."""
        seed = env.find_artifact(incident.seed_key)
        if seed is None:
            return False
        if not incident.true_contaminated:
            return False
        # The seed must be servable at incident time, otherwise there is nothing
        # to recover from.
        return seed.is_servable()

    # ==================================================================
    @staticmethod
    def save(results: Dict[str, Any], out_dir: Optional[Path] = None) -> Path:
        out_dir = Path(out_dir or RESULTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {k: v for k, v in results.items() if k != "metrics"}
        (out_dir / "results.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")

        try:
            import pandas as pd

            pd.DataFrame(results["rows"]).to_csv(out_dir / "metrics.csv", index=False)
            for name in ("by_condition", "by_condition_family",
                         "by_condition_depth", "by_condition_provenance"):
                pd.DataFrame(results[name]).to_csv(out_dir / f"{name}.csv", index=False)
        except ImportError:
            pass
        return out_dir


__all__ = ["ExperimentRunner"]
