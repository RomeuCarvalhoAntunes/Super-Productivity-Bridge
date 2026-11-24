"""Validate and map the import format."""
import json
import re
from datetime import date
from pathlib import Path

MAX_BYTES = 2_000_000
FIELDS = {
    "externalId", "taskId", "title", "description", "project", "tags",
    "priority", "estimatedMinutes", "scheduledDate", "deadlineDate", "subtasks",
}


def read_file(path: str) -> str:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("The file exceeds 2 MB.")
    return data.decode("utf-8-sig")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def parse(raw: str) -> dict:
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("The JSON exceeds 2 MB.")
    try:
        document = json.loads(raw, object_pairs_hook=unique_object)
    except RecursionError:
        raise ValueError("The JSON is nested too deeply.") from None
    if (not isinstance(document, dict) or set(document) != {"schemaVersion", "source", "tasks"}
            or document["schemaVersion"] != "1.0" or document["source"] != "chatgpt"
            or not isinstance(document["tasks"], list) or not document["tasks"]):
        raise ValueError("Use schemaVersion 1.0, source chatgpt, and a non-empty tasks array.")

    identifiers = set()

    def validate(item, path, child=False):
        if not isinstance(item, dict) or item.keys() - FIELDS:
            raise ValueError(f"{path}: invalid task or unknown field.")
        if child and item.keys() & {"subtasks", "project", "tags"}:
            raise ValueError(f"{path}: subtasks inherit the project and cannot define tags or nested subtasks.")
        for field in ("externalId", "title", "taskId", "project", "description"):
            if field not in item and field not in {"externalId", "title"}:
                continue
            value = item.get(field)
            if not isinstance(value, str) or (field != "description" and not value.strip()):
                raise ValueError(f"{path}.{field}: invalid text.")
            item[field] = value if field == "description" else value.strip()
        external = item["externalId"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", external) or external in identifiers:
            raise ValueError(f"{path}.externalId: invalid or duplicate ID.")
        identifiers.add(external)
        if "priority" in item and item["priority"] not in ("low", "medium", "high"):
            raise ValueError(f"{path}.priority: use low, medium, or high.")
        if "estimatedMinutes" in item:
            minutes = item["estimatedMinutes"]
            if type(minutes) is not int or not 0 <= minutes <= 1_000_000:
                raise ValueError(f"{path}.estimatedMinutes: use an integer between 0 and 1000000.")
        for field in ("scheduledDate", "deadlineDate"):
            if field in item:
                try:
                    value = item[field]
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except (TypeError, ValueError):
                    raise ValueError(f"{path}.{field}: use a valid YYYY-MM-DD date.") from None
        if "tags" in item:
            tags = item["tags"]
            if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags):
                raise ValueError(f"{path}.tags: provide an array of names or IDs.")
            item["tags"] = list(dict.fromkeys(t.strip() for t in tags))
        children = item.get("subtasks", [])
        if not isinstance(children, list):
            raise ValueError(f"{path}.subtasks: provide an array.")
        for index, subtask in enumerate(children, 1):
            validate(subtask, f"{path}.subtasks[{index}]", True)

    for index, item in enumerate(document["tasks"], 1):
        validate(item, f"tasks[{index}]")
    return document


def fields(item: dict) -> dict:
    mapping = {
        "title": "title", "description": "notes", "scheduledDate": "dueDay",
        "deadlineDate": "deadlineDay",
    }
    payload = {target: item[source] for source, target in mapping.items() if source in item}
    if "estimatedMinutes" in item:
        payload["timeEstimate"] = item["estimatedMinutes"] * 60_000
    return payload
