"""REST client for the Super Productivity desktop app."""
import http.client
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote



def configured_token() -> str:
    token = os.environ.get("SP_TOKEN", "").strip()
    if token:
        return token
    path = Path(__file__).resolve().parent.parent / ".env"
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                name, separator, value = line.partition("=")
                if separator and name.strip() == "SP_TOKEN":
                    return value.strip()
    except (OSError, UnicodeError):
        return ""
    return ""


class ApiError(RuntimeError):
    def __init__(self, message: str, rejected: bool = False):
        super().__init__(message)
        self.rejected = rejected


class Api:
    def __init__(self, token: str):
        self.token = token.strip()
        if not self.token or not self.token.isascii() or not self.token.isprintable():
            raise ValueError("Set SP_TOKEN in .env using the REST token from Settings → Misc.")

    def request(self, method: str, path: str, payload: dict | None = None):
        headers = {"Authorization": "Bearer " + self.token}
        connection = http.client.HTTPConnection("127.0.0.1", 3876, timeout=17)
        try:
            if payload is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, None if payload is None else json.dumps(payload), headers)
            response = connection.getresponse()
            envelope = json.load(response)
            if response.status == 401:
                raise ApiError("REST token rejected (HTTP 401).", rejected=True)
            if response.status >= 300 or envelope.get("ok") is not True:
                code = envelope.get("error", {}).get("code", "")
                code = code if isinstance(code, str) and code.isupper() and code.replace("_", "").isalnum() else ""
                if self.token in code:
                    code = ""
                raise ApiError(
                    f"API rejected {method} (HTTP {response.status}) {code}".strip(),
                    rejected=400 <= response.status < 500 and envelope.get("ok") is False,
                )
            return envelope["data"]
        except (OSError, http.client.HTTPException, ValueError, KeyError, AttributeError):
            raise ApiError("Connection failed or the REST response was invalid. Make sure Super Productivity is running.") from None
        finally:
            connection.close()

    def snapshot(self):
        health = self.request("GET", "/health")
        if not isinstance(health, dict) or health.get("rendererReady") is not True:
            raise ApiError("Super Productivity is still loading.")
        return Snapshot(
            self.list_records("/projects"),
            self.list_records("/tags"),
            self.list_records("/tasks?includeDone=true&source=active"),
            self.list_records("/tasks?includeDone=true&source=archived"),
        )

    def list_records(self, path: str) -> list[dict]:
        records = self.request("GET", path)
        if not isinstance(records, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str)
            or not isinstance(item.get("title"), str) for item in records
        ):
            raise ApiError("Unexpected response format for " + path.split("?")[0])
        return records

    def task(self, method: str, task_id: str, payload: dict | None = None):
        return self.request(method, "/tasks/" + quote(task_id, safe=""), payload)


@dataclass
class Snapshot:
    projects: list[dict]
    tags: list[dict]
    tasks: list[dict]
    archived: list[dict]
