"""AEGIS-Care command line interface.

    python -m aegis_care.cli demo            one incident, full CARE, printed certificate
    python -m aegis_care.cli baselines       all nine conditions on one incident
    python -m aegis_care.cli experiment      the full paired matrix + report + figures
    python -m aegis_care.cli privacy         empirical leakage attacks
    python -m aegis_care.cli serve           the dashboard and API
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

from .care.coordinator import CAREOptions, RecoveryCoordinator
from .config import CONFIG, RESULTS_DIR
from .environment import AegisEnvironment
from .eval.baselines import CONDITION_INFO, BaselineRunner
from .eval.metrics import MetricsEvaluator
from .eval.privacy import PrivacyAuditor
from .eval.report import build_report, make_figures
from .eval.runner import ExperimentRunner
from .incident.masks import ProvenanceMask
from .incident.scenarios import FAMILIES, FAMILY_INFO, ScenarioBuilder

warnings.filterwarnings("ignore")


def _setup_console() -> bool:
    """Make output safe on a Windows cp1252 console.

    Returns True if the stream can carry box-drawing characters.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    if sys.platform == "win32":
        # Enable ANSI escape processing on Windows 10+ consoles.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


UNICODE_OK = _setup_console()
USE_COLOR = sys.stdout.isatty()

BOLD = "\033[1m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
BLUE = "\033[36m" if USE_COLOR else ""

HBAR = "─" if UNICODE_OK else "-"
ARROW = "→" if UNICODE_OK else "->"


def rule(title: str = "", width: int = 78) -> None:
    if title:
        pad = max(0, width - len(title) - 3)
        print(f"{BOLD}{BLUE}{HBAR * 2} {title} {HBAR * pad}{RESET}")
    else:
        print(f"{DIM}{HBAR * width}{RESET}")


def table(rows: List[Dict[str, Any]], columns: List[str], highlight: str = "") -> None:
    if not rows:
        print(f"{DIM}(no rows){RESET}")
        return
    widths = {c: max(len(c), *(len(_cell(r.get(c))) for r in rows)) for c in columns}
    print("  " + "  ".join(f"{BOLD}{c:<{widths[c]}}{RESET}" for c in columns))
    print("  " + "  ".join(HBAR * widths[c] for c in columns))
    for r in rows:
        line = "  ".join(f"{_cell(r.get(c)):<{widths[c]}}" for c in columns)
        mark = highlight and str(r.get(columns[0])) == highlight
        print(("  " + f"{GREEN}{line}{RESET}") if mark else ("  " + line))


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value if value is not None else "")


