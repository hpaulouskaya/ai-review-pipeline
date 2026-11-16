import sys
print(sys.argv)
if len(sys.argv) != 3:
    print("ERROR: Incorrect number of arguments.")
    print("Usage: python main.py model_predictions.json golden_dataset.json")
    sys.exit(1)

import json

with open(sys.argv[2], "r") as file:
    dataset = json.load(file)

with open(sys.argv[1], "r") as file:
    predictions = json.load(file)

print("Total submissions:", len(dataset))
print("Total predictions:", len(predictions))

if len(dataset) == 0:
    print("EMPTY GOLDEN DATASET")

if len(predictions) == 0:
    print("EMPTY PREDICTIONS")

correct_predictions = 0
true_positives = 0
false_positives = 0
false_negatives = 0
missing_predictions = 0
missing_ground_truth = 0
duplicate_predictions = 0
duplicate_ground_truth = 0 
invalid_predicted_verdicts = 0
invalid_expected_verdicts = 0
seen_prediction_ids = set()
seen_golden_ids = set()

for prediction in predictions:
    found_match = False
    current_id = prediction["submission_id"]
    if prediction["predicted_verdict"] not in ["approved", "rejected"]:
        print(current_id, "INVALID PREDICTED VERDICT")
        invalid_predicted_verdicts += 1    
    if current_id in seen_prediction_ids:
        print(current_id, "DUPLICATE PREDICTION")
        duplicate_predictions += 1
    seen_prediction_ids.add(current_id)
    for submission in dataset:
        if submission["submission_id"] == prediction["submission_id"]:
         found_match = True
    if found_match == False:
        print(prediction["submission_id"], "MISSING GROUND TRUTH")
        missing_ground_truth += 1

for submission in dataset:
    found_match = False
    current_id = submission["submission_id"]
    if submission["expected_verdict"] not in ["approved", "rejected"]:
        print(current_id, "INVALID EXPECTED VERDICT")
        invalid_expected_verdicts += 1

    if current_id in seen_golden_ids:
        print(current_id, "DUPLICATE GROUND TRUTH")
        duplicate_ground_truth += 1
    seen_golden_ids.add(current_id)

    for prediction in predictions:
        if submission["submission_id"] == prediction["submission_id"]:
            found_match = True
    if found_match == False:
        print(submission["submission_id"], "MISSING PREDICTION")
        missing_predictions += 1

if (
    missing_predictions > 0
    or missing_ground_truth > 0
    or duplicate_predictions > 0
    or duplicate_ground_truth > 0
    or invalid_predicted_verdicts > 0
    or invalid_expected_verdicts > 0
    or len(dataset) == 0
    or len(predictions) == 0
):
    print(
    "Validation failed:",
     missing_predictions, "missing predictions,",
     missing_ground_truth, "missing ground truth,",
     duplicate_predictions, "duplicate predictions,",
     duplicate_ground_truth, "duplicate ground truth,",
     invalid_predicted_verdicts, "invalid predicted verdicts,",
     invalid_expected_verdicts, "invalid expected verdicts"

)
    sys.exit(1)

for submission in dataset:

    for prediction in predictions:
        if submission["submission_id"] == prediction["submission_id"]:
            if submission["expected_verdict"] == prediction["predicted_verdict"]:
                print(submission["submission_id"], "PASS")
                correct_predictions += 1
                if submission["expected_verdict"] == "approved":
                  true_positives += 1
            else:
                print(submission["submission_id"], "FAIL")
                if submission["expected_verdict"] == "rejected" and prediction["predicted_verdict"] == "approved":
                    false_positives += 1
                if submission["expected_verdict"] == "approved" and prediction["predicted_verdict"] == "rejected":
                    false_negatives += 1
    
print("Correct predictions:", correct_predictions) 
print("True positives:", true_positives)
print("False positives:", false_positives)
accuracy = correct_predictions / len(dataset)
print(f"Accuracy: {accuracy:.2%}")
if true_positives + false_positives > 0:
    precision = true_positives / (true_positives + false_positives)
    print(f"Approve Precision: {precision:.2%}")
else: 
    print("Approve Precision: N/A")
if true_positives + false_negatives > 0:
    recall = true_positives / (true_positives + false_negatives)
    print(f"Approve Recall: {recall:.2%}")
else:
    print("Approve Recall: N/A")
print("False negatives:", false_negatives)