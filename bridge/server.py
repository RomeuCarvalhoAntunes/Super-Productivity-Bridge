"""Loopback HTTP interface for the local importer."""
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from bridge.api import Api, ApiError, configured_token
from bridge.importer import apply, plan
from bridge.journal import local_journal
from bridge.model import MAX_BYTES, parse
from bridge.review import export_context, task_summary

ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/styles.css": ("styles.css", "text/css"),
}


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), Handler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.host}"
        self.preview = None
        self.lock = Lock()

    def catalog(self):
        api = Api(configured_token())
        projects = [
            {"id": item["id"], "title": item["title"]}
            for item in api.list_records("/projects")
            if not item.get("isArchived")
        ]
        tags = [{"id": item["id"], "title": item["title"]} for item in api.list_records("/tags")]
        return {"projects": projects, "tags": tags}

    def tasks(self):
        snapshot = Api(configured_token()).snapshot()
        return {"tasks": [task_summary(task) for task in snapshot.tasks if not task.get("isDone")]}

    def export(self, payload):
        selected = payload.get("taskIds")
        if not isinstance(selected, list) or any(not isinstance(task_id, str) for task_id in selected):
            raise ValueError("Select the tasks to export.")
        return export_context(Api(configured_token()).snapshot(), set(selected))

    def create_preview(self, payload):
        self.preview = None
        raw, project = payload.get("document"), payload.get("project", "")
        if not isinstance(raw, str) or not isinstance(project, str):
            raise ValueError("Provide a JSON document and a valid default project.")
        document = parse(raw)
        api = Api(configured_token())
        journal = local_journal()
        with closing(journal.db):
            operations = plan(document, api.snapshot(), journal, project)
        preview_id = None
        if all(operation.kind != "Blocked" for operation in operations):
            preview_id = secrets.token_urlsafe(24)
            self.preview = (preview_id, document, project, operations, False)
        return {"previewId": preview_id, "operations": [asdict(operation) for operation in operations]}

    def confirm(self, payload):
        preview = self.preview
        if not preview or payload.get("previewId") != preview[0]:
            raise ValueError("This preview has expired. Generate a new preview.")
        self.preview = None
        _, document, project, operations, deleting = preview
        if deleting and payload.get("confirmDeletion") is not True:
            raise ValueError("Confirm the deletion separately.")
        api = Api(configured_token())
        journal = local_journal()
        with closing(journal.db):
            results = apply(api, journal, document, project, operations, delete=deleting)
        deletes = [operation for operation in operations if operation.kind == "Delete"]
        expected = len(deletes) if deleting else len(operations) - len(deletes)
        complete = len(results) == expected and all(not line.startswith("Stopped at ") for line in results)
        deletion_id = None
        if complete and deletes and not deleting:
            deletion_id = secrets.token_urlsafe(24)
            deletion_document = document | {"tasks": [item for item in document["tasks"]
                                                       if item.get("action") == "delete"]}
            self.preview = (deletion_id, deletion_document, project, deletes, True)
        return {"results": results, "complete": complete and not deletion_id,
                "deletionId": deletion_id, "deletions": [asdict(operation) for operation in deletes]
                if deletion_id else []}



class Handler(BaseHTTPRequestHandler):
    timeout = 5

    def log_message(self, _format, *_args):
        pass

    def send_content(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_content(status, "application/json", json.dumps(value).encode("utf-8"))

    def do_GET(self):
        if self.headers.get_all("Host") != [self.server.host]:
            self.send_json(403, {"error": "Use the local address printed by the bridge."})
        elif self.path in ASSETS:
            name, kind = ASSETS[self.path]
            self.send_content(200, kind, (Path(__file__).parent / "web" / name).read_bytes())
        elif self.path == "/api/catalog":
            self.run_action(self.server.catalog)
        elif self.path == "/api/tasks":
            self.run_action(self.server.tasks)
        else:
            self.send_json(404, {"error": "Page not found."})

    def do_POST(self):
        if (self.headers.get_all("Host") != [self.server.host]
                or self.headers.get_all("Origin") != [self.server.origin]
                or self.headers.get_content_type() != "application/json"):
            self.send_json(403, {"error": "Requests must come from the local bridge page."})
            return
        actions = {"/api/preview": self.server.create_preview, "/api/confirm": self.server.confirm,
                   "/api/export": self.server.export}
        if self.path not in actions:
            self.send_json(404, {"error": "Page not found."})
            return
        self.run_action(lambda: self.mutate(actions[self.path]))

    def mutate(self, action):
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
            raise ValueError("Invalid request length.")
        length = int(lengths[0])
        if not 0 < length <= MAX_BYTES * 6 + 4096:
            raise ValueError("Invalid request length.")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        with self.server.lock:
            return action(payload)

    def run_action(self, action):
        try:
            value = action()
        except (ValueError, RecursionError) as error:
            self.send_json(400, {"error": str(error)})
        except ApiError as error:
            self.send_json(502, {"error": str(error)})
        except (OSError, sqlite3.Error):
            self.send_json(500, {"error": "Could not read the request or access local import state."})
        else:
            self.send_json(200, value)
