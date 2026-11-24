"use strict";

const $ = (id) => document.getElementById(id);
const file = $("file");
const documentInput = $("document");
const project = $("project");
const previewButton = $("preview");
const confirmButton = $("confirm");
const confirmation = $("confirmation");
const results = $("results");
const status = $("status");
const projectNames = new Map();
const tagNames = new Map();
let previewId = null;
let busy = false;
let deleting = false;
let taskList = [];
const selectedTasks = new Set();
const filter = $("filter-project");

const labels = {
  title: "Title", notes: "Description", projectId: "Project",
  tagIds: "Tags", timeEstimate: "Estimate", dueDay: "Scheduled date",
  dueWithTime: "Scheduled time", deadlineDay: "Deadline",
};

function element(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function controls() {
  for (const control of [file, documentInput, project, $("reconnect")]) control.disabled = busy;
  for (const id of ["export-model", "load-tasks", "filter-project"]) $(id).disabled = busy;
  for (const id of ["export-tasks", "delete-tasks"]) $(id).disabled = busy || !selectedTasks.size;
  const visible = visibleTasks();
  const selectAll = $("select-all-tasks");
  selectAll.disabled = busy || !visible.length;
  selectAll.textContent = visible.length && visible.every((task) => selectedTasks.has(task.id))
    ? "Deselect all" : "Select all";
  $("selection-count").textContent = selectedTasks.size ? `${selectedTasks.size} selected` : "";
  for (const checkbox of $("tasks").querySelectorAll("input")) checkbox.disabled = busy;
  confirmButton.textContent = deleting ? "Confirm deletions" : "Confirm proposal";
  previewButton.disabled = busy || !documentInput.value.trim();
  confirmButton.disabled = busy || !previewId;
}

function invalidate(message) {
  previewId = null;
  deleting = false;
  results.replaceChildren();
  status.className = "";
  status.textContent = message;
  controls();
}

async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

async function connect() {
  const connection = $("connection");
  connection.className = "";
  connection.textContent = "Checking the local connection…";
  try {
    const {projects, tags} = await request("/api/catalog");
    tagNames.clear();
    for (const tag of tags) tagNames.set(tag.id, tag.title);
    const previous = project.value;
    projectNames.clear();
    project.replaceChildren(new Option("Use projects from the proposal", ""));
    for (const item of projects) {
      projectNames.set(item.id, item.title);
      project.add(new Option(item.title, item.id));
    }
    const previousFilter = filter.value;
    filter.replaceChildren(new Option("All projects", ""));
    for (const item of projects) filter.add(new Option(item.title, item.id));
    filter.value = projectNames.has(previousFilter) ? previousFilter : "";
    project.value = projectNames.has(previous) ? previous : "";
    connection.className = "connected";
    connection.textContent = "Super Productivity connected";
  } catch (error) {
    connection.className = "error";
    connection.textContent = error.message;
  }
}

function display(key, value) {
  if (value === null) return "Unset";
  if (key === "dueWithTime") return new Date(value).toLocaleString();
  if (key === "timeEstimate") return `${value / 60000} min`;
  if (key === "projectId") return projectNames.get(value) || value;
  if (key === "tagIds") return (value || []).map((id) => tagNames.get(id) || id).join(", ") || "None";
  return value === "" ? "Empty" : String(value);
}

function render(operation) {
  const task = element("article", "", operation.parent ? "task subtask" : "task");
  task.append(element("span", operation.kind, "action " + operation.kind.toLowerCase().replaceAll(" ", "-")));
  task.append(element("h4", operation.title));
  if (operation.task_id) task.append(element("p", `Task ID: ${operation.task_id}`));

  const changes = document.createElement("dl");
  for (const [key, value] of Object.entries(operation.changes)) {
    const next = display(key, value);
    const text = Object.hasOwn(operation.before, key)
      ? `${display(key, operation.before[key])} → ${next}` : next;
    changes.append(element("dt", labels[key] || key), element("dd", text));
  }
  if (changes.children.length) task.append(changes);

  for (const notice of operation.notices) task.append(element("p", notice));
  return task;
}

function renderGroups(operations) {
  const groups = [
    ["Blocked", "Needs attention"],
    ["Create", "New tasks"],
    ["Update", "Updates to existing tasks"],
    ["Delete", "Deletions"],
    ["No changes", "No changes"],
  ];
  return groups.flatMap(([kind, title]) => {
    const items = operations.filter((operation) => operation.kind === kind);
    if (!items.length) return [];
    const group = element("section", "", "operation-group");
    group.append(element("h3", title), ...items.map(render));
    return [group];
  });
}

async function run(action) {
  busy = true;
  controls();
  try {
    await action();
  } catch (error) {
    const message = error.message || "The request failed.";
    invalidate(message);
    status.className = "error";
  } finally {
    busy = false;
    controls();
  }
}

file.addEventListener("change", () => {
  invalidate("Reading the selected file…");
  documentInput.value = "";
  controls();
  const selected = file.files[0];
  if (!selected) {
    invalidate("Choose a JSON file to preview its tasks.");
    return;
  }
  run(async () => {
    if (selected.size > 2_000_000) throw new Error("The file exceeds 2 MB.");
    documentInput.value = await selected.text();
    invalidate("File loaded. Preview the import to review its tasks.");
  });
});

documentInput.addEventListener("input", () => invalidate("Preview the import to review your edits."));
project.addEventListener("change", () => invalidate("Preview the import to check the selected project."));
$("reconnect").addEventListener("click", () => run(connect));

function previewProposal() {
  invalidate("Preparing the preview…");
  return run(async () => {
    const data = await request("/api/preview", {document: documentInput.value, project: project.value});
    previewId = data.previewId;
    const count = (kind) => data.operations.filter((operation) => operation.kind === kind).length;
    const summary = `${count("Create")} to create · ${count("Update")} to update · ${count("Delete")} to delete`;
    const deletionOnly = count("Delete") > 0 && count("Delete") === data.operations.length;
    status.textContent = previewId ? summary : "Fix the blocked items and generate a new preview.";
    if (!data.operations.length) {
      previewId = null;
      status.textContent = "The proposal contains no changes.";
    }
    $("confirmation-summary").textContent = deletionOnly
      ? "Continue to the separate deletion confirmation." : summary;
    results.replaceChildren(...renderGroups(data.operations));
  });
}

function visibleTasks() {
  return taskList.filter((task) => !filter.value || task.projectId === filter.value);
}

function renderTaskPicker() {
  $("tasks").replaceChildren();
  const visible = visibleTasks();
  const tasksById = new Map(taskList.map((task) => [task.id, task]));
  const visibleIds = new Set(visible.map((task) => task.id));
  const children = new Map();
  const roots = [];
  for (const task of visible) {
    if (task.parentId && visibleIds.has(task.parentId)) {
      if (!children.has(task.parentId)) children.set(task.parentId, []);
      children.get(task.parentId).push(task);
    } else {
      roots.push(task);
    }
  }

  for (const task of roots.flatMap((root) => [root, ...(children.get(root.id) || [])])) {
    const label = element("label", "", task.parentId ? "task-choice child-choice" : "task-choice");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedTasks.has(task.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedTasks.add(task.id);
      else selectedTasks.delete(task.id);
      controls();
    });
    const title = element("span", task.title, "task-choice-title");
    if (task.parentId && !visibleIds.has(task.parentId)) {
      const parent = tasksById.get(task.parentId);
      if (parent) title.append(element("small", `Parent: ${parent.title}`, "task-choice-parent"));
    }
    label.append(checkbox, title, element("span", task.parentId ? "Subtask" : "Task", "task-choice-kind"));
    $("tasks").append(label);
  }
  $("export-status").textContent = taskList.length ? "" : "No open tasks found.";
  controls();
}

