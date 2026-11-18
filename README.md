# Mini Eval Harness

A small Python pipeline for AI-assisted app review screening:

1. **Inference** — an LLM screens each app submission against three
   independent policy checks and returns `PASS`, `FAIL` or `UNDECIDED`
   with a justification.
2. **Routing** — each check is routed to `AUTO_APPROVE`, `AUTO_REJECT` or
   `HUMAN_REVIEW` based on configurable permissions. `UNDECIDED` always
   goes to human review.
3. **Evaluation** — the model's verdicts are compared against a hand-labelled
   golden dataset for every (submission, policy check) pair, with
   `UNDECIDED` measured as an abstention.

Policy checks: `metadata_quality`, `real_money_gambling`, `child_safety`.

This is **description-based screening**: the model sees only the app name
and description. A `PASS` means no violation was found in that text, not that
the whole app is compliant.

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

Create a `.env` file in the project root (it is git-ignored):

```text
OPENAI_API_KEY=<your key>
```

`invoke_llm.py` loads it automatically. A key already exported in the shell
takes precedence.

## Commands

Run from the project root.

```bash
# 1. Inference — makes paid OpenAI API calls (one per submission).
python invoke_llm.py
#    → runs/<run_id>/predictions.json

# 2. Routing — no API calls
python routing_engine.py runs/<run_id>/predictions.json
#    → runs/<run_id>/routing_results.json, routing_config_used.json

# 3. Evaluation — no API calls
python main.py runs/<run_id>/predictions.json golden_dataset.json
#    → runs/<run_id>/eval_results.json, golden_dataset_used.json

# Tests — no API calls (the inference tests use a fake client)
python test_main.py
python test_routing.py
python test_invoke_llm.py
python test_api_failure.py
```

Routing and evaluation are separate commands; run both on the same
`predictions.json`.

Example with the committed reference run:

```bash
python main.py runs/2026-11-15_15-36-16/predictions.json golden_dataset.json
```

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | The name and description do not reveal a violation of this check. |
| `FAIL` | The provided information clearly indicates a violation. Explicit violation evidence wins over contradictory reassuring claims. |
| `UNDECIDED` | Policy-relevant signals are present, but the evidence is insufficient or contradictory for a justified PASS or FAIL. The justification must name the unresolved question. |

`UNDECIDED` is a **model abstention**, not a policy outcome. It is never
used as a golden label. API errors and invalid responses are technical
failures, not `UNDECIDED`.

The full rules per check are in
[prompts/system_prompt.md](prompts/system_prompt.md).

---

## Evaluation (`main.py`)

`main.py` evaluates the **model's** verdicts. It does not evaluate routing.

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

Records are matched by `submission_id`. Golden labels must be exactly
`PASS` or `FAIL`. Predictions must be exactly `PASS`, `FAIL` or `UNDECIDED`.

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
  ├── Invalid verdicts (golden: not PASS/FAIL;
  │                     predictions: not PASS/FAIL/UNDECIDED)
  └── Missing prediction / missing ground truth (unexpected submission)
  ↓
GUARD     any error → print all errors, exit 1, write no files
  ↓
EVALUATE  each (submission_id, policy_check) pair independently
  ↓
REPORT    print metrics, confusion matrices, mismatches, abstentions
  ↓
