# Mini Eval Harness

A Python-based evaluation tool that compares model predictions
against a golden dataset, validates input data, and calculates
evaluation metrics.

## Architecture

The evaluation pipeline follows five stages:

```text
LOAD
  ↓
VALIDATE
  ├── Empty Golden Dataset
  ├── Empty Predictions
  ├── Missing Predictions
  ├── Missing Ground Truth
  ├── Duplicate Predictions
  ├── Duplicate Ground Truth
  ├── Invalid Predicted Verdict
  └── Invalid Expected Verdict
  ↓
VALIDATION GUARD
  ├── Errors found → STOP
  └── No errors → Continue
  ↓
EVALUATE
  └── Compare predicted verdicts against expected verdicts
  ↓
REPORT
  ├── Accuracy
  ├── Approve Precision
  └── Approve Recall
```
## Input Files

The evaluation tool requires two JSON files:

### 1. Predictions

Contains model-generated verdicts for each submission.

Example:

```json
[
  {
    "submission_id": "APP001",
    "predicted_verdict": "approved"
  }
]
```

### 2. Golden Dataset

Contains the expected verdicts used as ground truth.

Example:

```json
[
  {
    "submission_id": "APP001",
    "expected_verdict": "approved"
  }
]
```

Both files use `submission_id` to match records.

Supported verdicts:
- `approved`
- `rejected`

## How to Run

### Requirements

- Python 3.13
- No external Python packages required

### Run Evaluation

Open a terminal in the project directory and run:

```bash
python3.13 main.py test_predictions.json golden_dataset.json
```

The program accepts two command-line arguments:

1. Path to the predictions JSON file.
2. Path to the golden dataset JSON file.

### Example Output

```text
Total submissions: 3
Total predictions: 3

APP001 PASS
APP002 PASS
APP003 PASS

Correct predictions: 3
True positives: 2
False positives: 0
False negatives: 0

Accuracy: 100.00%
Approve Precision: 100.00%
Approve Recall: 100.00%
```

If validation fails, the program reports the detected errors
and stops before calculating evaluation metrics.

## Validation & Metrics

### Input Validation

Before evaluating predictions, the tool performs eight validation checks:

1. Empty Golden Dataset
2. Empty Predictions
3. Missing Predictions
4. Missing Ground Truth
5. Duplicate Predictions
6. Duplicate Ground Truth
7. Invalid Predicted Verdict
8. Invalid Expected Verdict

If any validation check fails, the tool reports the errors
and stops before calculating evaluation metrics.

### Evaluation Metrics

The tool calculates three metrics:

**Accuracy**
- Percentage of all predictions that match the expected verdicts.

**Approve Precision**
- Percentage of predicted approvals that are actually approved
  according to the golden dataset.
- Formula: TP / (TP + FP)

**Approve Recall**
- Percentage of expected approvals correctly identified by the model.
- Formula: TP / (TP + FN)

For Precision and Recall, `approved` is the positive class.

When a metric's denominator is zero, the tool reports `N/A`
instead of attempting division by zero.