async function exportContext(taskIds) {
  const message = $(taskIds.length ? "export-status" : "model-status");
  try {
    const context = await request("/api/export", {taskIds});
    const url = URL.createObjectURL(new Blob([JSON.stringify(context, null, 2)], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = taskIds.length ? "task-review.json" : "task-model.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = "Attach the downloaded file in your ChatGPT conversation.";
  } catch (error) {
    message.textContent = error.message;
  }
}

previewButton.addEventListener("click", previewProposal);
filter.addEventListener("change", renderTaskPicker);
$("select-all-tasks").addEventListener("click", () => {
  const visible = visibleTasks();
  const allSelected = visible.every((task) => selectedTasks.has(task.id));
  for (const task of visible) {
    if (allSelected) selectedTasks.delete(task.id);
    else selectedTasks.add(task.id);
  }
  renderTaskPicker();
});
$("export-model").addEventListener("click", () => run(() => exportContext([])));
$("export-tasks").addEventListener("click", () => run(() => exportContext([...selectedTasks])));
$("load-tasks").addEventListener("click", () => run(async () => {
  try {
    ({tasks: taskList} = await request("/api/tasks"));
    selectedTasks.clear();
    $("task-picker").hidden = false;
    $("task-picker").open = true;
    renderTaskPicker();
  } catch (error) {
    $("export-status").textContent = error.message;
  }
}));
$("delete-tasks").addEventListener("click", () => {
  documentInput.value = JSON.stringify({
    schemaVersion: "2.0", source: "chatgpt",
    tasks: taskList.filter((task) => selectedTasks.has(task.id) && !selectedTasks.has(task.parentId))
      .map((task) => ({action: "delete", taskId: task.id, revision: task.revision})),
  }, null, 2);
  previewProposal();
});

confirmButton.addEventListener("click", () => {
  $("confirm-heading").textContent = deleting ? "Permanently delete tasks?" : "Confirm proposal";
  $("apply-changes").textContent = deleting ? "Delete tasks" : "Continue";
  $("apply-changes").classList.toggle("danger", deleting);
  confirmation.returnValue = "cancel";
  confirmation.showModal();
});
confirmation.addEventListener("close", () => {
  if (confirmation.returnValue !== "apply" || !previewId) return;
  const approvedId = previewId;
  const confirmDeletion = deleting;
  invalidate("Applying the approved changes…");
  run(async () => {
    const data = await request("/api/confirm", {previewId: approvedId, confirmDeletion});
    if (data.deletionId) {
      previewId = data.deletionId;
      deleting = true;
      status.textContent = "Review and confirm the remaining deletions.";
      $("confirmation-summary").textContent = `${data.deletions.length} selected task(s), including any subtasks listed below.`;
      results.replaceChildren(...data.results.map((line) => element("p", line)), ...renderGroups(data.deletions));
      status.focus();
      return;
    }
    status.textContent = data.complete ? "Proposal applied." : "Import stopped. Review the results below.";
    if (data.complete) {
      taskList = [];
      selectedTasks.clear();
      $("task-picker").hidden = true;
      $("tasks").replaceChildren();
      $("export-status").textContent = "";
    }
    status.className = data.complete ? "" : "error";
    results.replaceChildren(...data.results.map((line) => element("p", line)));
    status.focus();
  });
});

run(connect);
