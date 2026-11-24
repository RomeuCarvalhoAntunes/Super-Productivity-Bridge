# Super Productivity Bridge

A local web app that applies task proposals from ChatGPT to the Super Productivity desktop app. It uses the desktop app's loopback REST API and stores creation links in SQLite so retries can reuse existing tasks.

## Setup

1. Enable the local REST API in Super Productivity under **Settings → Misc**. Keep the desktop app running.
2. On Ubuntu with Python 3.10 or later, create the token file if it does not already exist.

   ```sh
   install -m 600 .env.example .env
   ```

   Paste the Super Productivity REST token after `SP_TOKEN=` in `.env`. An environment variable of the same name takes precedence. The token stays on the local backend.
3. Run `python3 -m bridge` and open [the local web app](http://127.0.0.1:8765). Use `--port` to choose another port.

## Work with ChatGPT

- For new tasks, click **Download model** and attach the downloaded JSON in your conversation.
- To review existing tasks, click **Load open tasks**, select tasks, and click **Download selected tasks**. The export contains the tasks' notes, IDs, and revisions. Attach only the tasks you want to share with your conversation.
- Ask ChatGPT for a proposal. Paste its JSON into **Task proposal** or select the returned JSON file. Use **Preview import** and **Confirm proposal** to review and apply it.
- For deletions, ask ChatGPT to include an explicit delete action, or select tasks and click **Preview selected deletions**. The bridge shows affected subtasks and requires a separate deletion confirmation after any creations and updates have succeeded.

> Use the attached Super Productivity Bridge model. Turn the decisions from this conversation into actionable tasks. For the selected existing tasks, improve the titles, fill missing fields, and reschedule overdue work while preserving existing deadlines. Return one JSON block ready to paste into the bridge.

## Proposal format

Use UTF-8 JSON up to 2 MB. A fenced Markdown JSON block also works. The downloadable model uses the current project and tag catalog.

```json
{
  "schemaVersion": "2.0",
  "source": "chatgpt",
  "tasks": [
    {
      "action": "create",
      "externalId": "review-notes",
      "title": "Review meeting notes",
      "project": "EXISTING_PROJECT_NAME",
      "tags": ["EXISTING_TAG_NAME"],
      "priority": "medium",
      "subtasks": [
        {
          "action": "create",
          "externalId": "summarize-decisions",
          "title": "Summarize decisions",
          "estimatedMinutes": 25
        }
      ]
    }
  ]
}
```

- `create` requires a title and a stable, unique `externalId`. Use an existing project name or ID, or select a default project in the bridge.
- `update` requires `taskId` and `revision` from the latest export. Include only fields to change. Use `subtasks` with nested `create` actions to add children to an existing top-level task. New nested tasks may omit `action` when they include a title and external ID.
- `delete` takes only `action`, `taskId`, and `revision`. Deleting a parent includes its subtasks. Recurring task deletions are blocked.
- Optional fields include `description`, `tags`, `estimatedMinutes`, `priority`, `scheduledDate`, and `deadlineDate`. Tag arrays replace the current set. Dates use local `YYYY-MM-DD`. Subtasks inherit their project and cannot have their own tags. The bridge stores `low`, `medium`, or `high` priority in the notes.
- Rescheduling an existing timed task to another day clears its scheduled time and reminder. The preview shows this change. Super Productivity may plan a task for today when setting a deadline of today. The bridge reports mismatches between requested fields and the saved task.

Version 1.0 imports remain supported. The bridge keeps their existing project default and task-link behavior.

## Reimports and recovery

Reuse creation IDs across retries. The link database is at `${XDG_STATE_HOME:-~/.local/state}/super-productivity-bridge/imports.sqlite3`. If a creation response is lost, verify the task in Super Productivity and use its `taskId` on the pending creation to reconcile it. If a batch stops partway, check the per-task results, remove any finished updates or deletions from the proposal, and re-export affected tasks before retrying.

## Development

Run `python3 -m unittest discover -s tests -v`. The web tests use loopback and a simulated Super Productivity API. See the [Super Productivity REST API documentation](https://super-productivity.com/docs/reference/api/) for desktop behavior.