# ======================================================================
def cmd_demo(args) -> int:
    env = AegisEnvironment(model_spec=args.model)
    builder = ScenarioBuilder(env)
    task = next((t for t in env.tasks if t["task_id"] == args.task), env.tasks[0])

    rule("INCIDENT CONSTRUCTION")
    info = FAMILY_INFO[args.family]
    print(f"  Family      : {args.family} — {info['name']}")
    print(f"  Seed        : {info['seed']}")
    print(f"  Propagation : {info['propagation']}")
    print(f"  Failure     : {info['failure']}")
    print(f"  Task        : {task['task_id']} ({task['label']}), patient {task['patient_id']}")

    incident = builder.build(args.family, task, depth=args.depth, n_controls=1)
    mask = ProvenanceMask(env, CONFIG.seed).apply(args.provenance)

    print()
    print(f"  Seed artifact         : {incident.seed_key}")
    if incident.wrong_patient:
        print(f"  Seed points at        : {RED}{incident.wrong_patient}{RESET} "
              f"(intended {GREEN}{task['patient_id']}{RESET})")
    print(f"  Contaminated (truth)  : {len(incident.true_contaminated)} descendant(s)")
    print(f"  Provenance condition  : {mask.condition} — "
          f"{mask.edges_removed}/{mask.edges_before} edges removed "
          f"({mask.loss_fraction:.0%} loss)")

    print()
    rule("CONTAMINATED TRAJECTORY")
    for node in incident.contaminated.nodes:
        artifact = env.find_artifact(node.key)
        flag = f"{RED}CONTAMINATED{RESET}" if node.contaminated else f"{GREEN}clean{RESET}"
        seed = f" {YELLOW}<- SEED{RESET}" if node.key == incident.seed_key else ""
        print(f"  depth {node.depth}  {node.role.value:<17s} {node.artifact_type.value:<19s}"
              f" patient={artifact.structured_facts.get('patient_id')}  {flag}{seed}")

    print()
    rule("CARE RECOVERY")
    coordinator = RecoveryCoordinator(env)
    result = coordinator.recover(incident.incident_id, [incident.seed_key],
                                 options=CAREOptions(), followup_tasks=[incident.task])

    print(f"  C  candidates ranked   : {len(result.candidates_considered)}")
    print(f"  A  confirmed / cleared : {len(result.confirmed)} / {len(result.cleared)}")
    print(f"  R  repaired / quarant. : {len(result.repaired)} / {len(result.quarantined)}")
    print(f"  E  tombstones          : {result.enforcement['tombstones']}")
    print(f"     closure reached     : {result.closure_reached} in {result.rounds} round(s)")

    if result.repaired:
        print()
        print(f"  {BOLD}Clean-room repairs{RESET}")
        for r in result.repaired:
            repaired = env.find_artifact(r["new_key"])
            pid = repaired.structured_facts.get("patient_id") if repaired else "?"
            ok = GREEN if pid == task["patient_id"] else RED
            print(f"    {r['memory_key']}  {ARROW}  {r['new_key']}   patient={ok}{pid}{RESET}")

    print()
    rule("POST-RECOVERY VERIFICATION")
    followup = env.run_followup_task(incident.task, depth=incident.depth)
    colour = GREEN if followup["correct"] else RED
    print(f"  Follow-up task selects : {colour}{followup['selected_patient']}{RESET} "
          f"(intended {task['patient_id']}) — "
          f"{'CORRECT' if followup['correct'] else 'WRONG PATIENT'}")

    evaluator = MetricsEvaluator(env)
    from .eval.baselines import ConditionOutcome
    repaired_keys = {r["memory_key"] for r in result.repaired}
    quarantined_keys = {q["memory_key"] for q in result.quarantined}
    outcome = ConditionOutcome(
        condition="I", incident_id=incident.incident_id,
        withdrawn={incident.seed_key} | repaired_keys | quarantined_keys,
        repaired=repaired_keys,
        touched=repaired_keys | quarantined_keys | {incident.seed_key},
        cleared=set(result.cleared), overhead=dict(result.overhead),
        certificate=result.certificate, recovery=result)
    metrics = evaluator.evaluate(outcome, incident, provenance=mask.condition,
                                 followup_tasks=[incident.task])
    print(f"  Descendant recall      : {metrics.descendant_recall:.3f}")
    print(f"  Descendant precision   : {metrics.descendant_precision:.3f}")
    print(f"  Benign-state retention : {metrics.bsr:.3f}")
    print(f"  Residual harm (RWH)    : {metrics.rwh:.3f}")
    print(f"  Unauthorized exposure  : {metrics.uer:.3f}")
    print(f"  Resurrection rate      : {metrics.drr:.3f}")

    print()
    print(result.certificate.to_text())
    return 0


