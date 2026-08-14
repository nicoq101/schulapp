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
    credentials: "same-origin",
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
    document.getElementById("fab-add").style.display = tab.dataset.screen === "tutor" ? "none" : "flex";
  });
});

// ==================== Daten laden ====================

async function loadAll() {
  const [timetable, exams, tasks, settings, notifications, grades, tutorHistory] = await Promise.all([
    api("/api/timetable"),
    api("/api/exams"),
    api("/api/tasks"),
    api("/api/settings"),
    api("/api/notifications"),
    api("/api/grades"),
    api("/api/tutor/history"),
  ]);
  state = { timetable, exams, tasks, settings, notifications, grades, tutorHistory };
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
  renderNoten(state.grades || []);
  populateTutorFaecher();
  renderTutorChat(state.tutorHistory || []);
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

document.getElementById("notenskala-segmented").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const skala = btn.dataset.skala;
  state.settings.notenskala = skala;
  applyNotenskala(skala);
  await api("/api/settings", { method: "POST", body: JSON.stringify({ notenskala: skala }) });
  renderNoten(state.grades || []);
});

function applyNotenskala(skala) {
  document.querySelectorAll("#notenskala-segmented button").forEach((b) => {
    b.classList.toggle("active", b.dataset.skala === skala);
  });
  const noteInput = document.getElementById("new-note");
  const label = document.getElementById("note-label");
  if (skala === "oberstufe") {
    noteInput.min = 0; noteInput.max = 15; noteInput.step = 1; noteInput.placeholder = "0–15";
    label.textContent = "Punkte (Notenpunkte)";
  } else {
    noteInput.min = 1; noteInput.max = 6; noteInput.step = 0.5; noteInput.placeholder = "1–6";
    label.textContent = "Note";
  }
}

function renderEinstellungen() {
  applyNotenskala(state.settings.notenskala || "unterstufe");
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
    switchTaskType(btn.dataset.typ);
  });
});

document.getElementById("save-task-btn").addEventListener("click", async () => {
  if (newTaskTyp === "note") {
    const fach = document.getElementById("new-fach").value.trim();
    const note = document.getElementById("new-note").value;
    const gewichtung = document.getElementById("new-gewichtung").value || 1;
    const art = document.getElementById("new-art").value.trim();
    if (!fach || !note) return;

    await api("/api/grades", { method: "POST", body: JSON.stringify({ fach, note, gewichtung, art }) });
    document.getElementById("new-note").value = "";
    document.getElementById("new-art").value = "";
  } else {
    const fach = document.getElementById("new-fach").value.trim();
    const text = document.getElementById("new-text").value.trim();
    const faellig = document.getElementById("new-faellig").value || null;
    if (!fach || !text) return;

    await api("/api/tasks", { method: "POST", body: JSON.stringify({ typ: newTaskTyp, fach, text, faellig }) });
    document.getElementById("new-text").value = "";
    document.getElementById("new-faellig").value = "";
  }

  document.getElementById("new-fach").value = "";
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

// ==================== Auth ====================

let authMode = "login";

document.querySelectorAll("#auth-mode button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#auth-mode button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    authMode = btn.dataset.mode;
    document.getElementById("register-fields").style.display = authMode === "register" ? "block" : "none";
    document.getElementById("auth-submit").textContent = authMode === "register" ? "Account erstellen" : "Anmelden";
  });
});

document.getElementById("untis-toggle").addEventListener("click", () => {
  const sw = document.getElementById("untis-toggle");
  const on = !sw.classList.contains("on");
  sw.classList.toggle("on", on);
  document.getElementById("untis-fields").style.display = on ? "block" : "none";
});

