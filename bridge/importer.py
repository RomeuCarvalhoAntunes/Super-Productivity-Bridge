"""Plan imports, track task IDs locally, and apply changes incrementally."""
import re
import sqlite3
from dataclasses import dataclass

from bridge.api import Api, ApiError, Snapshot
from bridge.journal import Journal
from bridge.model import fields
from bridge.review import revision


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
    affected: dict | None = None


def plan(document: dict, snapshot: Snapshot, journal: Journal, default_project: str) -> list[Operation]:
    projects = [project for project in snapshot.projects if not project.get("isArchived")]
    tags = [tag for tag in snapshot.tags if tag["id"] != "TODAY"]
    active = {task["id"]: task for task in snapshot.tasks}
    archived = {task["id"]: task for task in snapshot.archived}
    operations = []
    claimed = set()
    new_titles = set()

    def add(item, parent=None, project_id=None):
        action = item.get("action")
        explicit = item.get("taskId")
        external = item.get("externalId") or ":" + explicit
        current = active.get(explicit, {})
        title = item.get("title") or current.get("title") or explicit
        try:
            if action in {"update", "delete"}:
                if not current or current.get("isDone") or revision(current) != item["revision"]:
                    raise ValueError("The selected task is missing, closed, or changed. Export it again.")
            if action == "delete":
                if explicit in claimed or current.get("repeatCfgId"):
                    raise ValueError("Duplicate target or recurring task. Review it in Super Productivity.")
                children = current.get("subTaskIds") or []
                if any(child not in active for child in children):
                    raise ValueError("Could not verify all subtasks before deletion.")
                affected = {child: revision(active[child]) for child in children}
                affected[explicit] = revision(current)
                notices = [f"Also deletes: {active[child]['title']}" for child in children]
                operations.append(Operation(external, "Delete", explicit, None, title, {}, {}, notices, affected))
                claimed.add(explicit)
                return

            payload = fields(item)
            link = journal.get(external) if action != "update" else None
            if link and link[0] and explicit and link[0] != explicit:
                raise ValueError("The taskId conflicts with the saved link.")
            task_id = (link and link[0]) or explicit
            current = active.get(task_id, {})
            if action == "create" and explicit and not link:
                raise ValueError("A taskId on creation is only allowed to recover a pending import.")
            if action == "update" and current.get("parentId") and item.keys() & {"project", "tags", "subtasks"}:
                raise ValueError("Subtasks inherit their project and cannot have tags or children.")
            if parent is None:
                if action == "update" or (action == "create" and current):
                    project_id = current.get("projectId")
                    if "project" in item:
                        project_id = resolve(item["project"], projects, "Project")["id"]
                        payload["projectId"] = project_id
                else:
                    project_id = resolve(item.get("project") or default_project, projects, "Project")["id"]
                    payload["projectId"] = project_id
            if "tags" in item:
                payload["tagIds"] = list(dict.fromkeys(resolve(tag, tags, "Tag")["id"] for tag in item["tags"]))
            pending = link and link[1] == "pending" and not task_id
            before = {}
            notices = []
            if "priority" in item or "notes" in payload:
                marker = re.compile(r"^\[Bridge priority: (low|medium|high)\][ \t]*(?:\n|$)", re.MULTILINE)
                existing_notes = current.get("notes") or ""
                previous = marker.search(existing_notes)
                priority = item.get("priority") or (previous and previous[1])
                notes = marker.sub("", payload.get("notes", existing_notes))
                if priority:
                    notes = f"[Bridge priority: {priority}]\n" + notes
                    notices.append("Priority is stored in the task notes.")
                payload["notes"] = notes
            if normalized(title) in {"study", "review", "task", "project", "work"}:
                notices.append("Generic title. Specify the expected result.")
            if not item.get("subtasks") and item.get("estimatedMinutes", 0) >= 90 and parent is None:
                notices.append("Consider splitting this task into preparation and execution subtasks.")
            scheduled = payload.get("dueDay") or current.get("dueDay")
            deadline = payload.get("deadlineDay") or current.get("deadlineDay")
            if scheduled and deadline and scheduled > deadline:
                notices.append("The scheduled date is after the deadline.")
            if task_id:
                current = active.get(task_id)
                if current is None:
                    raise ValueError(f"Linked task is not active: {task_id}.")
                expected_parent = parent.task_id if parent else None
                different_parent = (current.get("parentId") or None) != expected_parent
                if action != "update" and (different_parent or (parent and not expected_parent)):
                    raise ValueError("The linked task belongs to a different hierarchy.")
                owner = journal.owner(task_id)
                if task_id in claimed or (action != "update" and owner and owner[0] != external):
                    raise ValueError("The taskId is already linked to another externalId.")
                if current.get("repeatCfgId") and project_id != current.get("projectId") and parent is None:
                    raise ValueError("Moving this recurring task would also move other occurrences.")
                claimed.add(task_id)
                if ("deadlineDay" in payload and payload["deadlineDay"] != current.get("deadlineDay")
                        and current.get("deadlineWithTime")):
                    notices.append("Setting a date replaces the timed deadline and its reminder.")
                if "dueDay" in payload and current.get("dueWithTime"):
                    if payload["dueDay"] == current.get("dueDay"):
                        payload.pop("dueDay")
                    elif action == "update":
                        payload["dueWithTime"] = None
                        notices.append("Rescheduling to a date clears the scheduled time and its reminder.")
                    else:
                        raise ValueError("The task already has a scheduled time. Adjust it in Super Productivity first.")
                before = {key: current.get(key) for key in payload if current.get(key) != payload[key]}
                payload = {key: payload[key] for key in before}
                kind = "Update" if payload else "No changes"
            else:
                key = (parent.external_id if parent else None, project_id, normalized(title))
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
                        reason = "Unconfirmed creation." if pending else "Possible duplicate."
                        ids = ", ".join(sorted(set(candidates)))
                        raise ValueError(f"{reason} Check these IDs: {ids}")
                if pending:
                    raise ValueError("A previous creation could not be confirmed. Find the task and provide its taskId.")
                new_titles.add(key)
                kind = "Create"
            operation = Operation(external, kind, task_id, parent.external_id if parent else None,
                                  title, payload, before, notices)
        except ValueError as error:
            operation = Operation(external, "Blocked", None, parent.external_id if parent else None,
                                  title, {}, {}, [str(error)])
        operations.append(operation)
        if operation.kind != "Blocked":
            for child in item.get("subtasks", []):
                add(child, operation, project_id)

    for item in document["tasks"]:
        add(item)
    deletions = [operation for operation in operations if operation.kind == "Delete"]
    for deletion in deletions:
        targets = deletion.affected.keys()
        if any(other is not deletion and (other.task_id in targets or other.parent == deletion.external_id
               or (other.affected and targets & other.affected.keys())) for other in operations):
            deletion.kind = "Blocked"
            deletion.notices.append("This deletion overlaps another operation in the proposal.")
    return operations


