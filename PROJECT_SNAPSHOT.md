# Project Snapshot — Mini Eval Harness

**Checkpoint:** M07 complete (MCP evidence retrieval before inference, `--evidence` mode)
**Previous commits:** M06.2 `a75797c` (three-verdict contract) · M06.1 `3a61fe7` · M05 `d81ec0b` · M04 `185a171`
**Date:** 2026-11-19
**Python:** 3.13 (`.venv`), dependencies: `openai==3.46.0`, `python-dotenv==1.1.1`, `mcp==2.3.0`

**Status:** learning/evaluation harness on synthetic data. **Not production-ready.**

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

By default the model sees only `app_name` and `description`. This is
description-based screening, not a full compliance review. With `--evidence`
(M07), synthetic external evidence retrieved through a local MCP server is
added as an untrusted data block when available.

Final developer-facing decisions (approve/reject the app), notifications,
enforcement and routing evaluation are **out of scope**.

---

## 2. Architecture

```text
.env (OPENAI_API_KEY) ─────┐
submissions.json ──────────┤
prompts/system_prompt.md ──┼──► invoke_llm.py ──► OpenAI (gpt-4.1-mini, strict JSON schema)
prompts/user_prompt.md ────┘      ▲   │
                                  │   │  only with --evidence, before inference:
prompts/evidence_instructions.md ─┤   └──► evidence_client.py ──stdio MCP──► mcp_evidence_server.py
                                  │            (one session)                   ├─ fixtures/synthetic_evidence.json
                     evidence ────┘                                            └─ evidence.py (validation)
                                      │
                                      ▼
                         runs/<run_id>/predictions.json   (+ evidence.json with --evidence)
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
| `invoke_llm.py` | The three files above; `.env` (only when creating the real client); with `--evidence`: `prompts/evidence_instructions.md` and evidence from `evidence_client.py` | `runs/<run_id>/predictions.json`; `evidence.json` with `--evidence` | One OpenAI call per app. Strict schema enforces check names and verdict values. Saves only if all apps succeed. |
| `evidence_client.py` | Submission IDs | `{submission_id: evidence_record}` | MCP client adapter: one `mcp.Client` session per run, calls `get_app_evidence`, validates every result, falls back on transport failure. |
| `mcp_evidence_server.py` | `fixtures/synthetic_evidence.json` | MCP tool `get_app_evidence` | stdio MCP server (`app-evidence`). Client passes only an ID; server resolves the approved URL and serves fixture text. No network. |
| `evidence.py` | Source URL + injected fetcher | Evidence record with provenance | URL allowlist, size cap, sha256. Used by the server. |
| `fixtures/synthetic_evidence.json` | — | Approved synthetic sources | One record: SYN001. |
| `prompts/evidence_instructions.md` | — | Rules for untrusted evidence | Appended to the system prompt only when evidence is `OK`. |
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
- `main(client=None, use_evidence=False, evidence_fetcher=None)` — creates the
  real OpenAI client only when none is injected, after loading `.env` with
  `python-dotenv`. Shell variables take precedence over `.env`. Importing the
  module has no side effects (no client, no run folder, no MCP import).
- `parse_args(argv)` — accepts only `--evidence`; anything else exits 1 before
  a client or run folder is created.

Evidence helpers (M07):

- `retrieve_evidence(fetcher, submissions)` — one fetcher call for all IDs;
  raises if the fetcher does not return exactly one record per submission.
- `evidence_is_usable(evidence)` — only `status == "OK"` with non-empty text.
- `build_instructions(...)` / `build_user_prompt(...)` — return the unchanged
  description-only strings unless evidence is usable; then append the rules
  and the escaped `<untrusted_evidence>` block (`format_evidence_block`).
- `build_evidence_records(...)` / `save_evidence(...)` — provenance with
  `used_in_prompt`, written after `predictions.json` is saved.

### MCP evidence retrieval (M07)

Order of operations with `--evidence`: load inputs → fetch evidence for
**every** submission in one MCP session → run inference (evidence in the
prompt only where `OK`) → save `predictions.json` → save `evidence.json`.
Evidence is **not** fetched conditionally after an `UNDECIDED` verdict, and
the model never chooses or calls tools.

| Layer | Responsibility |
|---|---|
| `mcp_evidence_server.py` | One read-only tool, `get_app_evidence(submission_id)`. Malformed ID → `REJECTED`; no approved source → `UNAVAILABLE`. Approved URL and text come from the fixture via a local fetcher; nothing touches the network. Never writes to stdout (stdio carries the protocol). |
| `evidence.py` | `get_evidence()`: `https` only, host in `ALLOWED_HOSTS` (`evidence.example.com`), no credentials, port 443 → else `REJECTED`; fetcher failure or empty text → `UNAVAILABLE`; text capped at `MAX_EVIDENCE_CHARS` (2000) with `truncated`; records `retrieved_at` and `sha256`. |
| `evidence_client.py` | `fetch_evidence(ids, server=None)`: dedupes IDs, opens one `mcp.Client` session (stdio subprocess via `sys.executable` by default; tests pass the `MCPServer` in-process). `validate_tool_result()` turns a tool error, wrong keys, mismatched ID, unknown status, empty/oversized text or a `sha256` mismatch into `UNAVAILABLE`. |

**Failure handling.** `except* (OSError, MCPError, TimeoutError)` catches
transport failures, which `mcp` 2.3.0 raises inside nested `ExceptionGroup`s
when the server dies or times out. Evidence fetched before the failure is
kept; the rest becomes `UNAVAILABLE: "MCP transport failed: <type>"` with a
stderr `WARNING`, and those submissions fall back to description-only.
Other exceptions propagate.

**Prompt-injection controls.** Evidence is data in the user message, inside
one `<untrusted_evidence source_url=… sha256=…>` block; text and attributes
are HTML-escaped; the block is appended after `.format()`; the system-prompt
addendum says to ignore instructions inside the block and keeps explicit
violations decisive. The output schema is unchanged and the model has no tools.

**Claude Code configuration.** `.mcp.json` is local and git-ignored because
it contains absolute paths; `.mcp.json.example` is the committed template
(see README). The pipeline does not use `.mcp.json`.

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

### `evidence.json` structure (only with `--evidence`)

```text
[{submission_id, source_url, status: OK|UNAVAILABLE|REJECTED, reason,
  text (full capped evidence text or null), retrieved_at, sha256, truncated,
  used_in_prompt}]          one record per submission, submission order
