# Project Snapshot — Mini Eval Harness

**Checkpoint:** M06 complete (reusable inference + three-verdict decision contract)
**Previous commits:** M06.1 `3a61fe7` (reusable, testable inference) · M05 `d81ec0b` · M04 `185a171`
**Date:** 2026-11-18
**Python:** 3.13 (`.venv`), dependencies: `openai==3.46.0`, `python-dotenv==1.1.1`

> [README.md](README.md) explains how to run the pipeline, the verdicts and the
> evaluation metrics. This file describes architecture, contracts, status and
> limitations.

---

## 1. What this project does

An AI app-review **screening** pipeline:

1. **Inference:** an LLM screens each app submission against three independent
   policy checks and returns `PASS`, `FAIL` or `UNDECIDED` with a justification
   for each.
2. **Routing:** decides, per check, whether the AI result can be used
   automatically or needs a human reviewer; tracks whether each submission
   still awaits human review.
3. **Evaluation:** measures the model's verdicts against a hand-labelled
   golden dataset for every (submission, policy check) pair, treating
   `UNDECIDED` as an abstention.

The model sees only `app_name` and `description`. This is description-based
screening, not a full compliance review.

Final developer-facing decisions (approve/reject the app), notifications,
enforcement and routing evaluation are **out of scope**.

---

## 2. Architecture

