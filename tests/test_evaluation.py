"""Baselines, metrics, statistics, privacy attacks, and the experiment runner."""
from __future__ import annotations

import pytest

from aegis_care.config import CONFIG
from aegis_care.eval.baselines import CONDITION_INFO, BaselineRunner
from aegis_care.eval.metrics import MetricsEvaluator, aggregate, oracle_regret
from aegis_care.eval.privacy import PrivacyAuditor
from aegis_care.eval.runner import ExperimentRunner
from aegis_care.eval.stats import (
    brier_score,
    calibration_report,
    expected_calibration_error,
    frontier,
    mcnemar_exact,
    objective_j,
    paired_bootstrap,
)
from aegis_care.incident.masks import ProvenanceMask


@pytest.fixture
def scored(env, builder):
    """Every condition scored against one targeted-mask incident."""
    incident = builder.build("F1", env.tasks[0], depth=4, n_controls=1)
    ProvenanceMask(env).apply("targeted")
    snapshot = env.snapshot()
    evaluator = MetricsEvaluator(env)
    results = {}
    for condition in CONDITION_INFO:
        env.restore(snapshot)
        outcome = BaselineRunner(env).run(condition, incident,
                                          followup_tasks=[incident.task])
        results[condition] = evaluator.evaluate(outcome, incident,
                                                provenance="targeted",
                                                followup_tasks=[incident.task])
    env.restore(snapshot)
    return incident, results


class TestBaselines:
    def test_all_nine_conditions_run(self, scored):
        _, results = scored
        assert set(results) == set(CONDITION_INFO)

    def test_no_recovery_leaves_residual_harm(self, scored):
        _, r = scored
        assert r["A"].rwh > 0
        assert r["A"].descendant_recall == 0.0

    def test_seed_deletion_does_not_repair_descendants(self, scored):
        """The proposal's motivating claim, as an assertion."""
        _, r = scored
        assert r["B"].descendant_recall == 0.0
        assert r["B"].rwh > 0

    def test_full_reset_destroys_clean_state(self, scored):
        _, r = scored
        assert r["C"].descendant_recall == 1.0
        assert r["C"].bsr == 0.0

    def test_explicit_lineage_fails_under_targeted_masking(self, scored):
        """RQ1/RQ2: this is the gap latent discovery exists to close."""
        _, r = scored
        assert r["D"].descendant_recall < 1.0
        assert r["E"].descendant_recall < 1.0

    def test_sketch_only_loses_precision(self, scored):
        _, r = scored
        assert r["F"].descendant_recall == 1.0
        assert r["F"].descendant_precision < 1.0

    def test_raw_oracle_leaks(self, scored):
        _, r = scored
        assert r["G"].uer == 1.0

    def test_full_care_dominates_non_oracle_baselines(self, scored):
        """The predeclared go criterion, checked directly."""
        _, r = scored
        care = r["I"]
        assert care.rwh <= r["D"].rwh
        assert care.rwh <= r["E"].rwh
        assert care.bsr > r["C"].bsr, "must retain more state than full reset"
        assert care.descendant_precision > r["F"].descendant_precision
        assert care.uer < r["G"].uer, "must leak less than the raw-content oracle"

    def test_full_care_matches_the_private_oracle_here(self, scored):
        _, r = scored
        assert r["I"].descendant_recall >= r["H"].descendant_recall
        assert r["I"].bsr >= r["H"].bsr

    def test_only_care_blocks_resurrection(self, scored):
        _, r = scored
        assert r["I"].drr == 0.0
        assert r["A"].drr > 0.0


class TestMetrics:
    def test_metric_row_is_flat(self, scored):
        _, r = scored
        row = r["I"].to_row()
        assert isinstance(row["rwh"], float)
        assert all(not isinstance(v, (dict, list)) for v in row.values())

    def test_aggregate_macro_averages(self, scored):
        _, r = scored
        rows = aggregate(list(r.values()), ("condition",))
        assert len(rows) == len(CONDITION_INFO)
        assert all("descendant_recall" in row for row in rows)

    def test_oracle_regret_is_computed(self, scored):
        _, r = scored
        regret = oracle_regret(list(r.values()))
        assert "I" in regret
        assert regret["C"] > regret["I"], "reset should regret more than CARE"

    def test_objective_j_prefers_care(self, scored):
        _, r = scored
        j_care = objective_j(r["I"], CONFIG.objective)
        for other in ("A", "B", "C", "F", "G"):
            assert j_care <= objective_j(r[other], CONFIG.objective), (
                f"objective J did not prefer CARE over condition {other}")