document.getElementById("auth-submit").addEventListener("click", async () => {
  const errorBox = document.getElementById("auth-error");
  errorBox.classList.remove("visible");

  const body = {
    username: document.getElementById("auth-username").value.trim(),
    password: document.getElementById("auth-password").value,
  };
  if (authMode === "register") {
    body.display_name = document.getElementById("auth-display-name").value.trim();
    if (document.getElementById("untis-toggle").classList.contains("on")) {
      body.untis_username = document.getElementById("auth-untis-username").value.trim();
      body.untis_password = document.getElementById("auth-untis-password").value;
    }
  }

  const res = await api(authMode === "register" ? "/api/register" : "/api/login", {
    method: "POST",
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    errorBox.textContent = res.error || "Etwas ist schiefgelaufen.";
    errorBox.classList.add("visible");
    return;
  }

  showApp();
  await loadAll();
  setupPush();
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  document.getElementById("app").classList.remove("visible");
  document.getElementById("auth-screen").classList.remove("hidden");
});

function showApp() {
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("app").classList.add("visible");
}

async function checkAuth() {
  const me = await api("/api/me");
  if (me.authenticated) {
    showApp();
    await loadAll();
    setupPush();
  }
}

// ==================== Noten-Tracker ====================

function switchTaskType(typ) {
  newTaskTyp = typ;
  const isNote = typ === "note";
  document.getElementById("field-fach").style.display = "block";
  document.getElementById("field-text").style.display = isNote ? "none" : "block";
  document.getElementById("field-faellig").style.display = isNote ? "none" : "block";
  document.getElementById("field-note").style.display = isNote ? "block" : "none";
  document.getElementById("field-gewichtung").style.display = isNote ? "block" : "none";
  document.getElementById("field-art").style.display = isNote ? "block" : "none";
  if (isNote) applyNotenskala(state.settings.notenskala || "unterstufe");
}

function renderNoten(grades) {
  const skala = state.settings.notenskala || "unterstufe";
  const unit = skala === "oberstufe" ? " NP" : "";
  const bySubject = {};
  for (const g of grades) (bySubject[g.fach] ||= []).push(g);

  let totalWeighted = 0, totalWeight = 0;
  for (const g of grades) {
    totalWeighted += g.note * g.gewichtung;
    totalWeight += g.gewichtung;
  }
  document.getElementById("tile-schnitt").textContent = totalWeight ? (totalWeighted / totalWeight).toFixed(skala === "oberstufe" ? 1 : 2) + unit : "–";
  document.getElementById("tile-anzahl-noten").textContent = grades.length;

  const bySubjectEl = document.getElementById("grades-by-subject");
  const subjects = Object.keys(bySubject).sort();
  if (subjects.length === 0) {
    bySubjectEl.innerHTML = `<div class="empty-state">Noch keine Noten erfasst.</div>`;
  } else {
    bySubjectEl.innerHTML = subjects
      .map((fach) => {
        const list = bySubject[fach];
        const w = list.reduce((s, g) => s + g.note * g.gewichtung, 0);
        const wSum = list.reduce((s, g) => s + g.gewichtung, 0);
        const schnitt = (w / wSum).toFixed(skala === "oberstufe" ? 1 : 2);
        return `<div class="card" style="margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
          <div><div class="task-fach">${escapeHtml(fach)}</div><div class="task-text">${list.length} Note${list.length !== 1 ? "n" : ""}</div></div>
          <div class="value mono" style="font-size:20px;">${schnitt}${unit}</div>
        </div>`;
      })
      .join("");
  }

  const listEl = document.getElementById("grades-list");
  if (grades.length === 0) {
    listEl.innerHTML = `<div class="empty-state">Über den "+"-Button eine Note hinzufügen.</div>`;
  } else {
    listEl.innerHTML = grades
      .map(
        (g) => `
        <div class="task-row" data-id="${g.id}">
          <div style="flex:1;">
            <div class="task-fach">${escapeHtml(g.fach)}${g.art ? " · " + escapeHtml(g.art) : ""}</div>
            <div class="task-text">${g.beschreibung ? escapeHtml(g.beschreibung) : fmtDate(g.datum)}</div>
            <div class="task-due later">Gewichtung ${g.gewichtung}x</div>
          </div>
          <div class="value mono" style="font-size:20px;">${g.note}${unit}</div>
          <button class="icon-btn" data-action="delete-grade" style="width:32px;height:32px;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>`
      )
      .join("");
    listEl.querySelectorAll('[data-action="delete-grade"]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.closest(".task-row").dataset.id;
        await api(`/api/grades/${id}`, { method: "DELETE" });
        await loadAll();
      });
    });
  }
}