```text
.env (OPENAI_API_KEY) ─────┐
submissions.json ──────────┤
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

Routing and evaluation both read `predictions.json` independently and are run
as separate commands. Neither depends on the other's output.

### How the active components connect

| Component | Reads | Produces | Connects to |
|---|---|---|---|
| `submissions.json` | — | Apps (`submission_id`, `app_name`, `description`) | Input to `invoke_llm.py` |
| `prompts/system_prompt.md` | — | Decision contract (PASS/FAIL/UNDECIDED) and rules per check | LLM instructions in `invoke_llm.py` |
| `prompts/user_prompt.md` | — | Template with `{app_name}`, `{description}` | Filled per app by `invoke_llm.py` |
| `invoke_llm.py` | The three files above; `.env` (only when creating the real client) | `runs/<run_id>/predictions.json` | One OpenAI call per app. Strict schema enforces check names and verdict values. Saves only if all apps succeed. |
| `routing_config.json` | — | Per-check `auto_approve` / `auto_reject` | Permissions → `routing_engine.py`. Keys = canonical check names for both routing and evaluation. |
| `routing_engine.py` | `predictions.json`, `routing_config.json` | `routing_results.json`, `routing_config_used.json` | Per-check routing + submission `workflow_status`. Also provides `load_routing_config()` to `main.py`. |
| `golden_dataset.json` | — | Hand-labelled expected PASS/FAIL per check | Ground truth for `main.py` |
| `main.py` | `predictions.json`, `golden_dataset.json`, check names from `routing_config.json` | `eval_results.json`, `golden_dataset_used.json` | Model evaluation. Does not use routing permissions or routing results. |
| `test_*.py` | Module functions | Pass/fail | Unit + end-to-end tests; inference tests use a fake client |

### Inference (`invoke_llm.py`)

Reusable functions take the API client as a parameter (dependency injection),
so tests run with a fake client and no paid calls:

- `build_schema()` — strict JSON schema: exactly the three checks, verdict
  enum `PASS` / `FAIL` / `UNDECIDED`.
- `review_submission(client, app, ...)` — one API call; rejects missing or
  duplicate checks.
- `run_inference(client, submissions, ...)` — reviews every submission; a
  failed submission is reported and skipped.
- `save_predictions(...)` — writes `predictions.json` only if every submission
  succeeded (all-or-nothing).
- `main(client=None)` — creates the real OpenAI client only when none is
  injected, after loading `.env` with `python-dotenv`. Shell variables take
  precedence over `.env`. Importing the module has no side effects.

### Routing rules (`routing_engine.py`)

`route_check()` applies these rules in order:

1. Unknown check → `HUMAN_REVIEW` (`UNKNOWN_CHECK`).
2. Verdict not exactly `PASS`/`FAIL`/`UNDECIDED` (including non-strings) →
   `HUMAN_REVIEW` (`INVALID_VERDICT`).
3. `UNDECIDED` → `HUMAN_REVIEW` (`MODEL_UNDECIDED`). Checked **before** the
   config, so no permission can automate it.
4. `PASS` + `auto_approve: true` → `AUTO_APPROVE`;
   `FAIL` + `auto_reject: true` → `AUTO_REJECT`.
5. Anything else (including missing or non-boolean flags) → `HUMAN_REVIEW`
   (`CONFIG_REQUIRES_HUMAN`).

Justification never affects routing.

`route_submission()`: duplicates → `DUPLICATE_CHECK`, missing checks →
`MISSING_CHECK` placeholder. Any `HUMAN_REVIEW` (or no checks) →
`PENDING_HUMAN_REVIEW`, else `AUTOMATED_CHECKS_COMPLETE`.
One `AUTO_REJECT` never rejects the whole submission.

### Evaluation workflow (`main.py`)

1. **Load** predictions, golden dataset and check names.
2. **Validate** both datasets: empty, missing fields, duplicate IDs,
   missing/unknown/duplicate checks, invalid verdicts (golden: PASS/FAIL only;
   predictions: PASS/FAIL/UNDECIDED), missing prediction, missing ground
   truth. Any error → exit 1, no files written.
3. **Evaluate** every (submission_id, policy_check) pair: correct decision,
   incorrect decision (mismatch) or abstention (UNDECIDED).
4. **Report** overall and per-check counts and rates (accuracy, decision
   accuracy, decision coverage, UNDECIDED rate), PASS/FAIL precision and
   recall, 2×3 confusion matrix; mismatches and abstentions listed separately.
5. **Save** `eval_results.json` and `golden_dataset_used.json` in the run folder.

Undefined metrics (zero denominator) are `null` in JSON and `N/A` in the terminal.
Metric definitions: see [README.md](README.md#metrics).

---

## 3. Decision contract (M06.2)

| Verdict | Meaning | Routing | Evaluation |
|---|---|---|---|
| `PASS` | No violation of this check is revealed by the name and description. Not a certification of the whole app. | Config decides | Decision |
| `FAIL` | The information clearly indicates a violation. Explicit evidence wins over reassuring claims. | Config decides | Decision |
| `UNDECIDED` | Policy-relevant signals exist, but the evidence is insufficient or contradictory. Justification must name the open question. | Always `HUMAN_REVIEW` (`MODEL_UNDECIDED`) | Abstention |

- `UNDECIDED` is not used for a short description or because the full app
  was not inspected.
- `metadata_quality` is expected to be PASS/FAIL only; a name and description
  with clearly incompatible purposes is a FAIL.
- Golden labels stay `PASS` / `FAIL`. `UNDECIDED` is never ground truth.
- API errors and invalid responses are technical failures, not `UNDECIDED`.

---

## 4. Data contracts

| Field | Created by | Consumed by | Values |
|---|---|---|---|
| `submission_id` | `submissions.json` | Every stage; join key | e.g. `APP001` |
| `policy_check` | LLM (schema-enforced) | Routing, evaluation | `metadata_quality`, `real_money_gambling`, `child_safety` |
| `verdict` | LLM (schema-enforced) | Routing; evaluation (as predicted verdict) | `PASS`, `FAIL`, `UNDECIDED` |
| `justification` | LLM | Preserved in routing output; shown in eval mismatches and abstentions | free text |
| `expected_checks` | `golden_dataset.json` (human labels) | Evaluation | `{policy_check: "PASS" \| "FAIL"}` |
| `routing_action` | `route_check()` | Routing output | `AUTO_APPROVE`, `AUTO_REJECT`, `HUMAN_REVIEW` |
| `result_source` | `route_check()` | Routing output | `AI`, `PENDING_HUMAN` |
| `routing_reason` | Routing | Audit trail | `CONFIG_ALLOWS_AUTO_APPROVE`, `CONFIG_ALLOWS_AUTO_REJECT`, `CONFIG_REQUIRES_HUMAN`, `MODEL_UNDECIDED`, `UNKNOWN_CHECK`, `INVALID_VERDICT`, `DUPLICATE_CHECK`, `MISSING_CHECK` |
| `human_review_checks` | `route_submission()` | Reviewers | list of check names |
| `workflow_status` | `route_submission()` | Downstream layer | `PENDING_HUMAN_REVIEW`, `AUTOMATED_CHECKS_COMPLETE` |

**Policy check names must match in four places:** `prompts/system_prompt.md`,
the JSON schema in `invoke_llm.py`, the keys of `routing_config.json`, and the
`expected_checks` keys in `golden_dataset.json`.

**Verdict values must match in three places:** the schema enum in
`invoke_llm.py`, `VALID_VERDICTS` in `routing_engine.py`, and
`PREDICTION_VERDICTS` in `main.py`.

### `eval_results.json` structure

```text
predictions_file, golden_dataset_file, policy_checks
overall:     total_evaluated_pairs, correct_predictions, mismatches,
             correct_decisions, incorrect_decisions, abstentions,
             accuracy, decision_accuracy, decision_coverage, undecided_rate
