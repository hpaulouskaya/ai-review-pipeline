"""Offline end-to-end demo (M08.3): recorded predictions -> review completion.

Usage:
    python demo.py

No API key, network, MCP server or third-party packages are needed.

The demo does NOT run inference. It starts from RECORDED predictions:
fixtures/demo_predictions.json is an unmodified copy of the real
gpt-4.1-mini output of the M07.3 description-only baseline run
(2026-11-19_14-25-55) on the synthetic submissions. Live inference is
`python invoke_llm.py` (paid OpenAI API calls).

Steps, each run as the existing CLI in a subprocess (stops at the first failure):
    1. routing_engine.py  predictions.json                 -> routing_results.json
    2. main.py            predictions.json + golden         -> eval_results.json
    3. human_review.py    routing_results.json + decisions  -> review_completion.json

Everything is written to a new folder runs/demo_<timestamp>/ (git-ignored).
Committed fixtures and the reference run are only read.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
RUNS_ROOT = PROJECT_DIR / "runs"
DEMO_PREDICTIONS = PROJECT_DIR / "fixtures" / "demo_predictions.json"
DEMO_DECISIONS = PROJECT_DIR / "fixtures" / "example_human_decisions.json"
GOLDEN_DATASET = PROJECT_DIR / "golden_dataset.json"
ARTIFACTS = ("predictions.json", "routing_results.json", "routing_config_used.json",
             "eval_results.json", "golden_dataset_used.json", "review_completion.json")


def create_demo_run_dir(runs_root=RUNS_ROOT):
    """New runs/demo_<timestamp>[_N]/; never reuses an existing folder."""
    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    for attempt in range(100):
        run_dir = runs_root / (f"demo_{stamp}" if attempt == 0 else f"demo_{stamp}_{attempt}")
        try:
            run_dir.mkdir()
            return run_dir
        except FileExistsError:
            continue
    raise RuntimeError(f"Could not create a new demo run folder in {runs_root}")


def _shown(path):
    """Path relative to the project when possible (no machine paths in the output)."""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path)


def run_step(number, label, script, *args):
    """Run one existing CLI with the current interpreter; exit on failure."""
    shown_args = [_shown(arg) for arg in args]
    print(f"\n=== Step {number}: {label} ===")
    print("$ python", script, *shown_args, flush=True)
    completed = subprocess.run([sys.executable, script, *shown_args], cwd=PROJECT_DIR)
    if completed.returncode != 0:
        print(f"\nDEMO FAILED at step {number} ({label}): exit code {completed.returncode}. "
              "Later steps were not run.")
        sys.exit(completed.returncode)


def main(argv=None, runs_root=RUNS_ROOT):
    argv = sys.argv if argv is None else argv
    if len(argv) != 1:
        print("ERROR: demo.py takes no arguments.")
        print("Usage: python demo.py")
        sys.exit(1)

    run_dir = create_demo_run_dir(runs_root)
    predictions = run_dir / "predictions.json"
    routing_results = run_dir / "routing_results.json"
    shutil.copyfile(DEMO_PREDICTIONS, predictions)
    print("Offline demo: RECORDED predictions (M07.3 baseline, gpt-4.1-mini), no API calls.")
    print("Run folder:", _shown(run_dir))

    run_step(1, "Routing", "routing_engine.py", predictions)
    run_step(2, "Evaluation", "main.py", predictions, GOLDEN_DATASET)
    run_step(3, "Human review completion", "human_review.py", routing_results, DEMO_DECISIONS)

    overall = json.loads((run_dir / "eval_results.json").read_text())["overall"]
    completion = json.loads((run_dir / "review_completion.json").read_text())
    pending = [s["submission_id"] for s in completion["submissions"]
               if s["completion_status"] != "CHECKS_RESOLVED"]
    print("\n=== Demo complete ===")
    print(f"Evaluation: {overall['correct_decisions']}/{overall['total_evaluated_pairs']} correct, "
          f"{overall['abstentions']} UNDECIDED, accuracy {overall['accuracy']:.2%}, "
          f"decision coverage {overall['decision_coverage']:.2%}")
    print(f"Review completion: {completion['summary']['checks_resolved']}/"
          f"{completion['summary']['total_submissions']} submissions resolved; "
          f"pending human review: {pending}")
    print("Artifacts:")
    for name in ARTIFACTS:
        print("  " + _shown(run_dir / name))
    return run_dir


if __name__ == "__main__":
    main()
