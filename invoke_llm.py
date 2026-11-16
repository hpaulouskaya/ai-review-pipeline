import json
import sys
from openai import OpenAI
from datetime import datetime
from pathlib import Path

client = OpenAI()
run_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = Path("runs") / run_id
run_dir.mkdir()

with open("submissions.json", "r") as file:

    submissions = json.load(file)

with open("prompts/system_prompt.md", "r") as file:

    system_prompt = file.read()

with open("prompts/user_prompt.md", "r") as file:

    user_prompt_template = file.read()

predictions = []
for app in submissions:
    user_prompt = user_prompt_template.format(
        app_name=app["app_name"],
        description=app["description"]
    )
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            instructions=system_prompt,
            input=user_prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "app_review",
                    "strict": True,
                    "schema": {

                        "type": "object",

                        "properties": {

                            "checks": {

                                "type": "array",

                                "items": {

                                    "type": "object",

                                    "properties": {

                                        "policy_check": {

                                            "type": "string",

                                            "enum": [

                                                "metadata_quality",

                                                "real_money_gambling",

                                                "child_safety"

                                            ]

                                        },

                                        "verdict": {

                                            "type": "string",

                                            "enum": ["PASS", "FAIL"]

                                        },

                                        "justification": {

                                            "type": "string"

                                        }

                                    },

                                    "required": [

                                        "policy_check",

                                        "verdict",

                                        "justification"

                                    ],

                                    "additionalProperties": False

                                }

        }

    },

    "required": ["checks"],

    "additionalProperties": False
                    }
                }
            }
        )
        result = json.loads(response.output_text)

        expected_checks = {

            "metadata_quality",

            "real_money_gambling",

            "child_safety"

        }

        returned_checks = [

            check["policy_check"]

            for check in result["checks"]

        ]

        if (

            len(returned_checks) != len(expected_checks)

            or set(returned_checks) != expected_checks

        ):

            raise ValueError("Missing or duplicate policy checks")

    except Exception as error:

        print("ERROR processing submission:", app["submission_id"])

        print("Details:", error)

        continue
    
    predictions.append({

        "submission_id": app["submission_id"],

        "checks": result["checks"]

    })

    print("Submission ID:", app["submission_id"])

    print()

print("\nALL PREDICTIONS:")

print(json.dumps(predictions, indent=2))

if len(predictions) == len(submissions):
    with open(run_dir / "predictions.json", "w") as file:
        json.dump(predictions, file, indent=2)

else:
    print("ERROR: Incomplete predictions. Existing file was not overwritten.")
    sys.exit(1)