# ChatGPT task planning prompt

Turn the plan from this conversation into a UTF-8 JSON file named `tasks.json` for Super Productivity Bridge. The root object must contain exactly `"schemaVersion": "1.0"`, `"source": "chatgpt"`, and a non-empty `tasks` array.

Give each task and subtask a unique `externalId` and a short, actionable `title`. Use IDs that start with an ASCII letter or digit and contain only ASCII letters, digits, hyphens, periods, and underscores. Preserve IDs when revising an existing plan. Break substantial tasks into actionable subtasks, using a single level.

Add optional fields when appropriate:

- `description` for relevant context or completion criteria.
- `estimatedMinutes` for a whole number of minutes.
- `scheduledDate` for a planned day and `deadlineDate` for a deadline. Use `YYYY-MM-DD` in my local calendar, and include dates only if I provide them or request a schedule.
- `priority` with `low`, `medium`, or `high` if I request priorities. This appears as a manual suggestion in the preview.
- `project` for a project name or ID I provide. Omit it to use the default project selected in the bridge.
- `tags` for an array of existing tag names I provide.
- `taskId` only if I supply the ID of an existing Super Productivity task to update.
- `subtasks` for an array of child tasks. Children must omit `project`, `tags`, and `subtasks`.

Omit unused fields and credentials. Deliver the JSON as a file without comments or trailing commas.
