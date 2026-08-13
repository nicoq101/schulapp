// ==================== Zustand ====================

let state = {
  timetable: [],
  exams: [],
  tasks: [],
  settings: {},
  notifications: [],
};

const WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

// Leichtes haptisches Feedback, wo das Gerät es unterstützt (Android/Desktop;
// iOS blockiert die Vibration API in Safari, daher übernimmt dort die
// visuelle Press-Animation aus dem CSS die "Haptik").
function haptic(ms = 8) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

// ==================== Hilfsfunktionen ====================

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function addDaysISO(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function fmtDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function weekdayName(iso) {
  const d = new Date(iso + "T00:00:00");
  return WEEKDAYS[(d.getDay() + 6) % 7];
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  return res.json();
}

// ==================== Navigation ====================

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    haptic(6);
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`screen-${tab.dataset.screen}`).classList.add("active");
  });
});

// ==================== Daten laden ====================

async function loadAll() {
  const [timetable, exams, tasks, settings, notifications] = await Promise.all([
    api("/api/timetable"),
    api("/api/exams"),
    api("/api/tasks"),
    api("/api/settings"),
    api("/api/notifications"),
  ]);
  state = { timetable, exams, tasks, settings, notifications };
  renderAll();
}

function renderAll() {
  applyTheme(state.settings.theme || "system");
  renderGreeting();
  renderDashboard();
  renderAufgaben();
  renderPlan();
  renderEinstellungen();
  renderNotifications();
}

// ==================== Begrüßung ====================

function renderGreeting() {
  const hour = new Date().getHours();
  const greeting = hour < 11 ? "Guten Morgen" : hour < 17 ? "Hallo" : "Guten Abend";
  const name = state.settings.name;
  document.getElementById("greeting").textContent = name ? `${greeting}, ${name}` : greeting;
  document.getElementById("today-label").textContent = `${weekdayName(todayISO())}, ${fmtDate(todayISO())}`;
}

// ==================== Dashboard ====================

function renderDashboard() {
  const today = todayISO();
  const now = new Date();
  const nowMinutes = now.getHours() * 60 + now.getMinutes();

  const todaysLessons = state.timetable
    .filter((p) => p.date === today)
    .sort((a, b) => a.start.localeCompare(b.start));

  // Rail
  const rail = document.getElementById("dashboard-rail");
  if (todaysLessons.length === 0) {
    rail.innerHTML = `<div class="empty-state"><div class="display">Heute keine Stunden 🎉</div>Genieß den Tag.</div>`;
  } else {
    rail.innerHTML = todaysLessons.map((p) => renderRailItem(p, nowMinutes)).join("");
  }

  // Tiles: nächste Stunde
  const upcoming = todaysLessons.find((p) => toMinutes(p.start) > nowMinutes && p.code !== "cancelled");
  document.getElementById("tile-next").textContent = upcoming ? upcoming.subject : "–";

  // Tiles: verbleibende Stunden
  const remaining = todaysLessons.filter((p) => toMinutes(p.end) > nowMinutes && p.code !== "cancelled").length;
  document.getElementById("tile-remaining").textContent = remaining;

  // Tiles: offene Aufgaben
  document.getElementById("tile-tasks").textContent = state.tasks.length;

  // Tiles: nächste Klausur (automatisch von WebUntis + manuell eingetragen)
  const manualExamsForTile = state.tasks
    .filter((t) => t.typ === "pruefung" && t.faellig)
    .map((t) => ({ date: t.faellig }));
  const nextExam = [...state.exams, ...manualExamsForTile].sort((a, b) => a.date.localeCompare(b.date))[0];
  if (nextExam) {
    const days = daysUntil(nextExam.date);
    document.getElementById("tile-exam").textContent = days === 0 ? "Heute" : `${days}d`;
  } else {
    document.getElementById("tile-exam").textContent = "–";
  }

  // Bald fällig
  const soon = state.tasks
    .filter((t) => t.faellig && t.faellig <= addDaysISO(2))
    .sort((a, b) => (a.faellig || "").localeCompare(b.faellig || ""));

  const dueBox = document.getElementById("dashboard-tasks");
  if (soon.length === 0) {
    dueBox.innerHTML = `<div class="empty-state">Nichts dringend Fälliges. 🙌</div>`;
  } else {
    dueBox.innerHTML = soon.map((t) => renderTaskRow(t)).join("");
    attachTaskHandlers(dueBox);
  }
}

function toMinutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function daysUntil(iso) {
  const today = new Date(todayISO() + "T00:00:00");
  const target = new Date(iso + "T00:00:00");
  return Math.round((target - today) / 86400000);
}

function renderRailItem(p, nowMinutes) {
  const isNow = nowMinutes >= toMinutes(p.start) && nowMinutes < toMinutes(p.end);
  const classes = ["rail-item"];
  if (isNow) classes.push("now");
  if (p.code === "cancelled") classes.push("cancelled");
  if (p.code === "irregular") classes.push("irregular");

  let badge = "";
  if (p.code === "cancelled") badge = `<span class="badge cancelled">Entfällt</span>`;
  else if (p.code === "irregular") badge = `<span class="badge irregular">Vertretung</span>`;

  return `
    <div class="${classes.join(" ")}">
      <div class="rail-time mono">${p.start} – ${p.end}</div>
      <div class="rail-subject">${p.subject}${badge}</div>
      <div class="rail-meta">Raum ${p.room} · ${p.teacher}</div>
    </div>`;
}

// ==================== Aufgaben ====================

function renderAufgaben() {
  const today = todayISO();
  const tomorrow = addDaysISO(1);
  const weekEnd = addDaysISO(7);

  const groups = { today: [], tomorrow: [], week: [], later: [] };

  for (const t of state.tasks) {
    if (!t.faellig) groups.later.push(t);
    else if (t.faellig <= today) groups.today.push(t);
    else if (t.faellig === tomorrow) groups.tomorrow.push(t);
    else if (t.faellig <= weekEnd) groups.week.push(t);
    else groups.later.push(t);
  }

  fillTaskGroup("tasks-today", groups.today);
  fillTaskGroup("tasks-tomorrow", groups.tomorrow);
  fillTaskGroup("tasks-week", groups.week);
  fillTaskGroup("tasks-later", groups.later);
}

function fillTaskGroup(id, tasks) {
  const el = document.getElementById(id);
  if (tasks.length === 0) {
    el.innerHTML = `<div class="empty-state">Nichts hier.</div>`;
    return;
  }
  el.innerHTML = tasks.map(renderTaskRow).join("");
  attachTaskHandlers(el);
}

function renderTaskRow(t) {
  let urgency = "later";
  if (t.faellig) {
    const d = daysUntil(t.faellig);
    if (d <= 0) urgency = "urgent";
    else if (d === 1) urgency = "soon";
    else if (d <= 7) urgency = "soon";
    else urgency = "later";
  }
  const emoji = t.typ === "pruefung" ? "📝" : "📚";
  const dueText = t.faellig ? fmtDate(t.faellig) : "kein Datum";

  return `
    <div class="task-row" data-id="${t.id}">
      <button class="check" data-action="done">✓</button>
      <div style="flex:1;">
        <div class="task-fach">${emoji} ${escapeHtml(t.fach)}</div>
        <div class="task-text">${escapeHtml(t.text)}</div>
        <div class="task-due ${urgency}">${dueText}</div>
      </div>
      <button class="icon-btn" data-action="delete" style="width:32px;height:32px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>`;
}

function attachTaskHandlers(container) {
  container.querySelectorAll('[data-action="done"]').forEach((btn) => {
    btn.addEventListener("click", async () => {
      haptic(12);
      const id = btn.closest(".task-row").dataset.id;
      await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ erledigt: 1 }) });
      state.tasks = state.tasks.filter((t) => String(t.id) !== id);
      renderAll();
    });
  });
  container.querySelectorAll('[data-action="delete"]').forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.closest(".task-row").dataset.id;
      await api(`/api/tasks/${id}`, { method: "DELETE" });
      state.tasks = state.tasks.filter((t) => String(t.id) !== id);
      renderAll();
    });
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ==================== Stundenplan (Woche) ====================

