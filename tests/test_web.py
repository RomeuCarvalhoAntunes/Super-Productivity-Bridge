import http.client
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from bridge.journal import Journal
from bridge.server import BridgeServer
from test_bridge import FakeApi


class WebTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        state = stack.enter_context(tempfile.TemporaryDirectory())
        self.api = FakeApi()
        stack.enter_context(patch("bridge.server.Api", return_value=self.api))
        stack.enter_context(patch("bridge.server.local_journal", side_effect=lambda: Journal(Path(state))))
        self.server = stack.enter_context(BridgeServer(0))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None, headers=None):
        connection = http.client.HTTPConnection(self.server.host, timeout=3)
        try:
            if payload is None:
                connection.request("GET", path, headers=headers or {})
            else:
                request_headers = {"Content-Type": "application/json", "Origin": self.server.origin}
                request_headers.update(headers or {})
                connection.request("POST", path, json.dumps(payload), request_headers)
            response = connection.getresponse()
            data = response.read()
            if response.headers.get_content_type() == "application/json":
                data = json.loads(data)
            return response.status, data
        finally:
            connection.close()

    def test_preview_confirm_and_replay(self):
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Preview import", page)
        document = {"schemaVersion": "1.0", "source": "chatgpt", "tasks": [
            {"externalId": "web-test", "title": "Review notes", "project": "Studies"}
        ]}
        status, preview = self.request("/api/preview", {"document": json.dumps(document)})
        self.assertEqual(status, 200)
        self.assertEqual(preview["operations"][0]["kind"], "Create")
        self.assertEqual(self.api.posts, 0)

        approval = {"previewId": preview["previewId"]}
        self.assertEqual(self.request("/api/confirm", approval, {"Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.api.posts, 0)
        status, result = self.request("/api/confirm", approval)
        self.assertEqual(status, 200)
        self.assertTrue(result["complete"])
        self.assertEqual(self.request("/api/confirm", approval)[0], 400)
        self.assertEqual(self.api.posts, 1)

    def test_export_update_and_replace_with_separate_deletion(self):
        self.api.data.tasks.extend([
            {"id": "update", "title": "write notes", "notes": "Keep this", "projectId": "p2",
             "dueWithTime": 1234567890, "dueDay": "2024-01-01"},
            {"id": "old", "title": "Old task", "projectId": "p1", "subTaskIds": ["child"]},
            {"id": "child", "title": "Old subtask", "projectId": "p1", "parentId": "old"},
        ])
        status, context = self.request("/api/export", {"taskIds": ["update", "old"]})
        self.assertEqual(status, 200)
        exported = {task["id"]: task for task in context["tasks"]}
        self.assertFalse(exported["child"]["selected"])
        proposal = context["responseExample"] | {"tasks": [
            {"action": "update", "taskId": "update", "revision": exported["update"]["revision"],
             "title": "Write meeting notes", "priority": "high", "scheduledDate": "2026-12-01",
             "estimatedMinutes": 20, "tags": ["Focus"]},
            {"action": "create", "externalId": "replacement", "title": "Replacement", "project": "p1"},
            {"action": "delete", "taskId": "old", "revision": exported["old"]["revision"]},
        ]}
        status, preview = self.request("/api/preview", {"document": "```json\n" + json.dumps(proposal) + "\n```"})
        self.assertEqual(status, 200)
        self.assertEqual([op["kind"] for op in preview["operations"]], ["Update", "Create", "Delete"])
        status, result = self.request("/api/confirm", {"previewId": preview["previewId"]})
        self.assertEqual(status, 200)
        updated = self.api.task("GET", "update")
        self.assertEqual(updated["projectId"], "p2")
        self.assertEqual(updated["notes"], "[Bridge priority: high]\nKeep this")
        self.assertIsNone(updated["dueWithTime"])
        self.assertEqual(updated["dueDay"], "2026-12-01")
        self.assertEqual(self.api.task("GET", "old")["title"], "Old task")
        self.assertIn("Old subtask", result["deletions"][0]["notices"][0])
        status, done = self.request("/api/confirm", {"previewId": result["deletionId"], "confirmDeletion": True})
        self.assertEqual(status, 200)
        self.assertTrue(done["complete"])
        self.assertEqual({task["id"] for task in self.api.data.tasks}, {"1", "update"})

    def test_changed_tasks_block_stale_updates_and_deletions(self):
        task = {"id": "active", "title": "Original", "projectId": "p1"}
        self.api.data.tasks.append(task)
        _, listed = self.request("/api/tasks")
        revision = listed["tasks"][0]["revision"]
        proposal = {"schemaVersion": "2.0", "source": "chatgpt", "tasks": [
            {"action": "delete", "taskId": "active", "revision": revision}
        ]}
        _, preview = self.request("/api/preview", {"document": json.dumps(proposal)})
        _, result = self.request("/api/confirm", {"previewId": preview["previewId"]})
        task["notes"] = "Changed after review"
        self.assertEqual(self.request("/api/confirm", {
            "previewId": result["deletionId"], "confirmDeletion": True,
        })[0], 400)
        proposal["tasks"][0].update(action="update", title="New title")
        _, stale = self.request("/api/preview", {"document": json.dumps(proposal)})
        self.assertIsNone(stale["previewId"])

    def test_rejects_unexpected_host_and_serves_only_known_paths(self):
        self.assertEqual(self.request("/", headers={"Host": "example.org"})[0], 403)
        self.assertEqual(self.request("/.env")[0], 404)
