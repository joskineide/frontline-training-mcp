"use strict";
// Platform stand-in UI (AI-generated, development only).
// Security rule for this file: text that people wrote (announcements, titles, names) is
// only ever inserted with textContent, never parsed as HTML. tests/test_standin.py checks it.

const $ = (id) => document.getElementById(id);
let viewer = "";

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-Dev-User": viewer, ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function row(cells) {
  const tr = document.createElement("tr");
  for (const cell of cells) {
    const td = document.createElement("td");
    if (cell instanceof Node) td.append(cell);
    else td.textContent = String(cell);
    tr.append(td);
  }
  return tr;
}

function statusBadge(item) {
  if (item.status === "completed") return element("span", "completed", "badge done");
  if (item.overdue) return element("span", "overdue", "badge late");
  return element("span", "open", "badge open");
}

function card(title, body, meta) {
  const li = element("li");
  li.append(element("h3", title), element("p", body), element("p", meta, "meta"));
  return li;
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

async function loadViewers() {
  const { users } = await api("/api/dev/users");
  const select = $("viewer");
  select.replaceChildren();
  const groups = new Map();
  for (const user of users) {
    if (!groups.has(user.tenant_id)) {
      const group = document.createElement("optgroup");
      group.label = user.tenant_name;
      groups.set(user.tenant_id, group);
      select.append(group);
    }
    const option = element("option", `${user.name} (${user.role})`);
    option.value = `${user.tenant_id}/${user.user_id}`;
    groups.get(user.tenant_id).append(option);
  }
  let remembered = "";
  try { remembered = localStorage.getItem("viewer") || ""; } catch { /* storage may be off */ }
  const values = users.map((user) => `${user.tenant_id}/${user.user_id}`);
  viewer = values.includes(remembered) ? remembered : values[0] || "";
  select.value = viewer;
}

async function render() {
  showError("");
  const me = await api("/api/me");
  $("who").textContent = `${me.name} · ${me.tenant_name}`;

  const { assignments } = await api("/api/me/assignments");
  $("assignments").replaceChildren(
    ...(assignments.length
      ? assignments.map((a) => row([a.title, a.due_date || "—", statusBadge(a)]))
      : [row(["Nothing assigned.", "", ""])])
  );

  const { announcements } = await api("/api/me/announcements");
  $("announcements").replaceChildren(
    ...(announcements.length
      ? announcements.map((a) =>
          card(a.title, a.body, `${a.author_name} · ${new Date(a.published_at).toLocaleString()}`))
      : [element("li", "No announcements yet.", "empty")])
  );

  const manager = me.role === "manager";
  $("team-section").hidden = !manager;
  $("drafts-section").hidden = !manager;
  if (!manager) return;

  const { workers } = await api("/api/me/team");
  $("team").replaceChildren(
    ...workers.map((w) => row([w.name, w.open, w.overdue, w.completed]))
  );

  const { drafts, team_size: teamSize } = await api("/api/me/drafts");
  $("drafts").replaceChildren(
    ...(drafts.length
      ? drafts.map((d) => {
          const item = card(d.title, d.body, `Draft · ${new Date(d.created_at).toLocaleString()}`);
          const button = element("button", `Publish to ${teamSize} people`);
          button.type = "button";
          button.addEventListener("click", () => publish(d, teamSize));
          item.append(button);
          return item;
        })
      : [element("li", "No drafts.", "empty")])
  );
}

async function publish(draft, teamSize) {
  // A deliberate human step: the assistant can only draft (design decision D12).
  if (!confirm(`Publish "${draft.title}" to ${teamSize} people? This can't be undone here.`)) return;
  try {
    await api(`/api/drafts/${encodeURIComponent(draft.draft_id)}/publish`, { method: "POST" });
    await render();
  } catch (error) {
    showError(error.message);
  }
}

async function start() {
  $("viewer").addEventListener("change", (event) => {
    viewer = event.target.value;
    try { localStorage.setItem("viewer", viewer); } catch { /* storage may be off */ }
    render().catch((error) => showError(error.message));
  });
  $("refresh").addEventListener("click", () => render().catch((error) => showError(error.message)));
  try {
    await loadViewers();
    await render();
  } catch (error) {
    showError(error.message);
  }
}

start();