per_check:   {check: total_evaluated, actual_pass, actual_fail,
                     predicted_pass, predicted_fail,
                     correct_decisions, incorrect_decisions, abstentions,
                     accuracy, decision_accuracy, decision_coverage, undecided_rate,
                     pass_precision, pass_recall, fail_precision, fail_recall,
                     confusion_matrix: {expected_PASS|FAIL:
                                        {predicted_PASS|FAIL|UNDECIDED: n}}}
mismatches:  [{submission_id, policy_check, expected_verdict,
               predicted_verdict, justification}]
abstentions: [same fields as mismatches]
```

`overall.correct_predictions` and `overall.mismatches` are kept for backward
compatibility; they equal `correct_decisions` and `incorrect_decisions`.

---

## 5. Execution

```bash
source .venv/bin/activate

python invoke_llm.py                                                            # paid API calls
python routing_engine.py runs/<run_id>/predictions.json                         # no API calls
python main.py runs/<run_id>/predictions.json golden_dataset.json               # no API calls
python test_main.py; python test_routing.py                                     # no API calls
python test_invoke_llm.py; python test_api_failure.py                           # no API calls (fake client)
```

`OPENAI_API_KEY` is read from `.env` (git-ignored) or the shell.

---

## 6. Verified results

### M04/M05 reference run `2026-11-15_15-36-16` (PASS/FAIL prompt, committed)

Routing: APP001 and APP003 `AUTOMATED_CHECKS_COMPLETE`; APP002
`PENDING_HUMAN_REVIEW` (`real_money_gambling`). 8 `AUTO_APPROVE`,
0 `AUTO_REJECT`, 1 `HUMAN_REVIEW`. Evaluation: 9/9 correct, accuracy 100%.

### M06.2 Part A — run `2026-11-17_22-00-12` (three-verdict prompt, local only)

All 9 verdicts identical to the reference run; no `UNDECIDED`.

| Submission | Verdicts (metadata / gambling / child) | Workflow status |
|---|---|---|
| APP001 | PASS / PASS / PASS | `AUTOMATED_CHECKS_COMPLETE` |
| APP002 | PASS / **FAIL** / PASS | `PENDING_HUMAN_REVIEW` (`real_money_gambling`, `CONFIG_REQUIRES_HUMAN`) |
| APP003 | PASS / PASS / PASS | `AUTOMATED_CHECKS_COMPLETE` |

Evaluation: 9 correct, 0 mismatches, 0 abstentions; accuracy, decision
accuracy and decision coverage 100%; UNDECIDED rate 0%.

| Check | Actual P/F | Predicted P/F/U | Accuracy | PASS prec / rec | FAIL prec / rec |
|---|---|---|---|---|---|
| metadata_quality | 3 / 0 | 3 / 0 / 0 | 100% | 100% / 100% | N/A / N/A |
| real_money_gambling | 2 / 1 | 2 / 1 / 0 | 100% | 100% / 100% | 100% / 100% |
| child_safety | 3 / 0 | 3 / 0 / 0 | 100% | 100% / 100% | N/A / N/A |

### M06.2 Part B — one synthetic behavioural check (1 API call, not saved)

`SYN001` "Daily Prize Wheel": *"Spin the wheel once a day to win prizes and
collect rewards."* Not part of `submissions.json` or the golden dataset.

| Check | Verdict | Routing |
|---|---|---|
| metadata_quality | PASS | `AUTO_APPROVE` |
| real_money_gambling | **UNDECIDED** — "does not specify whether these involve real money or cash prizes" | `HUMAN_REVIEW` (`MODEL_UNDECIDED`) |
| child_safety | PASS | `AUTO_APPROVE` |

Submission: `PENDING_HUMAN_REVIEW`. UNDECIDED overrode `auto_approve: true`.

### Tests (55, no API calls)

| File | Tests |
|---|---|
| `test_main.py` | 22 |
| `test_routing.py` | 21 |
| `test_invoke_llm.py` | 11 |
| `test_api_failure.py` | 1 |

---

## 7. File inventory

### Active

| File | Role |
|---|---|
| `invoke_llm.py` | LLM inference |
| `routing_engine.py` | Check routing + submission workflow |
| `main.py` | Check-level model evaluator |
| `test_main.py`, `test_routing.py`, `test_invoke_llm.py`, `test_api_failure.py` | Tests (fake client for inference) |
| `routing_config.json` | Routing permissions + canonical check names |
| `golden_dataset.json` | Check-level ground truth (9 labels, PASS/FAIL) |
| `submissions.json` | Input submissions |
| `prompts/system_prompt.md`, `prompts/user_prompt.md` | LLM prompts |
| `requirements.txt` | Python dependencies |
| `README.md`, `PROJECT_SNAPSHOT.md` | Documentation |
| `runs/2026-11-15_15-36-16/{predictions, routing_results, routing_config_used}.json` | Committed M04 reference artifacts |

### Not committed (see `.gitignore`)

`.env`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.DS_Store`, and all run
files except the three M04 reference artifacts. This includes the M06.2
Part A run `2026-11-17_22-00-12` and generated `eval_results.json` /
`golden_dataset_used.json`.

