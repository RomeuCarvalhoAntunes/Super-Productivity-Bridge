"""Export portable ChatGPT context and identify task revisions."""
import hashlib
import json
from datetime import datetime

TASK_FIELDS = (
    "id", "title", "notes", "projectId", "tagIds", "parentId", "subTaskIds",
    "isDone", "timeEstimate", "dueDay", "dueWithTime", "remindAt",
    "deadlineDay", "deadlineWithTime", "deadlineRemindAt", "repeatCfgId",
    "priority", "timeSpent", "timeSpentOnDay",
)


def revision(task):
    data = {key: task.get(key) for key in TASK_FIELDS}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def task_summary(task):
    return {key: task.get(key) for key in ("id", "title", "parentId", "projectId")} | {
        "revision": revision(task),
    }


def export_context(snapshot, selected):
    tasks = {task["id"]: task for task in snapshot.tasks}
    if any(task_id not in tasks or tasks[task_id].get("isDone") for task_id in selected):
        raise ValueError("Some selected tasks are no longer open. Refresh the task list.")
    export_ids = set(selected)
    for task_id in selected:
        task = tasks[task_id]
        export_ids.update(task.get("subTaskIds") or [])
        if task.get("parentId"):
            export_ids.add(task["parentId"])
    now = datetime.now().astimezone()
    return {
        "localDate": now.date().isoformat(),
        "timeZone": now.strftime("%Z %z"),
        "instructions": [
            "Use this conversation and context to return only one JSON block following responseExample.",
            "Use action create, update, or delete. Use unique, stable externalIds for new tasks and subtasks.",
            "For update and delete use the selected task's id as taskId and copy its revision. Never invent IDs.",
            "Related tasks are context only unless also selected. Delete only when explicitly requested.",
            "Send only changed fields on update. Fill missing fields based on context and use existing project and tag IDs.",
            "Use title, description, project, tags, estimatedMinutes, priority (low, medium, high), scheduledDate and deadlineDate.",
            "Existing tasks use projectId, tagIds, notes and timeEstimate in milliseconds. Due fields describe planned work.",
            "A priority line in notes already records a bridge priority. Preserve it unless asked to change it.",
            "Dates use YYYY-MM-DD. Replan overdue work on a future date and preserve deadlines unless explicitly asked.",
            "Use subtasks for new children of a new or existing parent. Children inherit the project and cannot have tags.",
            "A delete takes only action, taskId and revision. Use an empty tasks array when no changes are warranted.",
        ],
        "projects": [{key: item[key] for key in ("id", "title")} for item in snapshot.projects
                     if not item.get("isArchived")],
        "tags": [{key: item[key] for key in ("id", "title")} for item in snapshot.tags
                 if item["id"] != "TODAY"],
        "tasks": [
            {key: task[key] for key in TASK_FIELDS if key in task
             and key not in {"timeSpent", "timeSpentOnDay"}}
            | {"revision": revision(task), "selected": task_id in selected}
            for task_id, task in tasks.items() if task_id in export_ids
        ],
        "responseExample": {
            "schemaVersion": "2.0", "source": "chatgpt",
            "tasks": [{"action": "create", "externalId": "review-notes", "title": "Review meeting notes",
                       "estimatedMinutes": 20, "priority": "medium"}],
        },
    }
