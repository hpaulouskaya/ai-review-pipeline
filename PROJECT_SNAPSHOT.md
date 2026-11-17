# Project Snapshot — Mini Eval Harness

**Checkpoint:** M05 complete (golden dataset + check-level evals)
**Previous checkpoint:** M04 (check-level routing + submission workflow) — commit `185a171`
**Date:** 2026-11-16
**Python:** 3.13 (`.venv`), dependency: `openai==3.46.0`

> [README.md](README.md) explains how to run the pipeline and the evaluator.
> This file describes architecture, contracts, status and limitations.

---

## 1. What this project does

An AI app-review pipeline:

1. **Inference:** an LLM reviews each app submission against three independent
   policy checks and returns a `PASS`/`FAIL` verdict with a justification for each.
2. **Routing:** decides, per check, whether the AI result can be used
   automatically or needs a human reviewer; tracks whether each submission
   still awaits human review.
3. **Evaluation:** measures the model's verdicts against a hand-labelled
   golden dataset for every (submission, policy check) pair.

Final developer-facing decisions (approve/reject the app), notifications,
enforcement and routing evaluation are **out of scope**.

---

## 2. Architecture

```text
submissions.json ──────────┐
prompts/system_prompt.md ──┼──► invoke_llm.py ──► OpenAI (gpt-4.1-mini, strict JSON schema)
prompts/user_prompt.md ────┘          │
                                      ▼
                         runs/<run_id>/predictions.json
                           │                        │
routing_config.json ───────┼──► routing_engine.py   │
     (permissions)         │        ├──► routing_results.json
                           │        └──► routing_config_used.json
                           │                        │
routing_config.json ───────┴──► main.py ◄───────────┘
     (check names only)          ▲   ├──► eval_results.json
golden_dataset.json ─────────────┘   └──► golden_dataset_used.json
```

Routing and evaluation both read `predictions.json` independently.
Neither depends on the other's output.

### How the active components connect

| Component | Reads | Produces | Connects to |
|---|---|---|---|
| `submissions.json` | — | Apps (`submission_id`, `app_name`, `description`) | Input to `invoke_llm.py` |
| `prompts/system_prompt.md` | — | Policy definitions (3 checks, PASS/FAIL rules) | LLM instructions in `invoke_llm.py` |
| `prompts/user_prompt.md` | — | Template with `{app_name}`, `{description}` | Filled per app by `invoke_llm.py` |
| `invoke_llm.py` | The three files above | `runs/<run_id>/predictions.json` | One OpenAI call per app. Strict schema enforces check names and verdict values. Saves only if all apps succeed. |
| `routing_config.json` | — | Per-check `auto_approve` / `auto_reject` | Permissions → `routing_engine.py`. Keys = canonical check names for both routing and evaluation. |
| `routing_engine.py` | `predictions.json`, `routing_config.json` | `routing_results.json`, `routing_config_used.json` | Per-check routing + submission `workflow_status`. Also provides `load_routing_config()` to `main.py`. |
| `golden_dataset.json` | — | Hand-labelled expected verdict per check | Ground truth for `main.py` |
| `main.py` | `predictions.json`, `golden_dataset.json`, check names from `routing_config.json` | `eval_results.json`, `golden_dataset_used.json` | Model evaluation. Does not use routing permissions or routing results. |
| `test_routing.py` | `routing_engine` functions | Pass/fail | Unit tests with inline config |
| `test_main.py` | `main` functions | Pass/fail | Unit + end-to-end tests with inline data and temp files |

### Routing rules (`routing_engine.py`)

- `route_check()`: `PASS` + `auto_approve: true` → `AUTO_APPROVE`;
  `FAIL` + `auto_reject: true` → `AUTO_REJECT`; anything else → `HUMAN_REVIEW`.
  Unknown checks, invalid/non-string verdicts and missing/non-boolean flags
  always → `HUMAN_REVIEW`. Justification never affects routing.