---

## 8. Known limitations

1. **Tiny golden dataset.** 9 labels, only 1 FAIL. FAIL precision/recall are
   undefined for `metadata_quality` and `child_safety` and rest on one example
   for `real_money_gambling`. 100% accuracy is weak evidence.
2. **Binary golden labels.** Golden labels are PASS/FAIL only. `UNDECIDED` is a
   model abstention, never a golden label.
3. **Description-based screening.** Only `app_name` and `description` are
   sent to the model; this is not a complete compliance review.
4. **UNDECIDED verified on one example.** The real model's UNDECIDED behaviour
   was checked on one synthetic submission (one API call), not on a labelled
   evaluation set. No golden pair is borderline, so abstention metrics on real
   output are untested.
5. **No prompt snapshot.** Inference runs do not save the prompt (or model
   settings) they used, so a run cannot be tied to a prompt version.
6. **Separate steps.** Routing and evaluation are run as separate commands;
   there is no single pipeline command or run manifest.
7. **Duplicate golden keys are undetectable.** Python's JSON parser keeps the
   last value if a check name appears twice inside `expected_checks`.
8. **Overwrites on re-run.** Routing and evaluation overwrite their output files
   in the run folder.
9. **Empty run folders.** If inference fails part-way, the run folder exists
   but contains no `predictions.json`.
10. **Unnamed checks** in routing appear as `null` in `human_review_checks`.
11. **Out of scope:** final dispositions, notifications, enforcement, routing
    evaluation, tool calling (deferred until a check needs external evidence).
