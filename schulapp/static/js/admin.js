const REFRESH_MS = 10000;
let statusCache = {}; // id -> letzter geprüfter Status (bleibt beim Neurendern erhalten)

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadUsers() {
  const res = await fetch("/admin/users");
  if (res.status === 403) { location.reload(); return; }
  const users = await res.json();
  document.getElementById("user-count").textContent = users.length;

  const body = document.getElementById("users-body");
  if (users.length === 0) {
    body.innerHTML = `<tr class="empty-row"><td colspan="9">Noch keine Accounts.</td></tr>`;
    return;
  }
  body.innerHTML = users.map((u) => {
    const cached = statusCache[u.id];
    let badgeClass = "none";
    let statusText = "kein WebUntis";
    if (u.untis_username) {
      if (cached) {
        badgeClass = cached.ok ? "ok" : "fail";
        statusText = cached.ok ? "funktioniert" : (cached.error || "Fehler");
      } else {
        badgeClass = "neutral";
        statusText = "noch nicht geprüft";
      }
    }
    return `<tr id="row-${u.id}">
      <td class="mono">${escapeHtml(u.username)}</td><td>${escapeHtml(u.display_name)}</td><td class="mono">${escapeHtml(u.untis_username || "–")}</td>
      <td class="mono">${u.tasks}</td><td class="mono">${u.grades}</td><td class="mono">${u.push}</td>
      <td class="mono">${u.created_at.slice(0,10)}</td>
      <td><span class="badge ${badgeClass}" id="status-${u.id}">${escapeHtml(statusText)}</span></td>
      <td>
        <button data-action="check" data-id="${u.id}">WebUntis prüfen</button>
        <button data-action="delete" data-id="${u.id}" data-username="${escapeHtml(u.username)}">Löschen</button>
      </td>
    </tr>`;
  }).join("");
}

async function checkUser(id) {
  const el = document.getElementById("status-" + id);
  el.textContent = "prüfe …";
  el.className = "badge neutral";
  const res = await fetch(`/admin/check/${id}`);
  const data = await res.json();
  statusCache[id] = data;
  el.textContent = data.ok ? "funktioniert" : (data.error || "Fehler");
  el.className = "badge " + (data.ok ? "ok" : "fail");
}

async function deleteUser(id, name) {
  if (!confirm(`Account "${name}" wirklich komplett löschen?`)) return;
  await fetch(`/admin/delete/${id}`, { method: "POST" });
  delete statusCache[id];
  loadUsers();
}

async function loadLockouts() {
  const res = await fetch("/admin/lockouts");
  const data = await res.json();
  const body = document.getElementById("lockout-body");
  if (data.length === 0) {
    body.innerHTML = `<tr class="empty-row"><td colspan="5">Aktuell keine gesperrten IPs.</td></tr>`;
    return;
  }
  body.innerHTML = data.map((l) => `<tr>
    <td class="mono">${escapeHtml(l.ip)}</td><td>${escapeHtml(l.scope)}</td><td class="mono">${l.c}</td><td class="mono">${l.last.slice(0,16).replace("T"," ")}</td>
    <td><button data-action="unlock" data-ip="${escapeHtml(l.ip)}" data-scope="${escapeHtml(l.scope)}">Entsperren</button></td>
  </tr>`).join("");
}

async function unlockIp(ip, scope) {
  await fetch("/admin/unlock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip, scope }),
  });
  loadLockouts();
}

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/admin/logout", { method: "POST" });
  location.reload();
});

// Event-Delegation statt inline onclick="" (CSP-kompatibel: kein 'unsafe-inline' bei script-src nötig)
document.getElementById("users-body").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  if (btn.dataset.action === "check") checkUser(btn.dataset.id);
  if (btn.dataset.action === "delete") deleteUser(btn.dataset.id, btn.dataset.username);
});

document.getElementById("lockout-body").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action='unlock']");
  if (!btn) return;
  unlockIp(btn.dataset.ip, btn.dataset.scope);
});

loadUsers();
loadLockouts();
setInterval(loadUsers, REFRESH_MS);
setInterval(loadLockouts, REFRESH_MS);
