from pathlib import Path
import runpy
from unittest.mock import patch, MagicMock

mock_response = MagicMock()
mock_response.output_text = '{"verdict": "approved", "justification": "Mock success"}'
api_results = [
    mock_response,
    mock_response,
    RuntimeError("Simulated API failure")
]
runs_before = set(Path("runs").iterdir())
with patch("openai.OpenAI") as MockOpenAI:
    mock_client = MockOpenAI.return_value
    mock_client.responses.create.side_effect = api_results
    try:
        pipeline_vars = runpy.run_path("invoke_llm.py")
    except SystemExit as error:
        assert error.code == 1
        print("Pipeline exit code:", error.code)

runs_after = set(Path("runs").iterdir())
new_runs = runs_after - runs_before
assert len(new_runs) == 1
print("PASS: Exactly one new run directory was created")
new_run_dir = new_runs.pop()
predictions_path = new_run_dir / "predictions.json"
assert not predictions_path.exists()
print("PASS: No predictions.json saved after API failure")