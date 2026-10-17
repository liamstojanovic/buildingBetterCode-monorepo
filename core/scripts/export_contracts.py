"""Export the shared wire contracts for non-Python clients."""

import json
from pathlib import Path

from canopy.contracts import RunConfig, RunEvent, RunResult, Task
from canopy.advisor_contracts import AdvisorLimits, AdvisorReport, DecisionDescriptor


def main() -> None:
    output = Path(__file__).resolve().parents[1] / "schemas"
    output.mkdir(exist_ok=True)
    for model in (Task, RunConfig, RunEvent, RunResult, AdvisorReport, AdvisorLimits, DecisionDescriptor):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (output / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