SAVE      eval_results.json + golden_dataset_used.json in the run folder
```

### How each pair is counted

| Golden | Predicted | Counted as |
|---|---|---|
| PASS | PASS | correct decision |
| FAIL | FAIL | correct decision |
| PASS | FAIL | incorrect decision (mismatch) |
| FAIL | PASS | incorrect decision (mismatch) |
| PASS or FAIL | UNDECIDED | abstention — never correct, never a mismatch |

### Metrics

Per policy check, from a 2×3 confusion matrix of raw counts:

|                   | predicted PASS | predicted FAIL | predicted UNDECIDED |
|-------------------|----------------|----------------|---------------------|
| **expected PASS** | correct PASS   | false alarm    | abstention          |
| **expected FAIL** | missed violation | correct FAIL | abstention          |

With `decided = total − abstentions`:

| Metric | Formula | Question it answers |
|---|---|---|
| Accuracy | correct / total | How often is the model right, counting abstentions as not right? |
| Decision accuracy | correct / decided | When the model decides, is it right? |
| Decision coverage | decided / total | How often does the model decide at all? |
| UNDECIDED rate | abstentions / total | How often does the model abstain? |
| PASS precision | correct PASS / predicted PASS | When the model says PASS, is it right? |
| PASS recall | correct PASS / expected PASS | Does the model recognise compliant apps? |
| FAIL precision | correct FAIL / predicted FAIL | When the model flags a violation, is it real? |
| FAIL recall | correct FAIL / expected FAIL | Does the model catch real violations? |

- `UNDECIDED` is not a predicted PASS or FAIL, so it does not affect precision.
- Recall uses **all** expected labels, so an abstention on a real violation
  lowers FAIL recall.

The same counts and rates are reported overall. Each check also reports
actual PASS/FAIL and predicted PASS/FAIL counts.

**Undefined metrics:** when a denominator is zero (for example, FAIL recall
when the golden dataset has no FAIL labels for that check, or decision
accuracy when every prediction is UNDECIDED), the value is `null` in
`eval_results.json` and shown as `N/A` in the terminal.

### Diagnostics

- **Mismatches** — incorrect PASS/FAIL decisions only.
- **Abstentions** — every UNDECIDED pair, listed separately.

Both list `submission_id`, `policy_check`, `expected_verdict`,
`predicted_verdict` and the model's `justification`.

### Output files

Written next to the predictions file, in `runs/<run_id>/`:

| File | Contents |
|---|---|
| `eval_results.json` | Input file paths, policy checks, `overall`, `per_check` (metrics + `confusion_matrix`), `mismatches`, `abstentions` |
| `golden_dataset_used.json` | Exact copy of the golden dataset used for this evaluation |

Re-running the evaluator on the same run overwrites both files.
These generated files are ignored by Git.

### Example output (run `2026-11-17_22-00-12`, three-verdict prompt)

```text
=== OVERALL ===
Total evaluated pairs: 9
Correct decisions: 9
Incorrect decisions (mismatches): 0
Abstentions (UNDECIDED): 0
Accuracy: 100.00%  Decision accuracy: 100.00%  Coverage: 100.00%  UNDECIDED rate: 0.00%

=== real_money_gambling ===
Total: 3  Actual PASS/FAIL: 2/1  Predicted PASS/FAIL/UNDECIDED: 2/1/0
Correct: 3  Incorrect: 0  Abstentions: 0
Accuracy: 100.00%  Decision accuracy: 100.00%  Coverage: 100.00%  UNDECIDED rate: 0.00%
PASS precision: 100.00%  PASS recall: 100.00%
FAIL precision: 100.00%  FAIL recall: 100.00%
Confusion matrix (rows = expected, cols = predicted):
               pred PASS  pred FAIL  pred UNDECIDED
  exp PASS            2          0               0
  exp FAIL            0          1               0
```

`metadata_quality` and `child_safety` show `FAIL precision: N/A  FAIL recall: N/A`
because neither the golden dataset nor the model has a FAIL for them.

---

## Limitations

- **Small golden dataset.** 3 submissions × 3 checks = 9 labels, only 1 FAIL
  (APP002 / `real_money_gambling`). 100% accuracy on 9 labels is weak evidence;
  FAIL metrics are undefined for `metadata_quality` and `child_safety`.
- **Binary golden labels.** Golden labels are PASS/FAIL only. `UNDECIDED` is
  a model abstention, never a golden label.
- **Description-based screening.** The model sees only the app name and
  description; this is not a full compliance review.
- **UNDECIDED barely tested on real output.** The real model's UNDECIDED
  behaviour was verified on one synthetic example (one API call), not on an
  evaluation dataset. None of the 9 golden pairs is borderline.
- **No prompt snapshot.** Inference runs do not save the prompt they used.
- **Separate steps.** Routing and evaluation are run as separate commands.
