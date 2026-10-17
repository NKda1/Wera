"""Export the Pydantic contracts as JSON Schema to packages/schemas/json (SCH-03).

The web app generates its TypeScript types from responses.schema.json, so a Pydantic change
shows up as a type error in the UI instead of a runtime surprise. CI fails if the committed
files are stale (`wera schemas --check`).
"""

import json
from pathlib import Path

from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from .paths import SCHEMAS_DIR
from .schemas import REQUESTS, RESPONSES


class _Schema(GenerateJsonSchema):
    # Property titles ("Jobid") add noise and make the TS generator emit `type Jobid = string`.
    def field_title_should_be_set(self, schema) -> bool:  # noqa: ANN001
        return False


def _drop_null_defaults(node):
    """`{"$ref": X, "default": null}` makes the TS generator duplicate X as a new type. A null
    default says nothing a consumer needs (the field is simply not required), so drop it."""
    if isinstance(node, dict):
        if "default" in node and node["default"] is None:
            del node["default"]
        for v in node.values():
            _drop_null_defaults(v)
    elif isinstance(node, list):
        for v in node:
            _drop_null_defaults(v)
    return node


def build() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, models, mode in (("responses", RESPONSES, "serialization"), ("requests", REQUESTS, "validation")):
        _, schema = models_json_schema([(m, mode) for m in models], ref_template="#/$defs/{model}", schema_generator=_Schema)
        doc = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"WERA {name}",
            "description": f"Generated from the Pydantic models in services/agent/wera_agent/schemas ({mode} mode). Do not edit.",
            **_drop_null_defaults(schema),
        }
        out[f"{name}.schema.json"] = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    return out


def write(target: Path = SCHEMAS_DIR) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in build().items():
        path = target / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def stale(target: Path = SCHEMAS_DIR) -> list[str]:
    return [
        name
        for name, text in build().items()
        if not (target / name).exists() or (target / name).read_text(encoding="utf-8") != text
    ]
