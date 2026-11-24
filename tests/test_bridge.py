import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.api import Api, ApiError, Snapshot
from bridge.importer import Journal, apply, plan
from bridge.model import fields, parse


def document(*items):
    return parse(json.dumps({"schemaVersion": "1.0", "source": "chatgpt", "tasks": items}))


class FakeApi:
    def __init__(self):
        self.data = Snapshot(
            [{"id": "p1", "title": "Studies"}, {"id": "p2", "title": "Projects"}],
            [{"id": "t1", "title": "Focus"}], [], [],
        )
        self.posts = 0
        self.failure = None

    def snapshot(self):
        return copy.deepcopy(self.data)

    def request(self, method, path, payload):
        assert (method, path) == ("POST", "/tasks")
        if self.failure == "refused":
            raise ApiError("Rejected", rejected=True)
        self.posts += 1
        task = {"id": str(self.posts), **payload}
        self.data.tasks.append(task)
        if self.failure == "timeout":
            raise ApiError("Timeout")
        return task

    def task(self, method, task_id, payload=None):
        task = next(task for task in self.data.tasks if task["id"] == task_id)
        if method == "PATCH":
            task.update(payload)
        return task.copy()


class ValidationTests(unittest.TestCase):
    def test_mapping(self):
        item = document(dict(externalId="x", title=" Study functions ", description="Reading",
                             scheduledDate="2026-11-25", deadlineDate="2026-11-26",
                             estimatedMinutes=30, priority="high"))["tasks"][0]
        self.assertEqual(fields(item), {
            "title": "Study functions", "notes": "Reading", "dueDay": "2026-11-25",
            "deadlineDay": "2026-11-26", "timeEstimate": 1800000,
        })

    def test_invalid_inputs(self):
        for extra in (
            {"estimatedMinutes": True}, {"scheduledDate": "2026-02-30"}, {"priority": 1},
            {"tags": "Focus"}, {"subtasks": [{"externalId": "x", "title": "Duplicate"}]},
            {"subtasks": [{"externalId": "y", "title": "Child", "tags": []}]},
            {"token": "exemplo"}, {"deadlineDate": "20261125"},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                document({"externalId": "x", "title": "Task", **extra})
        with self.assertRaises(ValueError):
            parse('{"source": "chatgpt", "source": "outro"}')


class ApiTests(unittest.TestCase):
    @patch("bridge.api.http.client.HTTPConnection")
    def test_snapshot_and_auth(self, client):
        connection = client.return_value
        responses = [dict(rendererReady=True), [], [], [], []]
        connection.getresponse.side_effect = [
            self.response(200, {"ok": True, "data": value}) for value in responses
        ]
        self.assertEqual(Api("local-token").snapshot(), Snapshot([], [], [], []))
        self.assertEqual([call.args[1] for call in connection.request.call_args_list], [
            "/health", "/projects", "/tags", "/tasks?includeDone=true&source=active",
            "/tasks?includeDone=true&source=archived",
        ])
        self.assertEqual(connection.request.call_args.args[3]["Authorization"], "Bearer local-token")

    @patch("bridge.api.http.client.HTTPConnection")
    def test_connection_and_auth_errors_hide_token(self, client):
        connection = client.return_value
        connection.request.side_effect = ConnectionRefusedError("local-token")
        with self.assertRaisesRegex(ApiError, "Connection failed") as result:
            Api("local-token").snapshot()
        self.assertNotIn("local-token", str(result.exception))
        connection.request.side_effect = None
        connection.getresponse.return_value = self.response(401, {"ok": False})
        with self.assertRaisesRegex(ApiError, "401"):
            Api("local-token").request("GET", "/projects")

    @staticmethod
    def response(status, body):
        stream = io.StringIO(json.dumps(body))
        stream.status = status
        return stream


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Journal(Path(temp.name))
        self.addCleanup(self.store.db.close)
        self.api = FakeApi()
        self.item = {"externalId": "x", "title": "Practice Kotlin", "tags": ["Focus"],
                     "subtasks": [{"externalId": "y", "title": "Solve exercises", "estimatedMinutes": 30}]}

    def preview(self, doc):
        return plan(doc, self.api.snapshot(), self.store, "p1")

    def execute(self, doc):
        return apply(self.api, self.store, doc, "p1", self.preview(doc))

    def test_import_and_repeat_then_update(self):
        doc = document(self.item)
        self.assertEqual([op.kind for op in self.preview(doc)], ["Create", "Create"])
        self.assertEqual(self.api.posts, 0)
        self.execute(doc)
        parent, child = self.api.data.tasks
        self.assertEqual(parent["tagIds"], ["t1"])
        self.assertEqual(child["parentId"], parent["id"])
        self.assertEqual(child["timeEstimate"], 1800000)
        self.assertEqual([op.kind for op in self.preview(doc)], ["No changes", "No changes"])
        self.execute(doc)
        self.execute(document({**self.item, "title": "Study Kotlin", "project": "p2"}))
        self.assertEqual(self.api.posts, 2)
        self.assertEqual(parent["id"], "1")
        self.assertEqual(parent["projectId"], "p2")
        self.assertEqual(parent["title"], "Study Kotlin")

    def test_duplicates_and_unknown_tags_block(self):
        self.api.data.tasks.append({"id": "existing", "projectId": "p1", "title": "practice  KOTLIN"})
        self.assertIn("existing", self.preview(document(self.item))[0].notices[0])
        doc = document({**self.item, "taskId": "existing", "tags": ["missing"]})
        self.assertEqual(self.preview(doc)[0].kind, "Blocked")
        self.assertEqual(self.api.posts, 0)

    def test_stale_preview_blocks_write(self):
        doc = document(self.item)
        preview = self.preview(doc)
        self.api.data.projects.clear()
        with self.assertRaisesRegex(ValueError, "changed since the preview"):
            apply(self.api, self.store, doc, "p1", preview)
        self.assertEqual(self.api.posts, 0)

    def test_timeout_requires_explicit_reconciliation(self):
        doc = document(self.item)
        self.api.failure = "timeout"
        self.assertIn("Timeout", self.execute(doc)[0])
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(self.preview(doc)[0].kind, "Blocked")
        self.api.failure = None
        self.execute(document({**self.item, "taskId": "1"}))
        self.assertEqual(self.api.posts, 2)

    def test_legacy_pending_creation_is_still_blocked(self):
        self.store.record("x", None, "pendente")
        migrated = Journal(Path(self.store.lock_path.parent))
        self.addCleanup(migrated.db.close)
        self.assertEqual(migrated.get("x"), (None, "pending"))
        preview = plan(document(self.item), self.api.snapshot(), migrated, "p1")
        self.assertEqual(preview[0].kind, "Blocked")

    def test_definitive_rejection_allows_retry(self):
        doc = document(self.item)
        self.api.failure = "refused"
        self.assertIn("Rejected", self.execute(doc)[0])
        self.api.failure = None
        self.execute(doc)
        self.assertEqual(self.api.posts, 2)

    def test_partial_child_failure_keeps_created_parent(self):
        doc = document(self.item)
        original = self.api.request

        def fail_child(method, path, payload):
            if payload.get("parentId"):
                raise ApiError("Rejected", rejected=True)
            return original(method, path, payload)

        with patch.object(self.api, "request", side_effect=fail_child):
            self.assertIn("Rejected", self.execute(doc)[1])
        self.assertEqual([op.kind for op in self.preview(doc)], ["No changes", "Create"])
        self.execute(doc)
        self.assertEqual(self.api.posts, 2)