def apply(api: Api, journal: Journal, document: dict, project: str, approved: list[Operation], *, delete=False) -> list[str]:
    if any(operation.kind == "Blocked" for operation in approved):
        raise ValueError("Resolve blocked operations and generate a new preview.")
    results = []
    ids = {}
    with journal.lock():
        if plan(document, api.snapshot(), journal, project) != approved:
            raise ValueError("Tasks or settings changed since the preview. Generate a new preview.")
        for operation in approved:
            if (operation.kind == "Delete") != delete:
                continue
            external = operation.external_id
            task_id = operation.task_id
            payload = operation.changes.copy()
            try:
                if operation.kind == "Delete":
                    api.task("DELETE", task_id)
                    snapshot = api.snapshot()
                    if any(task["id"] in operation.affected for task in snapshot.tasks + snapshot.archived):
                        raise ApiError("Deletion could not be verified.")
                elif operation.kind == "Create":
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
                    if not external.startswith(":"):
                        journal.record(external, task_id, "linked")
                    api.task("PATCH", task_id, payload | {"isIgnoreShortSyntax": True})
                elif not external.startswith(":"):
                    journal.record(external, task_id, "completed")
                if payload:
                    persisted = api.task("GET", task_id)
                    if not isinstance(persisted, dict) or persisted.get("id") != task_id:
                        raise ApiError("Verification returned an unexpected task ID.")
                    differences = [key for key, value in payload.items() if persisted.get(key) != value]
                    if differences:
                        raise ApiError("Verification found differences in " + ", ".join(differences))
                    if not external.startswith(":"):
                        journal.record(external, task_id, "completed")
                ids[external] = task_id
                results.append(f"{operation.kind} · {operation.title} · {task_id}")
            except (ApiError, sqlite3.Error) as error:
                results.append(f"Stopped at {operation.title} · {error}")
                results.append("Earlier changes were saved. Generate a new preview before resuming.")
                break
    return results
