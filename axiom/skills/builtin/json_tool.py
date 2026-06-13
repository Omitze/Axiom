"""JSON tool — format, validate, and query JSON data."""

import json

from axiom.tools.base import Tool


class JsonTool(Tool):
    name = "json"
    description = (
        "Format, validate, and query JSON data. "
        "Supports: format (pretty-print), validate (check + report errors), "
        "and query (extract a value by key path like 'data.items[0].name')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "What to do with the JSON",
                "enum": ["format", "validate", "query"],
            },
            "json_string": {
                "type": "string",
                "description": "The JSON string to operate on",
            },
            "key_path": {
                "type": "string",
                "description": "Dot-separated key path for query (e.g. 'data.items[0].name')",
            },
        },
        "required": ["action", "json_string"],
    }

    def execute(
        self, action: str, json_string: str, key_path: str | None = None
    ) -> str:
        try:
            data = json.loads(json_string)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        if action == "validate":
            return "Valid JSON ✓"

        if action == "format":
            return json.dumps(data, indent=2, ensure_ascii=False)

        if action == "query":
            if not key_path:
                return "Error: key_path is required for query action"
            result = self._query(data, key_path)
            if result is None:
                return f"Key path '{key_path}' not found"
            return json.dumps(result, indent=2, ensure_ascii=False)

        return f"Unknown action: {action}"

    @staticmethod
    def _query(data, key_path: str):
        """Traverse a key path like 'data.items[0].name'."""
        current = data
        parts = key_path.replace("[", ".[").split(".")
        for part in parts:
            if part.startswith("["):
                try:
                    idx = int(part.strip("[]"))
                    current = current[idx]
                except (IndexError, TypeError, ValueError):
                    return None
            else:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
        return current


def create_tool() -> Tool:
    return JsonTool()