function renderPlan() {
  const byDay = {};
  for (const p of state.timetable) {
    (byDay[p.date] ||= []).push(p);
  }

  const days = Object.keys(byDay).sort().slice(0, 5);
  const container = document.getElementById("week-container");

  if (days.length === 0) {
    container.innerHTML = `<div class="empty-state">Kein Stundenplan verfügbar (evtl. Ferien).</div>`;
  } else {
    container.innerHTML = days
      .map((date) => {
        const lessons = byDay[date].sort((a, b) => a.start.localeCompare(b.start));
        return `
          <div class="day-block">
            <h3>${weekdayName(date)}, ${fmtDate(date)}</h3>
            <div class="rail">${lessons.map((p) => renderRailItem(p, -1)).join("")}</div>
          </div>`;
      })
      .join("");
  }

  const examList = document.getElementById("exam-list");
  const manualExams = state.tasks
    .filter((t) => t.typ === "pruefung")
    .map((t) => ({ name: `${t.fach}: ${t.text}`, date: t.faellig, time: "" }));
  const allExams = [...state.exams, ...manualExams].filter((e) => e.date);

  if (allExams.length === 0) {
    examList.innerHTML = `<div class="empty-state">Keine Klausuren eingetragen. Tipp: über den "+"-Button unten rechts eine hinzufügen.</div>`;
  } else {
    examList.innerHTML = [...allExams]
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((e) => {
        const days = daysUntil(e.date);
        const dayLabel = days === 0 ? "Heute" : days === 1 ? "Morgen" : days > 1 ? `in ${days} Tagen` : fmtDate(e.date);
        const timeText = e.time ? ` · ${e.time} Uhr` : "";
        return `
          <div class="task-row">
            <div style="flex:1;">
              <div class="task-fach">📝 ${escapeHtml(e.name)}</div>
              <div class="task-text">${fmtDate(e.date)}${timeText}</div>
              <div class="task-due soon">${dayLabel}</div>
            </div>
          </div>`;
      })
      .join("");
  }
}

// ==================== Einstellungen ====================

function applyTheme(theme) {
  if (theme === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
  document.querySelectorAll("#theme-segmented button").forEach((b) => {
    b.classList.toggle("active", b.dataset.theme === theme);
  });
}

document.getElementById("theme-segmented").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const theme = btn.dataset.theme;
  applyTheme(theme);
  state.settings.theme = theme;
  await api("/api/settings", { method: "POST", body: JSON.stringify({ theme }) });
});

function renderEinstellungen() {
  document.querySelectorAll(".switch[data-setting]").forEach((sw) => {
    const key = sw.dataset.setting;
    sw.classList.toggle("on", state.settings[key] === "true" || state.settings[key] === true);
  });

  document.getElementById("setting-name").value = state.settings.name || "";
  document.getElementById("setting-klasse").value = state.settings.klasse || "";

  renderTimeChips();
}

document.querySelectorAll(".switch[data-setting]").forEach((sw) => {
  sw.addEventListener("click", async () => {
    haptic(6);
    const key = sw.dataset.setting;
    const newVal = !sw.classList.contains("on");
    sw.classList.toggle("on", newVal);
    state.settings[key] = String(newVal);
    await api("/api/settings", { method: "POST", body: JSON.stringify({ [key]: String(newVal) }) });
  });
});

document.getElementById("save-profile-btn").addEventListener("click", async () => {
  const name = document.getElementById("setting-name").value.trim();
  const klasse = document.getElementById("setting-klasse").value.trim();
  await api("/api/settings", { method: "POST", body: JSON.stringify({ name, klasse }) });
  state.settings.name = name;
  state.settings.klasse = klasse;
  renderGreeting();
});

function renderTimeChips() {
  const times = state.settings.reminder_times || [];
  const el = document.getElementById("time-chips");
  el.innerHTML = times
    .map((t, i) => `<span class="time-chip">${t} <button data-i="${i}">✕</button></span>`)
    .join("");
  el.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const times = [...state.settings.reminder_times];
      times.splice(Number(btn.dataset.i), 1);
      state.settings.reminder_times = times;
      await api("/api/settings", { method: "POST", body: JSON.stringify({ reminder_times: times }) });
      renderTimeChips();
    });
  });
}

document.getElementById("add-time-btn").addEventListener("click", async () => {
  const input = document.getElementById("new-time-input");
  if (!input.value) return;
  const times = [...(state.settings.reminder_times || []), input.value].sort();
  state.settings.reminder_times = times;
  await api("/api/settings", { method: "POST", body: JSON.stringify({ reminder_times: times }) });
  input.value = "";
  renderTimeChips();
});

// ==================== Benachrichtigungs-Center ====================