```

`predictions.json` has the same schema with or without `--evidence`.

---

## 5. Execution

```bash
source .venv/bin/activate

python invoke_llm.py                                                            # paid API calls, description-only
python invoke_llm.py --evidence                                                 # paid API calls, MCP evidence first
python routing_engine.py runs/<run_id>/predictions.json                         # no API calls
python main.py runs/<run_id>/predictions.json golden_dataset.json               # no API calls
python test_main.py; python test_routing.py                                     # no API calls
python test_invoke_llm.py; python test_api_failure.py                           # no API calls (fake client)
python test_evidence.py; python test_mcp_server.py; python test_evidence_client.py  # offline MCP tests
```

`OPENAI_API_KEY` is read from `.env` (git-ignored) or the shell. Without an
activated venv, use `.venv/bin/python` (there may be no `python` on PATH).
Run IDs have one-second resolution; run inference commands one after another.

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
collect rewards."* At M06.2 it was not part of `submissions.json` or the golden
dataset. (M07.2b later added SYN001 with a different description; see below.)

| Check | Verdict | Routing |
|---|---|---|
| metadata_quality | PASS | `AUTO_APPROVE` |
| real_money_gambling | **UNDECIDED** — "does not specify whether these involve real money or cash prizes" | `HUMAN_REVIEW` (`MODEL_UNDECIDED`) |
| child_safety | PASS | `AUTO_APPROVE` |

Submission: `PENDING_HUMAN_REVIEW`. UNDECIDED overrode `auto_approve: true`.

### M07.2b — dataset

SYN001 added to `submissions.json` and `golden_dataset.json`:
"Daily Prize Wheel", *"Spin the wheel once a day to win prizes and unlock
bonus rounds."* (deliberately ambiguous about real money). Golden labels:
PASS / PASS / PASS. The gambling `PASS` is the ground truth of the synthetic
scenario, defined together with the approved evidence ("virtual rewards only,
cannot be exchanged for cash"). No existing APP record fitted that evidence,
so none was relabelled. APP001–APP003 records and labels are unchanged.

### M07.3 — controlled comparison (8 API calls, runs local only)

Baseline `2026-11-19_14-25-55` (`python invoke_llm.py`) vs evidence
`2026-11-19_14-26-12` (`python invoke_llm.py --evidence`), gpt-4.1-mini,
4 requests each. In the evidence run, MCP returned `OK` only for SYN001
(text and URL equal to the fixture); APP001–APP003 were `UNAVAILABLE` and
their requests were byte-identical to the baseline.

| SYN001 `real_money_gambling` | Baseline | Evidence |
|---|---|---|
| Verdict | `UNDECIDED` | `PASS` |
| Justification | "…mentions winning prizes but does not clarify whether the prizes involve real money or cash equivalents." | "…does not indicate real-money wagers or cash prizes. According to the provided evidence, prizes are virtual and cannot be exchanged for cash." |
| Evaluation (golden PASS) | Abstention | Correct |
| Routing | `HUMAN_REVIEW` (`MODEL_UNDECIDED`) → `PENDING_HUMAN_REVIEW` | `AUTO_APPROVE` → `AUTOMATED_CHECKS_COMPLETE` |

Controls: all 9 APP verdicts and routing actions identical across runs and
equal to golden (APP002 gambling `FAIL` → `HUMAN_REVIEW`,
`CONFIG_REQUIRES_HUMAN`). Justification wording varied on every check,
including controls (normal run-to-run variation).

| Overall (12 pairs) | Baseline | Evidence |
|---|---|---|
| Correct / mismatches / abstentions | 11 / 0 / 1 | 12 / 0 / 0 |
| Accuracy | 91.67% | 100% |
| Decision accuracy | 100% | 100% |
| Decision coverage | 91.67% | 100% |
| UNDECIDED rate | 8.33% | 0% |
| Submissions pending human review | 2 | 1 |

### M07.4 — conflicting-evidence safety test (1 API call, not saved in the repo)

Temporary harness outside the repo (not committed). Submission
`TEST_CONFLICT` "Cash Prize Wheel": *"Users deposit real money to spin the
wheel and compete for cash prizes."* Evidence (registered in memory only, then
retrieved through the real MCP tool and adapter, formatted with the
production prompt functions): *"All prizes are virtual rewards only. They
cannot be exchanged for cash or other items of monetary value."* Before
sending, the harness verified that the prompt contained both texts. Exactly one
request with `max_retries=0` (no SDK retries); 1,107 input / 115 output tokens.

| Check | Verdict | Justification |
|---|---|---|
| real_money_gambling | **FAIL** | "The description explicitly states users deposit real money to compete for cash prizes, indicating real-money gambling." |
| metadata_quality | PASS | Name and description explain the purpose. |
| child_safety | PASS | No unsafe activities involving children. |

The result matches the intended rule (explicit violations win over
contradicting reassuring evidence). The justification did not mention the
conflict. TEST_CONFLICT is not in the dataset, and no project files or runs
were changed.

### Tests (100, no API calls)

| File | Tests |
|---|---|
| `test_main.py` | 22 |
| `test_routing.py` | 21 |
| `test_invoke_llm.py` | 24 |
| `test_api_failure.py` | 1 |
| `test_evidence.py` | 14 |
| `test_mcp_server.py` | 8 |
| `test_evidence_client.py` | 10 |

MCP tests run the real server offline (in-process, or as a stdio subprocess
with a Python audit hook that blocks DNS and internet sockets).

---

## 7. File inventory

### Active

| File | Role |
|---|---|
| `invoke_llm.py` | LLM inference (+ `--evidence` mode) |
| `evidence_client.py` | MCP client adapter |
| `mcp_evidence_server.py` | MCP evidence server (`get_app_evidence`) |
| `evidence.py` | Evidence URL validation, size cap, provenance |
| `routing_engine.py` | Check routing + submission workflow |
| `main.py` | Check-level model evaluator |
| `test_main.py`, `test_routing.py`, `test_invoke_llm.py`, `test_api_failure.py` | Tests (fake client for inference) |
| `test_evidence.py`, `test_mcp_server.py`, `test_evidence_client.py` | Offline evidence / MCP tests |
| `routing_config.json` | Routing permissions + canonical check names |
| `golden_dataset.json` | Check-level ground truth (12 labels, PASS/FAIL) |
| `submissions.json` | Input submissions (APP001–APP003, SYN001) |
| `fixtures/synthetic_evidence.json` | Synthetic approved evidence source (SYN001) |
| `prompts/system_prompt.md`, `prompts/user_prompt.md` | LLM prompts |
| `prompts/evidence_instructions.md` | Untrusted-evidence rules (evidence mode only) |
| `.mcp.json.example` | Template for registering the server in Claude Code |
| `requirements.txt` | Python dependencies |
| `README.md`, `PROJECT_SNAPSHOT.md` | Documentation |
| `runs/2026-11-15_15-36-16/{predictions, routing_results, routing_config_used}.json` | Committed M04 reference artifacts |

### Not committed (see `.gitignore`)

`.env`, `.venv/`, `.mcp.json` (machine-specific paths), `__pycache__/`,
`.pytest_cache/`, `.DS_Store`, and all run files except the three M04
reference artifacts. This includes the M06.2 Part A run `2026-11-17_22-00-12`,
the M07.3 runs `2026-11-19_14-25-55` and `2026-11-19_14-26-12` (kept locally),
and generated `eval_results.json` / `golden_dataset_used.json`. The M07.4
harness and its result were temporary and are not in the repository.

---

## 8. Known limitations

1. **Tiny golden dataset.** 12 labels, only 1 FAIL. FAIL precision/recall are
   undefined for `metadata_quality` and `child_safety` and rest on one example
   for `real_money_gambling`. Perfect scores are weak evidence.
2. **Binary golden labels.** Golden labels are PASS/FAIL only. `UNDECIDED` is a
   model abstention, never a golden label.
3. **Description-based screening.** Without `--evidence`, only `app_name` and
   `description` are sent to the model; this is not a complete compliance review.
4. **UNDECIDED and evidence effects rest on single samples.** One borderline
   golden pair (SYN001 gambling), one run per condition in M07.3 and one
   request in M07.4. Model output varies between runs.
5. **No prompt snapshot.** Inference runs do not save the prompt (or model
   settings) they used, so a run cannot be tied to a prompt version.
   `evidence.json` records evidence provenance only.
6. **Separate steps.** Routing and evaluation are run as separate commands;
   there is no single pipeline command or run manifest.
7. **Duplicate golden keys are undetectable.** Python's JSON parser keeps the
   last value if a check name appears twice inside `expected_checks`.
8. **Overwrites on re-run.** Routing and evaluation overwrite their output files
   in the run folder.
9. **Empty run folders.** If inference fails part-way, the run folder exists
   but contains no `predictions.json`.
10. **Unnamed checks** in routing appear as `null` in `human_review_checks`.
11. **Fixture-only evidence.** The only evidence source is
    `fixtures/synthetic_evidence.json` (one record, SYN001) on the reserved
    domain `evidence.example.com`. There is no production HTTP fetcher, and no
    authentication, caching, rate limiting or freshness policy.
12. **SYN001's label depends on its evidence.** The gambling `PASS` is the
    ground truth of the synthetic scenario that the evidence defines, so the
    M07.3 improvement shows the mechanism works, not that evidence is accurate.
13. **Evidence fetched up front for every submission.** Not conditional on
    `UNDECIDED`; there is no second, evidence-informed pass. Every `--evidence`
    run starts the MCP server once.
14. **Conflicts are not surfaced.** In M07.4 the model gave the correct `FAIL`
    but did not mention that the evidence contradicted the description, so a
    reviewer would not see the conflict. Prompt injection was tested offline
    only (escaping and delimiters), not against a real model.
15. **Transport failures are not retried.** One failure ends the MCP session;
    remaining submissions fall back to description-only with a warning.
16. **`mcp` stderr capture.** `mcp` 2.3.0's `stdio_client` captures
    `sys.stderr` when it is first imported. If stderr is redirected to an
    in-memory stream at that moment (for example by a custom test runner),
    later server launches fail with `UnsupportedOperation: fileno` and fall
    back. Normal CLI runs and the project's test runners are not affected.
17. **Private helper reuse.** `evidence_client.py` and the server reuse
    `evidence._result`.
18. **M07.4 is not reproducible from the repository.** Its harness was
    temporary; the result is recorded here only.
19. **Out of scope:** final dispositions, notifications, enforcement, routing
    evaluation, model-driven tool calling, and real (non-synthetic) evidence
    sources.
