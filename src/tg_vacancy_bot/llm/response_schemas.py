"""JSON Schema contracts sent to Mistral for live/history vacancy analysis."""

from tg_vacancy_bot.llm.schemas import EXPECTED_FIELDS

_TEXT_FIELDS = EXPECTED_FIELDS - {
    "is_match",
    "experience_from",
    "salary_from",
    "salary_to",
    "primary_roles",
    "specializations",
    "required_stack",
    "preferred_stack",
}
_NUMBER_FIELDS = {"experience_from", "salary_from", "salary_to"}
_LIST_FIELDS = {
    "primary_roles",
    "specializations",
    "required_stack",
    "preferred_stack",
}


def _object_schema(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _analysis_schema(*, universal: bool = False) -> dict:
    properties = {}
    for field in sorted(EXPECTED_FIELDS):
        if field == "is_match":
            properties[field] = {"type": "boolean"}
        elif field in _LIST_FIELDS:
            properties[field] = {"type": "array", "items": {"type": "string"}}
            if universal and field in {"primary_roles", "specializations"}:
                properties[field]["maxItems"] = 0
        elif field in _NUMBER_FIELDS:
            properties[field] = {"type": ["number", "null"]}
        elif field in _TEXT_FIELDS:
            properties[field] = {"type": ["string", "null"]}
    return _object_schema(properties)


def legacy_response_format() -> dict:
    """Constrain the legacy Go answer while retaining local validation."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "vacancy_analysis",
            "strict": True,
            "schema": _analysis_schema(),
        },
    }


def universal_response_format(schema_version: str, prompt_version: str) -> dict:
    """Constrain the versioned envelope and its nested vacancy analysis."""
    classification = _object_schema(
        {
            "direction_id": {"type": "string"},
            "specialization_id": {"type": "string"},
            "role_id": {"type": "string"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        }
    )
    schema = _object_schema(
        {
            "schema_version": {"type": "string", "enum": [schema_version]},
            "prompt_version": {"type": "string", "enum": [prompt_version]},
            "is_vacancy": {"type": "boolean"},
            "classifications": {"type": "array", "items": classification},
            "analysis": _analysis_schema(universal=True),
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reason_code": {"type": "string"},
            "needs_review": {"type": "boolean"},
        }
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "universal_vacancy_analysis",
            "strict": True,
            "schema": schema,
        },
    }