// ==================== KI-Lernassistent ====================

let tutorLevel = "";
let tutorSending = false;

const TUTOR_LEVEL_LABELS = {
  hinweis: "💡 Hinweis",
  schritt: "🧩 Nächster Schritt",
  erklaerung: "📖 Erklärung",
  loesung: "✅ Lösung",
};

function populateTutorFaecher() {
  const select = document.getElementById("tutor-fach");
  const faecher = new Set();
  (state.tasks || []).forEach((t) => faecher.add(t.fach));
  (state.grades || []).forEach((g) => faecher.add(g.fach));
  const current = select.value;
  select.innerHTML =
    `<option value="">Allgemein</option>` +
    [...faecher].sort().map((f) => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("");
  select.value = current && faecher.has(current) ? current : "";
}

function renderTutorMessage(m) {
  const levelLabel = m.role === "assistant" ? TUTOR_LEVEL_LABELS[m.level] : null;
  return `<div class="tutor-msg ${m.role}${m.pending ? " pending" : ""}">
    ${levelLabel ? `<span class="tutor-level-tag">${levelLabel}</span>` : ""}
    ${escapeHtml(m.content)}
  </div>`;
}

function renderTutorChat(messages) {
  const box = document.getElementById("tutor-chat");
  if (!messages || messages.length === 0) {
    box.innerHTML = `<div class="empty-state"><div class="display">Frag mich etwas 👋</div>Ich helfe dir beim Verstehen, Üben und Wiederholen.</div>`;
    return;
  }
  box.innerHTML = messages.map(renderTutorMessage).join("");
  box.scrollTop = box.scrollHeight;
}

document.getElementById("tutor-level-segmented").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  haptic(6);
  const wasActive = btn.classList.contains("active");
  document.querySelectorAll("#tutor-level-segmented button").forEach((b) => b.classList.remove("active"));
  tutorLevel = wasActive ? "" : btn.dataset.level;
  if (tutorLevel) btn.classList.add("active");
});

async function sendTutorMessage(text) {
  const trimmed = (text || "").trim();
  if (!trimmed || tutorSending) return;
  tutorSending = true;

  const fach = document.getElementById("tutor-fach").value;
  state.tutorHistory = [
    ...(state.tutorHistory || []),
    { role: "user", content: trimmed, level: tutorLevel, fach },
    { role: "assistant", content: "…", pending: true },
  ];
  renderTutorChat(state.tutorHistory);

  const result = await api("/api/tutor/chat", {
    method: "POST",
    body: JSON.stringify({ message: trimmed, level: tutorLevel, fach }),
  });

  state.tutorHistory = state.tutorHistory.filter((m) => !m.pending);
  state.tutorHistory.push(
    result.ok
      ? { role: "assistant", content: result.reply, level: tutorLevel, fach }
      : { role: "assistant", content: result.error || "Da ist etwas schiefgelaufen.", level: "" }
  );
  renderTutorChat(state.tutorHistory);
  tutorSending = false;
}

document.getElementById("tutor-send").addEventListener("click", () => {
  const input = document.getElementById("tutor-input");
  sendTutorMessage(input.value);
  input.value = "";
});

document.getElementById("tutor-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("tutor-send").click();
  }
});

document.getElementById("tutor-suggestions").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  sendTutorMessage(btn.dataset.text);
});

document.getElementById("tutor-clear-btn").addEventListener("click", async () => {
  if (!confirm("Chatverlauf mit dem KI-Tutor wirklich löschen?")) return;
  await api("/api/tutor/history", { method: "DELETE" });
  state.tutorHistory = [];
  renderTutorChat([]);
});

// ==================== Start ====================

checkAuth();
setInterval(() => { if (document.getElementById("app").classList.contains("visible")) loadAll(); }, 5 * 60 * 1000);
