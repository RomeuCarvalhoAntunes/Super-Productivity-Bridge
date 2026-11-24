"""Plan imports, track task IDs locally, and apply changes incrementally."""
import fcntl
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from bridge.api import Api, ApiError, Snapshot
from bridge.model import fields


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_path = directory / "import.lock"
        self.db = sqlite3.connect(directory / "imports.sqlite3")
        self.db.execute("""CREATE TABLE IF NOT EXISTS imports (
            external_id TEXT PRIMARY KEY, task_id TEXT UNIQUE,
            status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
        )""")
        # Translate records from builds that stored status values in Portuguese.
        self.db.executemany(
            "UPDATE imports SET status = ? WHERE status = ?",
            (("pending", "pendente"), ("linked", "vinculada"), ("completed", "concluída")),
        )
        self.db.commit()

    @contextmanager
    def lock(self):
        with self.lock_path.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another instance is applying an import.") from None
            yield

    def get(self, external_id):
        return self.db.execute(
            "SELECT task_id, status FROM imports WHERE external_id = ?", (external_id,)
        ).fetchone()

    def owner(self, task_id):
        return self.db.execute(
            "SELECT external_id FROM imports WHERE task_id = ?", (task_id,)
        ).fetchone()

    def record(self, external_id, task_id, status, detail=""):
        with self.db:
            self.db.execute(
                """INSERT INTO imports VALUES (?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET task_id=excluded.task_id,
                status=excluded.status, detail=excluded.detail""",
                (external_id, task_id, status, detail),
            )

    def clear_pending(self, external_id):
        with self.db:
            self.db.execute(
                "DELETE FROM imports WHERE external_id = ? AND status = 'pending'", (external_id,)
            )


def local_journal() -> Journal:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return Journal(base / "super-productivity-bridge")


def normalized(title: str) -> str:
    return " ".join(title.casefold().split())


def resolve(value: str, choices: list[dict], label: str) -> dict:
    matches = [item for item in choices if item["id"] == value]
    if not matches:
        matches = [item for item in choices if normalized(item["title"]) == normalized(value)]
    if len(matches) != 1:
        raise ValueError(f"{label} not found or ambiguous: {value}. Use the ID shown after connecting.")
    return matches[0]


@dataclass
class Operation:
    external_id: str
    kind: str
    task_id: str | None
    parent: str | None
    title: str
    changes: dict
    before: dict
    notices: list[str]