# ======================================================================
def cmd_baselines(args) -> int:
    env = AegisEnvironment(model_spec=args.model)
    builder = ScenarioBuilder(env)
    task = next((t for t in env.tasks if t["task_id"] == args.task), env.tasks[0])
    incident = builder.build(args.family, task, depth=args.depth, n_controls=1)
    mask = ProvenanceMask(env, CONFIG.seed).apply(args.provenance)
    snapshot = env.snapshot()

    rule(f"BASELINE MATRIX — {incident.incident_id}")
    print(f"  provenance: {mask.condition} "
          f"({mask.edges_removed}/{mask.edges_before} edges removed)")
    print(f"  ground truth: {len(incident.true_contaminated)} contaminated descendant(s), "
          f"{len(incident.clean_keys)} clean control artifact(s)")
    print()

    evaluator = MetricsEvaluator(env)
    rows = []
    for condition in CONDITION_INFO:
        env.restore(snapshot)
        try:
            outcome = BaselineRunner(env).run(condition, incident,
                                              followup_tasks=[incident.task])
        except Exception as exc:
            print(f"  {condition}: failed — {exc}")
            continue
        m = evaluator.evaluate(outcome, incident, provenance=mask.condition,
                               followup_tasks=[incident.task])
        rows.append({
            "id": condition, "condition": CONDITION_INFO[condition][0],
            "RWH": m.rwh, "recall": m.descendant_recall,
            "precision": m.descendant_precision, "BSR": m.bsr, "RTS": m.rts,
            "UER": m.uer, "DRR": m.drr,
        })
    env.restore(snapshot)

    table(rows, ["id", "condition", "RWH", "recall", "precision", "BSR", "RTS", "UER", "DRR"],
          highlight="I")
    print()
    print(f"  {DIM}RWH/UER/DRR lower is better; recall/precision/BSR/RTS higher is better.{RESET}")
    print(f"  {DIM}I is AEGIS-Care. Compare D/E (lineage only), F (similarity as cause),{RESET}")
    print(f"  {DIM}C (reset), G (raw oracle — note UER), H (private oracle graph).{RESET}")
    return 0


# ======================================================================
def cmd_experiment(args) -> int:
    rule("FULL EXPERIMENT MATRIX")
    runner = ExperimentRunner(model_spec=args.model, progress=lambda m: print(f"{DIM}{m}{RESET}"))
    results = runner.run(
        families=tuple(args.families),
        depths=tuple(args.depths),
        provenance_conditions=tuple(args.provenance),
        tasks_per_family=args.tasks_per_family,
    )
    out_dir = Path(args.out or RESULTS_DIR)
    ExperimentRunner.save(results, out_dir)

    print()
    rule("AGGREGATE BY CONDITION")
    table([{
        "id": r["condition"], "n": r["n"], "RWH": r["rwh"],
        "recall": r["descendant_recall"], "precision": r["descendant_precision"],
        "BSR": r["bsr"], "RTS": r["rts"], "UER": r["uer"], "DRR": r["drr"],
    } for r in results["by_condition"]],
        ["id", "n", "RWH", "recall", "precision", "BSR", "RTS", "UER", "DRR"],
        highlight="I")

    print()
    rule("RQ1 — RECOVERY UNDER PROVENANCE LOSS")
    table([{
        "id": r["condition"], "provenance": r["provenance"],
        "recall": r["descendant_recall"], "precision": r["descendant_precision"],
        "BSR": r["bsr"], "RWH": r["rwh"],
    } for r in results["by_condition_provenance"]
        if r["condition"] in ("D", "E", "F", "I", "H")],
        ["id", "provenance", "recall", "precision", "BSR", "RWH"], highlight="I")

    print()
    rule("ORACLE REGRET (vs condition H)")
    table([{"condition": k, "regret": v} for k, v in sorted(results["oracle_regret"].items())],
          ["condition", "regret"], highlight="I")

    # Privacy audit on a representative incident.
    privacy = None
    try:
        env = AegisEnvironment(model_spec=args.model)
        builder = ScenarioBuilder(env)
        incident = builder.build("F1", env.tasks[0], depth=4, n_controls=1)
        rec = RecoveryCoordinator(env).recover(incident.incident_id, [incident.seed_key])
        privacy = PrivacyAuditor(env).full_audit(incident, rec.capsules)
        print()
        rule("EMPIRICAL LEAKAGE")
        table([{
            "attack": privacy[k]["name"], "acc": privacy[k]["accuracy"],
            "baseline": privacy[k]["baseline"], "advantage": privacy[k]["advantage"],
        } for k in ("attribute_gender", "attribute_restricted", "membership", "linkability")],
            ["attack", "acc", "baseline", "advantage"])
        rf = privacy["released_fields"]
        colour = RED if rf["raw_content_exported"] else GREEN
        print(f"\n  Raw content exported: {colour}"
              f"{'YES' if rf['raw_content_exported'] else 'NONE'}{RESET}"
              f"   ({rf['capsules']} capsules, {rf['total_bytes']} bytes)")
    except Exception as exc:
        print(f"{YELLOW}  privacy audit skipped: {exc}{RESET}")

    build_report(results, privacy=privacy, out_dir=out_dir)
    figures = make_figures(results, out_dir)

    print()
    rule("ARTIFACTS WRITTEN")
    print(f"  {out_dir / 'report.md'}")
    print(f"  {out_dir / 'results.json'}")
    print(f"  {out_dir / 'metrics.csv'}")
    for f in figures:
        print(f"  {f}")
    print(f"\n  wall time: {results['wall_seconds']}s, "
          f"{len(results['rows'])} condition runs over "
          f"{len(results['incidents'])} incidents")
    return 0


