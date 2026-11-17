# Mini Eval Harness

A small Python pipeline for AI-assisted app review:

1. **Inference** — an LLM reviews each app submission against three
   independent policy checks and returns `PASS`/`FAIL` with a justification.
2. **Routing** — each check is routed to `AUTO_APPROVE`, `AUTO_REJECT` or
   `HUMAN_REVIEW` based on configurable permissions.
3. **Evaluation** — the model's verdicts are compared against a hand-labelled
   golden dataset for every (submission, policy check) pair.

Policy checks: `metadata_quality`, `real_money_gambling`, `child_safety`.

For architecture, data contracts and project status, see
[PROJECT_SNAPSHOT.md](PROJECT_SNAPSHOT.md).

---

## Setup

Requires Python 3.13.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Commands

Run from the project root.

```bash
# 1. Inference — makes paid OpenAI API calls.
#    OPENAI_API_KEY must be exported in the shell.
python invoke_llm.py
#    → runs/<run_id>/predictions.json

# 2. Routing — no API calls
python routing_engine.py runs/<run_id>/predictions.json
#    → runs/<run_id>/routing_results.json, routing_config_used.json

# 3. Evaluation — no API calls
python main.py runs/<run_id>/predictions.json golden_dataset.json
#    → runs/<run_id>/eval_results.json, golden_dataset_used.json

# Tests — no API calls
python test_routing.py
python test_main.py
```

Example with the committed reference run:

```bash
python main.py runs/2026-11-15_15-36-16/predictions.json golden_dataset.json
```

---

## Evaluation (`main.py`)

`main.py` evaluates the **model's** PASS/FAIL verdicts. It does not evaluate
routing.

### Inputs

**1. Predictions** — `runs/<run_id>/predictions.json`, produced by `invoke_llm.py`:

```json
[
  {
    "submission_id": "APP002",
    "checks": [
      {"policy_check": "metadata_quality", "verdict": "PASS", "justification": "..."},
      {"policy_check": "real_money_gambling", "verdict": "FAIL", "justification": "..."},
      {"policy_check": "child_safety", "verdict": "PASS", "justification": "..."}
    ]
  }
]
```

**2. Golden dataset** — `golden_dataset.json`, hand-labelled ground truth,
one expected verdict per policy check:

```json
[
  {
    "submission_id": "APP002",
    "expected_checks": {
      "metadata_quality": "PASS",
      "real_money_gambling": "FAIL",
      "child_safety": "PASS"
    }
  }
]
```

**3. Policy check names** — read from the keys of `routing_config.json`
(only the names are used; routing permissions are ignored).

Records are matched by `submission_id`. Verdicts must be exactly `PASS` or `FAIL`.

### Workflow

```text
LOAD      predictions, golden dataset, policy check names
  ↓
VALIDATE  both datasets
  ├── Empty dataset
  ├── Missing required fields (submission_id, checks / expected_checks,
  │                            policy_check, verdict)
  ├── Duplicate submission IDs
  ├── Missing, unknown or duplicate policy checks
  ├── Invalid verdicts (anything other than "PASS" / "FAIL")
  └── Missing prediction / missing ground truth (unexpected submission)
  ↓
GUARD     any error → print all errors, exit 1, write no files
  ↓
EVALUATE  each (submission_id, policy_check) pair independently
  ↓
REPORT    print metrics, confusion matrices, mismatches
  ↓
SAVE      eval_results.json + golden_dataset_used.json in the run folder
```

### Metrics

**Overall:** total evaluated pairs, correct predictions, mismatches, accuracy.

**Per policy check**, from a confusion matrix of raw counts:

|                   | predicted PASS | predicted FAIL |
|-------------------|----------------|----------------|
| **expected PASS** | correct PASS   | false alarm    |
| **expected FAIL** | missed violation | correct FAIL |

| Metric | Formula | Question it answers |
|---|---|---|
| Accuracy | correct / total | How often is the model right? |
| PASS precision | correct PASS / predicted PASS | When the model says PASS, is it right? |
| PASS recall | correct PASS / expected PASS | Does the model recognise compliant apps? |
| FAIL precision | correct FAIL / predicted FAIL | When the model flags a violation, is it real? |
| FAIL recall | correct FAIL / expected FAIL | Does the model catch real violations? |

Each check also reports raw counts: total evaluated, actual PASS/FAIL,
predicted PASS/FAIL.

**Undefined metrics:** when a denominator is zero (for example, FAIL recall
when the golden dataset has no FAIL labels for that check), the value is
`null` in `eval_results.json` and shown as `N/A` in the terminal.

### Mismatch diagnostics

Every pair where the prediction differs from the golden label is listed with:
`submission_id`, `policy_check`, `expected_verdict`, `predicted_verdict`
and the model's `justification`.

### Output files

Written next to the predictions file, in `runs/<run_id>/`:

| File | Contents |
|---|---|
| `eval_results.json` | Input file paths, policy checks, `overall`, `per_check` (metrics + `confusion_matrix`), `mismatches` |
| `golden_dataset_used.json` | Exact copy of the golden dataset used for this evaluation |

Re-running the evaluator on the same run overwrites both files.
These generated files are ignored by Git.

### Example output (reference run `2026-11-15_15-36-16`)

```text
=== OVERALL ===
Total evaluated pairs: 9
Correct predictions: 9
Mismatches: 0
Accuracy: 100.00%

=== real_money_gambling ===
Total: 3  Actual PASS/FAIL: 2/1  Predicted PASS/FAIL: 2/1
Accuracy: 100.00%
PASS precision: 100.00%  PASS recall: 100.00%
FAIL precision: 100.00%  FAIL recall: 100.00%
Confusion matrix (rows = expected, cols = predicted):
               pred PASS  pred FAIL
  exp PASS            2          0
  exp FAIL            0          1
```

`metadata_quality` and `child_safety` show `FAIL precision: N/A  FAIL recall: N/A`
because neither the golden dataset nor the model has a FAIL for them.

### Limitations of the current golden dataset

The golden dataset has **3 submissions × 3 checks = 9 labels**, of which
**only 1 is FAIL** (APP002 / `real_money_gambling`).

- 100% accuracy on 9 labels is weak evidence of model quality.
- FAIL precision and recall are **undefined** for `metadata_quality` and
  `child_safety`: the model's ability to catch those violations is untested.
- FAIL metrics for `real_money_gambling` rest on a single example.
- No borderline cases (e.g. social casino without real money, vague
  descriptions) are covered.

Meaningful FAIL metrics require more labelled submissions, especially
violations for each policy check.