def plan(document: dict, snapshot: Snapshot, journal: Journal, default_project: str) -> list[Operation]:
    projects = [project for project in snapshot.projects if not project.get("isArchived")]
    tags = [tag for tag in snapshot.tags if tag["id"] != "TODAY"]
    active = {task["id"]: task for task in snapshot.tasks}
    archived = {task["id"]: task for task in snapshot.archived}
    operations = []
    claimed = set()
    new_titles = set()

    def add(item, parent=None, project_id=None):
        external = item["externalId"]
        try:
            payload = fields(item)
            if parent is None:
                project_id = resolve(item.get("project") or default_project, projects, "Project")["id"]
                payload["projectId"] = project_id
            if "tags" in item:
                payload["tagIds"] = list(dict.fromkeys(resolve(tag, tags, "Tag")["id"] for tag in item["tags"]))
            link = journal.get(external)
            explicit = item.get("taskId")
            if link and link[0] and explicit and link[0] != explicit:
                raise ValueError("The taskId conflicts with the saved link.")
            task_id = (link and link[0]) or explicit
            if link and link[1] == "pending" and not task_id:
                raise ValueError("A previous creation could not be confirmed. Find the task and provide its taskId.")
            before = {}
            notices = []
            if "priority" in item:
                notices.append(f"Suggested priority to set manually: {item['priority']}.")
            if normalized(item["title"]) in {"study", "review", "task", "project", "work"}:
                notices.append("Generic title. Specify the expected result.")
            if not item.get("subtasks") and item.get("estimatedMinutes", 0) >= 90 and parent is None:
                notices.append("Consider splitting this task into preparation and execution subtasks.")
            if item.get("scheduledDate", "") > item.get("deadlineDate", "9999-12-31"):
                notices.append("The scheduled date is after the deadline.")
            if task_id:
                current = active.get(task_id)
                if current is None:
                    raise ValueError(f"Linked task is not active: {task_id}.")
                if (current.get("parentId") or None) != (parent.task_id if parent else None) or (parent and not parent.task_id):
                    raise ValueError("The linked task belongs to a different hierarchy.")
                owner = journal.owner(task_id)
                if task_id in claimed or (owner and owner[0] != external):
                    raise ValueError("The taskId is already linked to another externalId.")
                if current.get("repeatCfgId") and project_id != current.get("projectId") and parent is None:
                    raise ValueError("Moving this recurring task would also move other occurrences.")
                claimed.add(task_id)
                if "dueDay" in payload and current.get("dueWithTime"):
                    raise ValueError("The task already has a scheduled time. Adjust it in Super Productivity first.")
                before = {key: current.get(key) for key in payload if current.get(key) != payload[key]}
                payload = {key: payload[key] for key in before}
                kind = "Update" if payload else "No changes"
            else:
                key = (parent.external_id if parent else None, project_id, normalized(item["title"]))
                if key in new_titles:
                    raise ValueError("Another new task in the file has the same title and destination.")
                if parent is None or parent.task_id:
                    candidates = [
                        task["id"] for task in (*active.values(), *archived.values())
                        if (parent.task_id if parent else None) == (task.get("parentId") or None)
                        and (parent or task.get("projectId") == project_id)
                        and normalized(task["title"]) == key[2]
                    ]
                    if candidates:
                        raise ValueError("Possible duplicate. Check these IDs: " + ", ".join(sorted(set(candidates))))
                new_titles.add(key)
                kind = "Create"
            operation = Operation(external, kind, task_id, parent.external_id if parent else None,
                                  item["title"], payload, before, notices)
        except ValueError as error:
            operation = Operation(external, "Blocked", None, parent.external_id if parent else None,
                                  item["title"], {}, {}, [str(error)])
        operations.append(operation)
        if operation.kind != "Blocked":
            for child in item.get("subtasks", []):
                add(child, operation, project_id)

    for item in document["tasks"]:
        add(item)
    return operations


def apply(api: Api, journal: Journal, document: dict, project: str, approved: list[Operation]) -> list[str]:
    if not approved or any(operation.kind == "Blocked" for operation in approved):
        raise ValueError("Resolve blocked operations and generate a new preview.")
    results = []
    ids = {}
    with journal.lock():
        if plan(document, api.snapshot(), journal, project) != approved:
            raise ValueError("Tasks or settings changed since the preview. Generate a new preview.")
        for operation in approved:
            external = operation.external_id
            task_id = operation.task_id
            payload = operation.changes.copy()
            try:
                if operation.kind == "Create":
                    if operation.parent:
                        payload["parentId"] = ids[operation.parent]
                    journal.record(external, None, "pending")
                    try:
                        response = api.request("POST", "/tasks", payload | {"isIgnoreShortSyntax": True})
                    except ApiError as error:
                        if error.rejected:
                            journal.clear_pending(external)
                        raise
                    task_id = response.get("id") if isinstance(response, dict) else None
                    if not isinstance(task_id, str) or not task_id:
                        raise ApiError("The API returned no task ID. Check the task in Super Productivity.")
                    journal.record(external, task_id, "linked")
                elif operation.kind == "Update":
                    journal.record(external, task_id, "linked")
                    api.task("PATCH", task_id, payload | {"isIgnoreShortSyntax": True})
                else:
                    journal.record(external, task_id, "completed")
                if payload:
                    persisted = api.task("GET", task_id)
                    if not isinstance(persisted, dict) or persisted.get("id") != task_id:
                        raise ApiError("Verification returned an unexpected task ID.")
                    differences = [key for key, value in payload.items() if persisted.get(key) != value]
                    if differences:
                        raise ApiError("Verification found differences in " + ", ".join(differences))
                    journal.record(external, task_id, "completed")
                ids[external] = task_id
                results.append(f"{operation.kind} · {operation.title} · {task_id}")
            except (ApiError, sqlite3.Error) as error:
                results.append(f"Stopped at {operation.title} · {error}")
                results.append("Earlier changes were saved. Generate a new preview before resuming.")
                break
    return results