# ======================================================================
def cmd_privacy(args) -> int:
    env = AegisEnvironment(model_spec=args.model)
    builder = ScenarioBuilder(env)
    incident = builder.build(args.family, env.tasks[0], depth=args.depth, n_controls=1)
    result = RecoveryCoordinator(env).recover(incident.incident_id, [incident.seed_key])
    audit = PrivacyAuditor(env).full_audit(incident, result.capsules)

    rule("EMPIRICAL LEAKAGE MEASUREMENT")
    print(f"  {DIM}The system claims only that raw content is not exported through the{RESET}")
    print(f"  {DIM}defined recovery interface. Sketch confidentiality is NOT claimed.{RESET}")
    print()
    table([{
        "attack": audit[k]["name"], "n": audit[k]["n"], "accuracy": audit[k]["accuracy"],
        "baseline": audit[k]["baseline"], "advantage": audit[k]["advantage"],
    } for k in ("attribute_gender", "attribute_restricted", "membership", "linkability")],
        ["attack", "n", "accuracy", "baseline", "advantage"])

    link = audit["linkability"]
    print()
    print(f"  {BOLD}Scoping ablation{RESET}: with receiver scoping, cross-recipient linkage runs "
          f"at {link['accuracy']:.3f} (chance = {link['baseline']:.3f}).")
    print(f"  Without scoping it reaches "
          f"{RED}{link['detail']['unscoped_ablation_accuracy']:.3f}{RESET} — "
          f"every recovery event joinable to one patient.")

    rf = audit["released_fields"]
    print()
    rule("RELEASED-FIELD AUDIT")
    colour = RED if rf["raw_content_exported"] else GREEN
    print(f"  raw content exported : {colour}"
          f"{'YES' if rf['raw_content_exported'] else 'NONE'}{RESET}")
    print(f"  capsules / bytes     : {rf['capsules']} / {rf['total_bytes']}")
    print(f"  fields released      : {', '.join(rf['fields_released'])}")
    print(f"  undeclared fields    : {rf['undeclared_fields'] or 'none'}")
    return 0


# ======================================================================
def cmd_serve(args) -> int:
    import uvicorn
    host = args.host
    shown = "127.0.0.1" if host == "0.0.0.0" else host
    print()
    rule("AEGIS-CARE")
    print(f"  Presentation   {ARROW} {BLUE}http://{shown}:{args.port}/{RESET}")
    print(f"  Analyst console{ARROW} {BLUE}http://{shown}:{args.port}/console{RESET}")
    print(f"  API docs       {ARROW} {BLUE}http://{shown}:{args.port}/docs{RESET}")
    print()
    print(f"  {DIM}Press Ctrl+C to stop.{RESET}")
    print()
    if getattr(args, "open_browser", False):
        import threading
        import webbrowser

        url = f"http://{shown}:{args.port}/"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("aegis_care.api.app:app", host=host, port=args.port,
                reload=args.reload, log_level="warning")
    return 0


