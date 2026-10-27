"""Export the Workflow DSL as JSON Schema.

    python -m ghostflow.dsl.schema > ../schemas/workflow.schema.json
"""

import json

from ghostflow.dsl.models import DSL_VERSION, Workflow


def workflow_json_schema() -> dict:
    schema = Workflow.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://ghostflow.dev/schemas/workflow-{DSL_VERSION}.json"
    return schema


def main() -> None:
    print(json.dumps(workflow_json_schema(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
