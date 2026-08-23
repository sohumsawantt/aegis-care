"""Result tables and figures.

Produces the artifacts the capstone submission needs: frozen result tables,
paired confidence intervals, the safety-utility-privacy frontier, ablation
tables, and the figures referenced by Section 10.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..config import RESULTS_DIR
from .baselines import CONDITION_INFO
from .metrics import MetricSet, aggregate, oracle_regret
from .stats import frontier, mcnemar_exact, paired_bootstrap

# Headline comparisons from Section 10.1.
PRIMARY_COMPARISONS = [
    ("I", "D", "value of missing-edge recovery plus recompilation"),
    ("I", "E", "value of latent candidate discovery under incomplete provenance"),
    ("I", "F", "necessity of counterfactual confirmation to protect clean state"),
    ("I", "C", "utility retained relative to the safest simple fallback"),
    ("I", "G", "recovery loss and privacy gain vs centralized raw-content access"),
    ("I", "H", "oracle regret and irreducible cost of missing provenance"),
    ("I", "B", "value of descendant repair over seed deletion"),
    ("I", "A", "total effect of recovery"),
]


def markdown_table(rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "_no data_\n"
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col, "")
            cells.append(f"{value:.4f}" if isinstance(value, float) else str(value))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body]) + "\n"


def build_report(results: Dict[str, Any], *, privacy: Optional[Dict[str, Any]] = None,
                 out_dir: Optional[Path] = None) -> str:
    """Render the full markdown report."""
    metrics: List[MetricSet] = results.get("metrics", [])
    lines: List[str] = []

    lines.append("# AEGIS-Care experimental report\n")
    lines.append("_Privacy-bounded memory recompilation for recovering poisoned "
                 "clinical AI agents._\n")
    lines.append(f"Wall time: {results.get('wall_seconds', 0)}s · "
                 f"{len(results.get('incidents', []))} incidents · "
                 f"{len(metrics)} condition runs\n")

    # --- conditions legend --------------------------------------------
    lines.append("\n## Recovery conditions\n")
    lines.append(markdown_table(
        [{"id": k, "condition": v[0], "purpose": v[1]} for k, v in CONDITION_INFO.items()],
        ["id", "condition", "purpose"]))

    # --- headline table -------------------------------------------------
    lines.append("\n## Primary results by condition\n")
    lines.append("RWH = residual wrong-patient/unauthorized harm (lower better); "
                 "BSR = benign-state retention (higher better); "
                 "RTS = repaired task success; UER = unauthorized exposure; "
                 "DRR = deletion resurrection rate.\n")
    lines.append(markdown_table(results.get("by_condition", []), [
        "condition", "n", "rwh", "descendant_recall", "descendant_precision",
        "bsr", "rts", "false_repair_rate", "uer", "drr"]))

    # --- provenance sensitivity (RQ1) ------------------------------------
    lines.append("\n## RQ1 - sensitivity to provenance loss\n")
    lines.append("_Hypothesis: targeted loss of cross-role and semantic-derivation "
                 "edges harms provenance-only recovery more than random edge loss._\n")
    lines.append(markdown_table(results.get("by_condition_provenance", []), [
        "condition", "provenance", "n", "descendant_recall", "descendant_precision",
        "bsr", "rwh"]))

    lines.append("\n### RQ1 at matched edge loss\n")
    lines.append("_Comparing the `targeted` and `random*` labels directly is unfair: they "
                 "remove different numbers of edges. This groups runs by the realised loss "
                 "fraction and compares within each bucket. A positive "
                 "`targeted_worse_by` supports the hypothesis._\n")
    lines.append(markdown_table(results.get("rq1_matched_loss", []), [
        "condition", "loss_bucket", "mean_loss_targeted", "mean_loss_random",
        "recall_targeted", "recall_random", "targeted_worse_by",
        "n_targeted", "n_random"]))

    # --- family / depth --------------------------------------------------
    lines.append("\n## Macro averages by scenario family\n")
    lines.append(markdown_table(results.get("by_condition_family", []), [
        "condition", "family", "n", "descendant_recall", "descendant_precision",
        "bsr", "rwh", "uer"]))

    lines.append("\n## Macro averages by propagation depth\n")
    lines.append(markdown_table(results.get("by_condition_depth", []), [
        "condition", "depth", "n", "descendant_recall", "bsr", "rwh"]))

    # --- paired statistics ------------------------------------------------
    lines.append("\n## Paired comparisons (Section 10.1)\n")
    comparison_rows = []
    for a, b, rationale in PRIMARY_COMPARISONS:
        for metric_name in ("rwh", "descendant_recall", "bsr", "uer"):
            ci = paired_bootstrap(metrics, metric_name, a, b)
            if ci is None:
                continue
            comparison_rows.append({
                "comparison": f"{a} vs {b}", "metric": metric_name,
                "difference": ci.mean_difference, "ci_low": ci.ci_low,
                "ci_high": ci.ci_high, "n": ci.n_pairs,
                "significant": "yes" if ci.significant else "no",
                "rationale": rationale if metric_name == "rwh" else "",
            })
    lines.append(markdown_table(comparison_rows, [
        "comparison", "metric", "difference", "ci_low", "ci_high", "n",
        "significant", "rationale"]))

    lines.append("\n### McNemar exact tests on paired binary outcomes\n")
    mcnemar_rows = []
    for a, b, _ in PRIMARY_COMPARISONS:
        res = mcnemar_exact(metrics, a, b, predicate="no_residual_harm")
        if res is None:
            continue
        mcnemar_rows.append({
            "comparison": f"{a} vs {b}", "b (A better)": res.b, "c (B better)": res.c,
            "p_value": round(res.p_value, 5), "n": res.n_pairs,
        })
    lines.append(markdown_table(mcnemar_rows,
                                ["comparison", "b (A better)", "c (B better)", "p_value", "n"]))

    # --- frontier ----------------------------------------------------------
    lines.append("\n## Safety-utility-privacy frontier (Section 10.2)\n")
    points = frontier(metrics)
    lines.append(markdown_table(points, [
        "condition", "safety", "utility", "privacy", "recall", "precision",
        "pareto", "n"]))

    # --- oracle regret ------------------------------------------------------
    lines.append("\n## Oracle regret vs condition H\n")
    regret = results.get("oracle_regret", {})
    lines.append(markdown_table(
        [{"condition": k, "regret": v} for k, v in sorted(regret.items())],
        ["condition", "regret"]))

    # --- privacy ------------------------------------------------------------
    if privacy:
        lines.append("\n## Empirical leakage (Section 7.2)\n")
        lines.append("_The proposal makes no claim that sketches are private by "
                     "construction; these are measured attacks._\n")
        attack_rows = []
        for key in ("attribute_gender", "attribute_restricted", "membership", "linkability"):
            item = privacy.get(key)
            if not item:
                continue
            attack_rows.append({
                "attack": item["name"], "n": item["n"], "accuracy": item["accuracy"],
                "baseline": item["baseline"], "advantage": item["advantage"],
            })
        lines.append(markdown_table(attack_rows,
                                     ["attack", "n", "accuracy", "baseline", "advantage"]))
        released = privacy.get("released_fields", {})
        lines.append(f"\nRaw content exported through the recovery interface: "
                     f"**{released.get('raw_content_exported', 'unknown')}**. "
                     f"Fields released: `{', '.join(released.get('fields_released', []))}`.\n")
        ablation = (privacy.get("linkability") or {}).get(
            "detail", {}).get("unscoped_ablation_accuracy")
        if ablation is not None:
            lines.append(f"\nRemoving purpose/recipient scoping raises cross-recipient "
                         f"linkage accuracy to **{ablation}**, which is what the scoping "
                         f"ablation in Section 9.2 is meant to expose.\n")

    # --- verification failures ----------------------------------------------
    failures = results.get("verification_failures", [])
    lines.append("\n## Verification failures and negative results\n")
    if failures:
        lines.append(markdown_table(
            [{"incident": f.get("incident", ""), "condition": f.get("condition", "-"),
              "reason": f.get("reason", f.get("error", ""))} for f in failures],
            ["incident", "condition", "reason"]))
    else:
        lines.append("_No incident failed pre-recovery verification._\n")

    report = "\n".join(lines)
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(report, encoding="utf-8")
    return report


# ======================================================================
def make_figures(results: Dict[str, Any], out_dir: Optional[Path] = None) -> List[Path]:
    """Render the figures. Returns the paths written."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    out = Path(out_dir or RESULTS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    metrics: List[MetricSet] = results.get("metrics", [])
    by_condition = results.get("by_condition", [])
    if not by_condition:
        return []

    order = [row["condition"] for row in by_condition]

    # --- Figure 1: safety vs utility ---------------------------------
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    for row in by_condition:
        ax.scatter(row["bsr"], row["rwh"], s=160,
                   edgecolor="black", linewidth=0.8, zorder=3)
        ax.annotate(row["condition"], (row["bsr"], row["rwh"]),
                    textcoords="offset points", xytext=(8, 6), fontsize=11,
                    fontweight="bold")
    ax.set_xlabel("Benign-state retention (BSR) - higher is better")
    ax.set_ylabel("Residual wrong-patient / unauthorized harm (RWH) - lower is better")
    ax.set_title("Safety-utility frontier by recovery condition")
    ax.grid(alpha=0.3, linestyle=":")
    ax.set_xlim(-0.05, 1.1)
    fig.tight_layout()
    path = out / "fig1_safety_utility.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(path)

    # --- Figure 2: recall / precision / BSR bars ---------------------
    fig, ax = plt.subplots(figsize=(10, 5.5))
    import numpy as np
    x = np.arange(len(order))
    width = 0.26
    ax.bar(x - width, [r["descendant_recall"] for r in by_condition], width,
           label="descendant recall", edgecolor="black", linewidth=0.5)
    ax.bar(x, [r["descendant_precision"] for r in by_condition], width,
           label="descendant precision", edgecolor="black", linewidth=0.5)
    ax.bar(x + width, [r["bsr"] for r in by_condition], width,
           label="benign-state retention", edgecolor="black", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(order)
    ax.set_ylim(0, 1.15)
    ax.set_xlabel("Recovery condition")
    ax.set_title("Discovery quality and clean-state retention")
    ax.legend(loc="upper left", ncols=3, fontsize=9)
    ax.grid(axis="y", alpha=0.3, linestyle=":")
    fig.tight_layout()
    path = out / "fig2_recall_precision_bsr.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(path)

    # --- Figure 3: provenance sensitivity ----------------------------
    prov_rows = results.get("by_condition_provenance", [])
    if prov_rows:
        conditions = sorted({r["condition"] for r in prov_rows})
        provenances = sorted({r["provenance"] for r in prov_rows})
        fig, ax = plt.subplots(figsize=(10, 5.5))
        x = np.arange(len(conditions))
        width = 0.8 / max(1, len(provenances))
        for i, prov in enumerate(provenances):
            values = [
                next((r["descendant_recall"] for r in prov_rows
                      if r["condition"] == c and r["provenance"] == prov), 0.0)
                for c in conditions
            ]
            ax.bar(x + i * width - 0.4 + width / 2, values, width, label=prov,
                   edgecolor="black", linewidth=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(conditions)
        ax.set_ylabel("Descendant recall")
        ax.set_xlabel("Recovery condition")
        ax.set_title("RQ1: recovery under provenance loss")
        ax.legend(title="provenance", fontsize=9)
        ax.grid(axis="y", alpha=0.3, linestyle=":")
        ax.set_ylim(0, 1.15)
        fig.tight_layout()
        path = out / "fig3_provenance_sensitivity.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        written.append(path)

    # --- Figure 4: overhead ------------------------------------------
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(order, [r.get("oh_replays", 0) for r in by_condition],
           edgecolor="black", linewidth=0.5, color="#8b6bb1")
    ax.set_ylabel("Local counterfactual replays")
    ax.set_xlabel("Recovery condition")
    ax.set_title("Recovery overhead: local replay cost")
    ax.grid(axis="y", alpha=0.3, linestyle=":")
    fig.tight_layout()
    path = out / "fig4_overhead.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    written.append(path)

    return written


__all__ = ["build_report", "make_figures", "markdown_table", "PRIMARY_COMPARISONS"]
