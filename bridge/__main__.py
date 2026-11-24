"""Local importer window."""
import json
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from bridge.api import Api, ApiError, configured_token
from bridge.importer import apply, local_journal, plan
from bridge.model import parse, read_file


LABELS = {
    "title": "Title", "notes": "Description", "projectId": "Project",
    "tagIds": "Tags", "dueDay": "Scheduled date", "deadlineDay": "Deadline",
    "timeEstimate": "Estimate (min)",
}


class Window:
    def __init__(self, root):
        self.root = root
        root.title("Super Productivity Bridge")
        root.geometry("1020x720")
        root.minsize(720, 480)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        self.token = tk.StringVar(value=configured_token())
        self.status = tk.StringVar(value="Click Connect to query Super Productivity."
                                   if self.token.get() else "Enter your Super Productivity REST token.")
        self.approved = None
        self.projects = {}
        self.tags = {}
        self.queue = queue.SimpleQueue()

        header = ttk.Frame(root, padding=12)
        header.grid(sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="REST token").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.token_entry = ttk.Entry(header, textvariable=self.token, show="•")
        self.token_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.connect_button = ttk.Button(header, text="Connect", command=self.connect)
        self.connect_button.grid(row=0, column=2)
        ttk.Label(header, text="Default project").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.project = ttk.Combobox(header, state="readonly")
        self.project.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0))

        toolbar = ttk.Frame(root, padding=(12, 0, 12, 8))
        toolbar.grid(row=1, sticky="ew")
        self.open_button = ttk.Button(toolbar, text="Open JSON…", command=self.open_file)
        self.open_button.pack(side="left")
        self.preview_button = ttk.Button(toolbar, text="Preview import", command=self.preview)
        self.preview_button.pack(side="left", padx=8)
        self.apply_button = ttk.Button(toolbar, text="Confirm import…", command=self.confirm, state="disabled")
        self.apply_button.pack(side="right")
        tabs = ttk.Notebook(root)
        tabs.grid(row=2, sticky="nsew", padx=12)
        self.tabs = tabs
        self.editor = ScrolledText(tabs, wrap="none", undo=True)
        self.output = ScrolledText(tabs, wrap="word", state="disabled")
        tabs.add(self.editor, text="Edit JSON")
        tabs.add(self.output, text="Connection, preview, and results")
        ttk.Label(root, textvariable=self.status, padding=12, wraplength=690).grid(row=3, sticky="ew")
        self.editor.bind("<<Modified>>", self.edited)
        self.project.bind("<<ComboboxSelected>>", lambda _: self.invalidate())
        self.token.trace_add("write", lambda *_: self.invalidate())
        self.token_entry.focus_set()

    def edited(self, _):
        if self.editor.edit_modified():
            self.invalidate()
            self.editor.edit_modified(False)

    def invalidate(self):
        self.approved = None
        self.apply_button.config(state="disabled")

    def show(self, text):
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.config(state="disabled")
        self.tabs.select(self.output)

    def run(self, status, work, finish):
        self.status.set(status)
        controls = (self.open_button, self.preview_button, self.connect_button, self.apply_button,
                    self.editor, self.token_entry, self.project)
        for control in controls:
            control.config(state="disabled")

        def worker():
            try:
                self.queue.put((work(), None))
            except (ApiError, ValueError, OSError, sqlite3.Error, TypeError, KeyError) as error:
                self.queue.put((None, str(error)))

        def check():
            try:
                result, error = self.queue.get_nowait()
            except queue.Empty:
                self.root.after(75, check)
                return
            for control in controls:
                control.config(state="normal")
            self.project.config(state="readonly")
            self.apply_button.config(state="disabled")
            if error is not None:
                self.status.set("Operation stopped.")
                messagebox.showerror("Importer", error, parent=self.root)
            else:
                finish(result)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(75, check)

    def connect(self):
        self.invalidate()
        try:
            api = Api(self.token.get())
        except ValueError as error:
            messagebox.showerror("Connection", str(error), parent=self.root)
            return

        def finish(snapshot):
            self.projects = {f"{p['title']}  [{p['id']}]": p["id"]
                             for p in snapshot.projects if not p.get("isArchived")}
            self.tags = {tag["id"]: tag["title"] for tag in snapshot.tags}
            selected = self.project.get()
            self.project["values"] = list(self.projects)
            self.project.set(selected if selected in self.projects else "")
            lines = ["REST API connected", "", "Active projects", *self.projects, "", "Tags"]
            lines.extend(f"{title}  [{key}]" for key, title in self.tags.items() if key != "TODAY")
            lines.extend(["", "Active tasks and subtasks"])
            lines.extend(f"{task['title']}  [{task['id']}]" for task in snapshot.tasks)
            lines.extend(["", f"Archived tasks found: {len(snapshot.archived)}"])
            self.show("\n".join(lines))
            self.status.set("Connected. Open a JSON file and select a default project if needed.")

        self.run("Querying the API at 127.0.0.1…", api.snapshot, finish)

    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            raw = read_file(path)
        except (OSError, ValueError) as error:
            messagebox.showerror("File", str(error), parent=self.root)
            return
        self.invalidate()
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", raw)
        self.tabs.select(self.editor)
        self.status.set("Review the fields and preview the import.")

    def preview(self):
        self.invalidate()
        raw = self.editor.get("1.0", "end-1c")
        project = self.projects.get(self.project.get(), "")
        try:
            api = Api(self.token.get())
            document = parse(raw)
        except ValueError as error:
            messagebox.showerror("Preview", str(error), parent=self.root)
            return

        def work():
            journal = local_journal()
            try:
                return plan(document, api.snapshot(), journal, project)
            finally:
                journal.db.close()

        def finish(operations):
            lines = []
            for operation in operations:
                indent = "    " if operation.parent else ""
                lines.append(f"{indent}{operation.kind} · {operation.title} [{operation.external_id}]")
                if operation.task_id:
                    lines.append(f"{indent}  Super Productivity ID: {operation.task_id}")
                for key, value in operation.changes.items():
                    def display(item):
                        if key == "timeEstimate" and item is not None:
                            item = item / 60_000
                        return json.dumps(item, ensure_ascii=False)
                    before = f"{display(operation.before[key])} → " if key in operation.before else ""
                    lines.append(f"{indent}  {LABELS[key]}: {before}{display(value)}")
                lines.extend(f"{indent}  {notice}" for notice in operation.notices)
                lines.append("")
            self.show("\n".join(lines))
            if any(operation.kind == "Blocked" for operation in operations):
                self.status.set("Fix blocked items in the JSON and generate a new preview.")
                return
            self.approved = (api, document, project, operations, raw)
            self.apply_button.config(state="normal")
            self.status.set("Preview ready. Review the changes before confirming.")

        self.run("Comparing the JSON with existing tasks…", work, finish)

    def confirm(self):
        if self.approved is None:
            return
        api, document, project, operations, raw = self.approved
        if raw != self.editor.get("1.0", "end-1c"):
            self.invalidate()
            return
        creates = sum(item.kind == "Create" for item in operations)
        updates = sum(item.kind == "Update" for item in operations)
        if not messagebox.askyesno(
            "Confirm import",
            f"Create {creates} and update {updates} task(s), including subtasks?\n\n"
            "Before your first import, export a backup from Super Productivity settings.\n"
            "Confirm after reviewing the preview and checking that you have a backup.",
            parent=self.root,
        ):
            return
        self.invalidate()

        def work():
            journal = local_journal()
            try:
                return apply(api, journal, document, project, operations)
            finally:
                journal.db.close()

        def finish(results):
            self.show("\n".join(results))
            self.status.set("Import finished. Review the results.")

        self.run("Applying the approved changes…", work, finish)


def main():
    root = tk.Tk()
    Window(root)
    root.mainloop()


if __name__ == "__main__":
    main()