- `route_submission()`: duplicates → `DUPLICATE_CHECK`, missing checks →
  `MISSING_CHECK` placeholder. Any `HUMAN_REVIEW` (or no checks) →
  `PENDING_HUMAN_REVIEW`, else `AUTOMATED_CHECKS_COMPLETE`.
  One `AUTO_REJECT` never rejects the whole submission.

### Evaluation workflow (`main.py`)

1. **Load** predictions, golden dataset and check names.
2. **Validate** both datasets: empty, missing fields, duplicate IDs,
   missing/unknown/duplicate checks, invalid verdicts, missing prediction,
   missing ground truth. Any error → exit 1, no files written.
3. **Evaluate** every (submission_id, policy_check) pair.
4. **Report** overall metrics; per check: raw counts, accuracy, PASS/FAIL
   precision and recall, confusion matrix; list of mismatches with justification.
5. **Save** `eval_results.json` and `golden_dataset_used.json` in the run folder.

Undefined metrics (zero denominator) are `null` in JSON and `N/A` in the terminal.
Metric definitions: see [README.md](README.md#metrics).

---

## 3. Data contracts

| Field | Created by | Consumed by | Values |
|---|---|---|---|
| `submission_id` | `submissions.json` | Every stage; join key | e.g. `APP001` |
| `policy_check` | LLM (schema-enforced) | Routing, evaluation | `metadata_quality`, `real_money_gambling`, `child_safety` |
| `verdict` | LLM | Routing; evaluation (as predicted verdict) | `PASS`, `FAIL` |
| `justification` | LLM | Preserved in routing output; shown in eval mismatches | free text |
| `expected_checks` | `golden_dataset.json` (human labels) | Evaluation | `{policy_check: "PASS" \| "FAIL"}` |
| `routing_action` | `route_check()` | Routing output | `AUTO_APPROVE`, `AUTO_REJECT`, `HUMAN_REVIEW` |
| `result_source` | `route_check()` | Routing output | `AI`, `PENDING_HUMAN` |
| `routing_reason` | Routing | Audit trail | `CONFIG_ALLOWS_AUTO_APPROVE`, `CONFIG_ALLOWS_AUTO_REJECT`, `CONFIG_REQUIRES_HUMAN`, `UNKNOWN_CHECK`, `INVALID_VERDICT`, `DUPLICATE_CHECK`, `MISSING_CHECK` |
| `human_review_checks` | `route_submission()` | Reviewers | list of check names |
| `workflow_status` | `route_submission()` | Downstream layer | `PENDING_HUMAN_REVIEW`, `AUTOMATED_CHECKS_COMPLETE` |

**Policy check names must match in four places:** `prompts/system_prompt.md`,
the JSON schema in `invoke_llm.py`, the keys of `routing_config.json`, and the
`expected_checks` keys in `golden_dataset.json`.

### Golden dataset format

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

### `eval_results.json` structure

```text
predictions_file, golden_dataset_file, policy_checks
overall:    total_evaluated_pairs, correct_predictions, mismatches, accuracy
per_check:  {check: total_evaluated, actual_pass, actual_fail,
                    predicted_pass, predicted_fail, accuracy,
                    pass_precision, pass_recall, fail_precision, fail_recall,
                    confusion_matrix: {expected_PASS|FAIL: {predicted_PASS|FAIL: n}}}
mismatches: [{submission_id, policy_check, expected_verdict,
              predicted_verdict, justification}]
```

---

## 4. Execution

```bash
source .venv/bin/activate

python invoke_llm.py                                                            # paid API calls
python routing_engine.py runs/2026-11-15_15-36-16/predictions.json              # no API calls
python main.py runs/2026-11-15_15-36-16/predictions.json golden_dataset.json    # no API calls
python test_routing.py                                                          # no API calls
python test_main.py                                                             # no API calls
```

`OPENAI_API_KEY` must be exported in the shell; the code does not load `.env`.

---

## 5. Verified results

### M04 — routing (run `2026-11-15_15-36-16`)

Config: all checks `auto_approve: true`, `auto_reject: false`.

| Submission | Workflow status | Human review checks |
|---|---|---|
| APP001 | `AUTOMATED_CHECKS_COMPLETE` | — |
| APP002 | `PENDING_HUMAN_REVIEW` | `real_money_gambling` |
| APP003 | `AUTOMATED_CHECKS_COMPLETE` | — |

Totals: 8 `AUTO_APPROVE`, 0 `AUTO_REJECT`, 1 `HUMAN_REVIEW`.

### M05 — model evaluation (same run, `golden_dataset.json`)

Overall: 9 pairs, 9 correct, 0 mismatches, accuracy 100%.

| Check | Actual P/F | Predicted P/F | Accuracy | PASS prec / rec | FAIL prec / rec |
|---|---|---|---|---|---|
| metadata_quality | 3 / 0 | 3 / 0 | 100% | 100% / 100% | N/A / N/A |
| real_money_gambling | 2 / 1 | 2 / 1 | 100% | 100% / 100% | 100% / 100% |
| child_safety | 3 / 0 | 3 / 0 | 100% | 100% / 100% | N/A / N/A |

### Tests

- `test_routing.py`: 17 tests
- `test_main.py`: 15 tests

---

## 6. File inventory

### Active

| File | Role |
|---|---|
| `invoke_llm.py` | LLM inference |
| `routing_engine.py` | Check routing + submission workflow |
| `main.py` | Check-level model evaluator |
| `test_routing.py`, `test_main.py` | Unit tests |
| `routing_config.json` | Routing permissions + canonical check names |
| `golden_dataset.json` | Check-level ground truth (9 labels) |
| `submissions.json` | Input submissions |
| `prompts/system_prompt.md`, `prompts/user_prompt.md` | LLM prompts |
| `requirements.txt` | Python dependencies |
| `README.md`, `PROJECT_SNAPSHOT.md` | Documentation |
| `runs/2026-11-15_15-36-16/{predictions, routing_results, routing_config_used}.json` | Committed M04 reference artifacts |

### Legacy (kept unchanged)

| File | Notes |
|---|---|
| `test_invoke_llm.py` | **Stale:** mocks the old single-verdict response shape; would now fail. Creates a run folder. |
| `test_api_failure.py` | **Stale:** mocks the old response shape. Creates a run folder. |

### Removed in M05

`model_predictions.json`, `test_predictions.json`, `test_predictions_empty.json`,
`test_golden_duplicates.json`, `test_golden_empty.json`, `test_golden_invalid.json`:
old single-verdict fixtures, unreferenced by any code or test, replaced by inline
test data in `test_main.py`. Recoverable from commit `185a171`.

### Not committed (see `.gitignore`)

`.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.DS_Store`, and all run
files except the three M04 reference artifacts. This includes generated
`eval_results.json` and `golden_dataset_used.json`.

---

## 7. Known limitations

1. **Tiny golden dataset.** 9 labels, only 1 FAIL. FAIL precision/recall are
   undefined for `metadata_quality` and `child_safety` and rest on one example
   for `real_money_gambling`. 100% accuracy is weak evidence.
2. **Duplicate golden keys are undetectable.** Python's JSON parser keeps the
   last value if a check name appears twice inside `expected_checks`.
3. **Overwrites on re-run.** Routing and evaluation overwrite their output files
   in the run folder.
4. **Stale inference tests.** `test_invoke_llm.py` and `test_api_failure.py`
   need updating before reuse.
5. **Empty run folders.** `invoke_llm.py` creates the run folder before any API
   call; failed runs leave empty folders.
6. **`.env` is not loaded automatically.** Export `OPENAI_API_KEY` in the shell.
7. **Unnamed checks** in routing appear as `null` in `human_review_checks`.
8. **Out of scope:** final dispositions, notifications, enforcement, routing evaluation.
