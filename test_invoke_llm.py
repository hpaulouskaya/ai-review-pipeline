import json
import runpy
from unittest.mock import patch, MagicMock

mock_response = MagicMock()

mock_response.output_text = json.dumps({

    "verdict": "approved",

    "justification": "Mock response for testing"

})

with patch("openai.OpenAI") as MockOpenAI:

    mock_client = MockOpenAI.return_value

    mock_client.responses.create.return_value = mock_response

    pipeline_vars = runpy.run_path("invoke_llm.py")

    run_dir = pipeline_vars["run_dir"]
    predictions_path = run_dir / "predictions.json"
    assert predictions_path.exists()
    print("PASS: predictions.json exists")

    with open("submissions.json", "r") as file:
        submissions = json.load(file)
    assert mock_client.responses.create.call_count == len(submissions)
    print("PASS: One mock API call per submission")

    with open(predictions_path, "r") as file:
        saved_predictions = json.load(file)
    assert len(saved_predictions) == len(submissions)
    print("PASS: All submissions have saved predictions")

    for prediction in saved_predictions:
        assert "submission_id" in prediction
        assert "predicted_verdict" in prediction
        assert "justification" in prediction
    print("PASS: All predictions have required fields")


input_ids = {app["submission_id"] for app in submissions}
output_ids = {prediction["submission_id"] for prediction in saved_predictions}
assert input_ids == output_ids
print("PASS: Submission IDs match")

allowed_verdicts = {"approved", "rejected"}
for prediction in saved_predictions:
    assert prediction["predicted_verdict"] in allowed_verdicts
print("PASS: All verdicts have valid values")