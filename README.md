# Super Productivity Bridge

A local Ubuntu desktop app for reviewing and importing tasks planned in ChatGPT into Super Productivity. The app talks directly to the desktop application's REST API on loopback and stores import links in SQLite.

## Requirements

- Ubuntu with Python 3.10 or later and Tkinter (`python3-tk` on Ubuntu).
- The Super Productivity desktop app with its local REST API enabled under **Settings → Misc**. The bridge was verified with the Super Productivity 20.1.0 Snap.

## Get started

1. From the project directory, run `./install-launcher.sh` once. Then open **Super Productivity Bridge** from the Ubuntu application menu. To launch directly, run `python3 -m bridge`.
2. Paste your Super Productivity REST token into the masked field and click **Connect**. To prefill it on startup, set the `SP_TOKEN` environment variable or create a private `.env` file and paste the token after `SP_TOKEN=`.

   ```sh
   install -m 600 .env.example .env
   ```

   Run this command only when creating the file for the first time. The environment variable takes precedence over the file. The Git ignore rules exclude `.env`.
3. Generate a JSON document using the prompt below. Open it in the bridge and choose a default project for tasks that omit `project`.
4. Click **Preview import** and review the proposed changes. Edit the JSON as needed and regenerate the preview.
5. Export a backup from Super Productivity before your first import. Use **Confirm import** to apply the reviewed changes and check the results.

## JSON format

```json
{
  "schemaVersion": "1.0",
  "source": "chatgpt",
  "tasks": [
    {
      "externalId": "review-notes",
      "title": "Review meeting notes"
    }
  ]
}
```

To generate an import file, ask ChatGPT:

> Turn this plan into a UTF-8 JSON file using the format above. Use short, actionable titles and unique IDs. Keep the IDs stable when revising the same plan. Include optional fields only where needed, use one level of subtasks, and omit credentials. Use project names, tags, and existing task IDs only when I provide them.

Use a UTF-8 JSON object with `schemaVersion: "1.0"`, `source: "chatgpt"`, and a non-empty `tasks` array. The file limit is 2 MB.

| Field | Format and behavior |
| --- | --- |
| `externalId` | Required stable ID, unique across all tasks and subtasks in a document. Start with an ASCII letter or digit. The remaining characters may also include hyphens, periods, and underscores. Maximum 128 characters. |
| `title` | Required text, stored literally. |
| `taskId` | Optional ID of an active Super Productivity task to link or update. |
| `description` | Optional notes. An empty string clears existing notes. |
| `project` | Active project name or ID. Uses the selected default project when omitted. |
| `tags` | Optional array of existing tag names or IDs. Replaces the task's tags. An empty array clears them. |
| `priority` | Optional manual suggestion displayed in the preview. Accepts `low`, `medium`, or `high`. |
| `estimatedMinutes` | Optional whole number of minutes from 0 to 1,000,000. |
| `scheduledDate` | Optional local calendar day in `YYYY-MM-DD` format. |
| `deadlineDate` | Optional deadline in `YYYY-MM-DD` format. |
| `subtasks` | Optional array of child tasks. Children inherit the parent's project and cannot define `project`, `tags`, or nested `subtasks`. |

An update preserves omitted optional fields except for `project`, which uses the selected default for top-level tasks. Super Productivity can schedule a task for today when its deadline is today. The bridge stops and reports differences if the saved fields differ from the approved changes. Change existing timed schedules in Super Productivity before setting a new calendar day. Recurring task moves are blocked because they would also move other occurrences.

## Reimports and recovery

Keep the same `externalId` when revising a task. The bridge stores the link to the Super Productivity task ID in `${XDG_STATE_HOME:-~/.local/state}/super-productivity-bridge/imports.sqlite3`. Back up that file to preserve import links. The preview also flags tasks with identical titles in the same project or under the same parent. To link an existing task after checking it, add its `taskId` to the JSON.

An import stops at the first failure and reports earlier changes. Generate a fresh preview to resume. If the response to a creation request is lost, the bridge marks that external ID as pending. Use **Connect** to look up the task and provide its `taskId` before retrying. If you cannot find it, verify in Super Productivity that it was never created before choosing a new `externalId`.

## Development

Run the tests with `python3 -m unittest discover -s tests -v`. The tests use a simulated API and cover validation, authentication errors, duplicate detection, reimports, stale previews, and partial failures. The real Snap was checked separately for authenticated reads, task and subtask creation, repeated imports, and updating a task without changing its ID.

The bridge uses the [local REST API](https://super-productivity.com/docs/reference/api/). The tested version exposes project, tag, and task reads, as well as individual task POST and PATCH requests. Priority is absent from its writable fields. Subtasks can be created under a top-level task. Reparenting an existing subtask is outside the API contract.