class TestStatistics:
    def test_paired_bootstrap(self, scored):
        _, r = scored
        metrics = list(r.values())
        ci = paired_bootstrap(metrics, "bsr", "I", "C", n_resamples=500)
        assert ci is not None
        assert ci.mean_difference > 0

    def test_mcnemar(self, scored):
        _, r = scored
        res = mcnemar_exact(list(r.values()), "I", "A")
        assert res is not None
        assert res.n_pairs >= 1

    def test_brier_and_ece(self):
        probs = [0.9, 0.8, 0.2, 0.1]
        outcomes = [True, True, False, False]
        assert brier_score(probs, outcomes) < 0.05
        assert expected_calibration_error(probs, outcomes) < 0.2
        report = calibration_report(probs, outcomes)
        assert report["n"] == 4

    def test_frontier_marks_pareto_points(self, scored):
        _, r = scored
        points = frontier(list(r.values()))
        assert any(p["pareto"] for p in points)
        care = next(p for p in points if p["condition"] == "I")
        assert care["pareto"], "full CARE should sit on the frontier"


class TestPrivacyAttacks:
    def test_no_raw_content_exported(self, env, recovered):
        incident, _ = recovered
        audit = PrivacyAuditor(env).released_field_audit(incident.incident_id)
        assert audit["raw_content_exported"] is False

    def test_attribute_inference_is_near_chance(self, env, recovered):
        result = PrivacyAuditor(env).attribute_inference(attribute="gender")
        assert result.advantage < 0.25, (
            "the sketch leaks protected attributes far above chance")

    def test_scoping_prevents_linkability(self, env, recovered):
        """The scoping ablation from Section 9.2, as an assertion."""
        result = PrivacyAuditor(env).linkability(n_patients=20)
        assert result.advantage < 0.2, "scoped sketches were linkable across recipients"
        assert result.detail["unscoped_ablation_accuracy"] > result.accuracy, (
            "removing scoping should measurably increase linkability")

    def test_membership_leak_is_measured_not_hidden(self, env, recovered):
        """We do not assert the leak is zero - only that it is quantified."""
        incident, care = recovered
        result = PrivacyAuditor(env).membership_inference(incident, care.capsules)
        assert result.n > 0
        assert -1.0 <= result.advantage <= 1.0


class TestExperimentRunner:
    def test_small_matrix_runs_end_to_end(self):
        runner = ExperimentRunner()
        results = runner.run(families=("F1",), depths=(4,),
                             provenance_conditions=("complete", "targeted"),
                             tasks_per_family=1)
        assert results["rows"]
        assert results["by_condition"]
        assert not results["verification_failures"]

    def test_conditions_are_paired_on_identical_state(self):
        """Every condition must be scored on the same incidents."""
        runner = ExperimentRunner()
        results = runner.run(families=("F1",), depths=(4,),
                             provenance_conditions=("targeted",), tasks_per_family=1)
        by_condition = {}
        for m in results["metrics"]:
            by_condition.setdefault(m.condition, set()).add(m.incident_id)
        incident_sets = list(by_condition.values())
        assert all(s == incident_sets[0] for s in incident_sets)

    def test_results_are_reproducible(self):
        a = ExperimentRunner().run(families=("F1",), depths=(4,),
                                   provenance_conditions=("targeted",), tasks_per_family=1)
        b = ExperimentRunner().run(families=("F1",), depths=(4,),
                                   provenance_conditions=("targeted",), tasks_per_family=1)
        key = lambda res: [(r["condition"], r["descendant_recall"], r["bsr"])
                           for r in res["by_condition"]]
        assert key(a) == key(b), "the experiment is not deterministic"

    def test_save_writes_artifacts(self, tmp_path):
        results = ExperimentRunner().run(families=("F1",), depths=(4,),
                                         provenance_conditions=("complete",),
                                         tasks_per_family=1)
        out = ExperimentRunner.save(results, tmp_path)
        assert (out / "results.json").exists()
        assert (out / "metrics.csv").exists()


class TestReport:
    def test_report_and_figures_render(self, tmp_path):
        from aegis_care.eval.report import build_report, make_figures

        results = ExperimentRunner().run(families=("F1",), depths=(4,),
                                         provenance_conditions=("complete", "targeted"),
                                         tasks_per_family=1)
        report = build_report(results, out_dir=tmp_path)
        assert "AEGIS-Care experimental report" in report
        assert "RQ1" in report
        assert (tmp_path / "report.md").exists()

        figures = make_figures(results, tmp_path)
        assert figures
        assert all(f.exists() and f.stat().st_size > 0 for f in figures)