function renderNotifications() {
  const hasUnread = state.notifications.some((n) => !n.gelesen);
  document.getElementById("notif-btn").classList.toggle("has-unread", hasUnread);

  const list = document.getElementById("notif-list");
  if (state.notifications.length === 0) {
    list.innerHTML = `<div class="empty-state">Noch keine Benachrichtigungen.</div>`;
    return;
  }
  list.innerHTML = state.notifications
    .map(
      (n) => `
      <div class="notif-item ${n.gelesen ? "" : "unread"}" data-id="${n.id}">
        <div class="title">${escapeHtml(n.titel)}</div>
        <div class="text">${escapeHtml(n.text)}</div>
        <div class="time">${new Date(n.erstellt).toLocaleString("de-DE")}</div>
      </div>`
    )
    .join("");

  list.querySelectorAll(".notif-item").forEach((item) => {
    item.addEventListener("click", async () => {
      if (item.classList.contains("unread")) {
        item.classList.remove("unread");
        await api(`/api/notifications/${item.dataset.id}/read`, { method: "POST" });
      }
    });
  });
}

document.getElementById("notif-btn").addEventListener("click", () => {
  document.getElementById("notif-backdrop").classList.add("open");
  document.getElementById("notif-sheet").classList.add("open");
});
document.getElementById("notif-backdrop").addEventListener("click", () => {
  document.getElementById("notif-backdrop").classList.remove("open");
  document.getElementById("notif-sheet").classList.remove("open");
});

// ==================== Neue Aufgabe / Klausur ====================

let newTaskTyp = "hausaufgabe";

document.getElementById("fab-add").addEventListener("click", () => {
  document.getElementById("add-backdrop").classList.add("open");
  document.getElementById("add-sheet").classList.add("open");
});
document.getElementById("add-backdrop").addEventListener("click", closeAddSheet);

function closeAddSheet() {
  document.getElementById("add-backdrop").classList.remove("open");
  document.getElementById("add-sheet").classList.remove("open");
}

document.querySelectorAll(".type-toggle button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".type-toggle button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    newTaskTyp = btn.dataset.typ;
  });
});

document.getElementById("save-task-btn").addEventListener("click", async () => {
  const fach = document.getElementById("new-fach").value.trim();
  const text = document.getElementById("new-text").value.trim();
  const faellig = document.getElementById("new-faellig").value || null;

  if (!fach || !text) return;

  await api("/api/tasks", {
    method: "POST",
    body: JSON.stringify({ typ: newTaskTyp, fach, text, faellig }),
  });

  document.getElementById("new-fach").value = "";
  document.getElementById("new-text").value = "";
  document.getElementById("new-faellig").value = "";
  closeAddSheet();
  await loadAll();
});

// ==================== Push-Benachrichtigungen ====================

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function setupPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    document.getElementById("push-status").textContent = "Push wird auf diesem Gerät nicht unterstützt.";
    return;
  }

  const reg = await navigator.serviceWorker.register("/sw.js");
  const existing = await reg.pushManager.getSubscription();
  updatePushToggle(!!existing);

  document.getElementById("push-toggle").addEventListener("click", async () => {
    const current = await reg.pushManager.getSubscription();
    if (current) {
      await current.unsubscribe();
      updatePushToggle(false);
      return;
    }

    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      document.getElementById("push-status").textContent = "Berechtigung wurde nicht erteilt.";
      return;
    }

    const { key } = await api("/api/vapid-public-key");
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    await api("/api/subscribe", { method: "POST", body: JSON.stringify(sub) });
    updatePushToggle(true);
  });
}

function updatePushToggle(active) {
  document.getElementById("push-toggle").classList.toggle("on", active);
  document.getElementById("push-status").textContent = active
    ? "Aktiviert – du bekommst Benachrichtigungen."
    : "Noch nicht aktiviert";
}

document.getElementById("test-push-btn").addEventListener("click", async () => {
  const btn = document.getElementById("test-push-btn");
  btn.textContent = "Sende …";
  const result = await api("/api/test-push", { method: "POST" });
  btn.textContent = result.ok
    ? "Gesendet! Kommt sie an?"
    : result.error || "Fehler";
  setTimeout(() => (btn.textContent = "Testnachricht senden"), 3000);
});

// ==================== Start ====================

loadAll();
setupPush();
setInterval(loadAll, 5 * 60 * 1000); // alle 5 Minuten aktualisieren