def cmd_console(args) -> int:
    """Launch the operations console for a clinical safety team."""
    import os

    if args.fhir_url:
        os.environ["AEGIS_FHIR_URL"] = args.fhir_url
    if args.fhir_token:
        os.environ["AEGIS_FHIR_TOKEN"] = args.fhir_token
    if args.db:
        os.environ["AEGIS_OPS_DB"] = args.db

    live = bool(os.environ.get("AEGIS_FHIR_URL"))
    print()
    rule("AEGIS-CARE OPERATIONS CONSOLE")
    print(f"  Record source : {GREEN if live else YELLOW}"
          f"{os.environ.get('AEGIS_FHIR_URL') if live else 'synthetic sandbox'}{RESET}")
    print(f"  Incident store: {os.environ.get('AEGIS_OPS_DB', 'in-memory (not persisted)')}")
    if not live:
        print(f"  {DIM}Sign in with a sandbox account shown on the login screen.{RESET}")
        print(f"  {DIM}Safety officer: a.khan / safety123{RESET}")
    args.open_browser = True
    args.reload = False
    return cmd_serve(args)


def cmd_present(args) -> int:
    """Launch straight into the guided presentation with the browser open."""
    print()
    rule("PRESENTATION MODE")
    print(f"  {BOLD}Keyboard{RESET}: {ARROW} / {ARROW} navigate, Space runs the act, N toggles speaker notes.")
    print(f"  {DIM}Every act calls the live system. Nothing is pre-recorded.{RESET}")
    print(f"  {DIM}Use the Scenario button to switch contamination family or provenance.{RESET}")
    args.open_browser = True
    args.reload = False
    return cmd_serve(args)


# ======================================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aegis_care",
        description="AEGIS-Care: a privacy-bounded memory recompiler for recovering "
                    "poisoned clinical AI agents (simulated FHIR sandbox only).")
    parser.add_argument("--model", default=None,
                        help="model spec: 'deterministic' (default) or "
                             "'openai:qwen3:8b@http://localhost:11434/v1'")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_incident_args(p, default_family="F1"):
        p.add_argument("--family", default=default_family, choices=list(FAMILIES))
        p.add_argument("--task", default=None, help="task id, e.g. T-DOC-01")
        p.add_argument("--depth", type=int, default=4, choices=[1, 2, 3, 4])
        p.add_argument("--provenance", default="targeted",
                       choices=list(ProvenanceMask.CONDITIONS))

    p = sub.add_parser("demo", help="one incident end to end with a recovery certificate")
    add_incident_args(p)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("baselines", help="all nine recovery conditions on one incident")
    add_incident_args(p)
    p.set_defaults(func=cmd_baselines)

    p = sub.add_parser("experiment", help="the full paired matrix, report, and figures")
    p.add_argument("--families", nargs="+", default=list(FAMILIES), choices=list(FAMILIES))
    p.add_argument("--depths", nargs="+", type=int, default=[2, 3, 4])
    p.add_argument("--provenance", nargs="+",
                   default=["complete", "random40", "targeted"],
                   choices=list(ProvenanceMask.CONDITIONS))
    p.add_argument("--tasks-per-family", type=int, default=1)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("privacy", help="empirical leakage attacks on the capsule interface")
    p.add_argument("--family", default="F1", choices=list(FAMILIES))
    p.add_argument("--depth", type=int, default=4, choices=[1, 2, 3, 4])
    p.set_defaults(func=cmd_privacy)

    p = sub.add_parser("serve", help="run the presentation, console, and API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--open", dest="open_browser", action="store_true",
                   help="open the presentation in your browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("present", help="launch the guided presentation and open a browser")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_present)

    p = sub.add_parser("console", help="launch the clinical operations console")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--fhir-url", default=None,
                   help="base URL of a real FHIR R4 server, e.g. https://hapi.fhir.org/baseR4")
    p.add_argument("--fhir-token", default=None, help="bearer token for that server")
    p.add_argument("--db", default=None,
                   help="path to the incident database, e.g. ./aegis-ops.sqlite")
    p.set_defaults(func=cmd_console)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
