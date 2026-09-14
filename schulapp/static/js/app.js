// ==================== Zustand ====================

let state = {
  timetable: [],
  weekTimetable: [],
  exams: [],
  tasks: [],
  settings: {},
  notifications: [],
  grades: [],
  absences: [],
  materials: [],
  lessonNotes: [],
  studySessions: [],
};

let planMode = "tage"; // "tage" oder "woche"

const WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

function haptic(ms = 8) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

// ==================== Hilfsfunktionen ====================

function dateToLocalISO(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function todayISO() {
  return dateToLocalISO(new Date());
}

function addDaysISO(days) {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() + days);
  return dateToLocalISO(d);
}

function fmtDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function weekdayName(iso) {
  const d = new Date(iso + "T00:00:00");
  return WEEKDAYS[(d.getDay() + 6) % 7];
}

function mondayOfWeek(isoDate) {
  const d = new Date(isoDate + "T12:00:00");
  const day = (d.getDay() + 6) % 7; // 0 = Montag
  d.setDate(d.getDate() - day);
  return dateToLocalISO(d);
}

function addDaysToISO(iso, days) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + days);
  return dateToLocalISO(d);
}

function mondayForPlan() {
  const today = todayISO();
  const d = new Date(today + "T12:00:00");
  const monday = mondayOfWeek(today);
  // Am Wochenende direkt die kommende Schulwoche anzeigen.
  return (d.getDay() === 0 || d.getDay() === 6) ? addDaysToISO(monday, 7) : monday;
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const cacheKey = `schulapp-api:${path}`;
  try {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      ...options,
    });
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      const text = await res.text();
      return { ok: false, error: `Serverfehler ${res.status}: ${text.slice(0, 180)}` };
    }
    const data = await res.json();
    if (method === "GET" && res.ok) {
      try { localStorage.setItem(cacheKey, JSON.stringify({ ts: Date.now(), data })); } catch (_) {}
      setOfflineState(false);
    }
    return data;
  } catch (err) {
    if (method === "GET") {
      try {
        const cached = JSON.parse(localStorage.getItem(cacheKey) || "null");
        if (cached) { setOfflineState(true, cached.ts); return cached.data; }
      } catch (_) {}
    }
    return { ok: false, error: "Keine Verbindung zum Server." };
  }
}

function setOfflineState(isOffline, ts = null) {
  let pill = document.getElementById("offline-pill");
  if (!pill) {
    pill = document.createElement("div"); pill.id = "offline-pill"; pill.className = "offline-pill";
    const greeting = document.getElementById("greeting"); if (greeting) greeting.after(pill);
  }
  pill.classList.toggle("visible", !!isOffline);
  pill.textContent = isOffline ? `Offline · letzter Stand ${ts ? new Date(ts).toLocaleTimeString("de-DE", {hour:"2-digit",minute:"2-digit"}) : "gespeichert"}` : "";
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
  const [timetable, exams, tasks, settings, notifications, grades, absences, materials, lessonNotes, studySessions] = await Promise.all([
    api("/api/timetable"),
    api("/api/exams"),
    api("/api/tasks"),
    api("/api/settings"),
    api("/api/notifications"),
    api("/api/grades"),
    api("/api/absences"),
    api("/api/materials"),
    api("/api/lesson-notes"),
    api("/api/study-sessions"),
  ]);
  state = { ...state, timetable, exams, tasks, settings, notifications, grades, absences, materials, lessonNotes, studySessions };
  renderAll();
}

async function loadWeekTimetable() {
  const monday = mondayForPlan();
  const sunday = addDaysToISO(monday, 6);
  state.weekTimetable = await api(`/api/timetable?start=${monday}&end=${sunday}`);
  renderPlan();
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
  renderSmartFeatures();
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

  const rail = document.getElementById("dashboard-rail");
  if (todaysLessons.length === 0) {
    rail.innerHTML = `<div class="empty-state"><div class="display">Heute keine Stunden 🎉</div>Genieß den Tag.</div>`;
  } else {
    rail.innerHTML = todaysLessons.map((p) => renderRailItem(p, nowMinutes)).join("");
  }

  const upcoming = todaysLessons.find((p) => toMinutes(p.start) > nowMinutes && p.code !== "cancelled");
  document.getElementById("tile-next").textContent = upcoming ? upcoming.subject : "–";

  const remaining = todaysLessons.filter((p) => toMinutes(p.end) > nowMinutes && p.code !== "cancelled").length;
  document.getElementById("tile-remaining").textContent = remaining;

  document.getElementById("tile-tasks").textContent = state.tasks.length;

  const manualExamsForTile = state.tasks
    .filter((t) => t.typ === "pruefung" && t.faellig)
    .map((t) => ({ date: t.faellig }));
  const nextExam = [...state.exams, ...manualExamsForTile].sort((a, b) => a.date.localeCompare(b.date))[0];
  document.getElementById("tile-exam").textContent = nextExam
    ? (daysUntil(nextExam.date) === 0 ? "Heute" : `${daysUntil(nextExam.date)}d`)
    : "–";

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
  renderDashboardSmart(todaysLessons, nowMinutes);
  applyDashboardVisibility();
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

// ==================== Stundenplan (Tage / Ganze Woche) ====================

document.getElementById("plan-mode-segmented").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  haptic(6);
  planMode = btn.dataset.mode;
  document.querySelectorAll("#plan-mode-segmented button").forEach((b) => b.classList.toggle("active", b === btn));
  document.getElementById("week-summary-card").style.display = planMode === "woche" ? "flex" : "none";

  if (planMode === "woche" && state.weekTimetable.length === 0) {
    await loadWeekTimetable();
  } else {
    renderPlan();
  }
});

function renderPlan() {
  const source = planMode === "woche" ? state.weekTimetable : state.timetable;

  const byDay = {};
  for (const p of source) {
    (byDay[p.date] ||= []).push(p);
  }

  const container = document.getElementById("week-container");

  let dayKeys;
  if (planMode === "woche") {
    const monday = mondayForPlan();
    dayKeys = Array.from({ length: 7 }, (_, i) => addDaysToISO(monday, i));
  } else {
    dayKeys = Object.keys(byDay).sort().slice(0, 5);
  }

  if (planMode === "woche") {
    const allLessons = source.filter((p) => p.code !== "cancelled");
    const cancelled = source.filter((p) => p.code === "cancelled");
    document.getElementById("week-hours-count").textContent = allLessons.length;
    document.getElementById("week-cancelled-count").textContent = cancelled.length;
  }

  if (dayKeys.length === 0) {
    container.innerHTML = `<div class="empty-state">Kein Stundenplan verfügbar (evtl. Ferien).</div>`;
  } else {
    container.innerHTML = dayKeys
      .map((date) => {
        const lessons = (byDay[date] || []).sort((a, b) => a.start.localeCompare(b.start));
        if (lessons.length === 0) {
          return `
            <div class="day-block empty-day">
              <h3>${weekdayName(date)}, ${fmtDate(date)}</h3>
              <div class="day-empty-hint">Keine Stunden.</div>
            </div>`;
        }
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

  loadUntisSettings();
  renderTimeChips();
  const widgets = normalizedDashboardWidgets();
  document.querySelectorAll(".dashboard-widget-switch").forEach((sw) => sw.classList.toggle("on", widgets.includes(sw.dataset.widget)));
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


async function loadUntisSettings() {
  const data = await api("/api/untis");
  document.getElementById("setting-untis-username").value = data.username || "";
  document.getElementById("setting-untis-server").value = data.server || "";
  document.getElementById("setting-untis-school").value = data.school || "";
  document.getElementById("setting-untis-student-firstname").value = data.student_firstname || "";
  document.getElementById("setting-untis-student-surname").value = data.student_surname || "";
  const studentInfo = data.student_id ? ` Schüler-ID ${data.student_id} ist gespeichert.` : "";
  document.getElementById("untis-status").textContent = data.connected
    ? `Verbunden.${studentInfo} Mit 'Verbindung testen' kannst du Login + Stundenplan prüfen.`
    : "Noch kein WebUntis-Konto verbunden.";
}

document.getElementById("save-untis-btn").addEventListener("click", async () => {
  const status = document.getElementById("untis-status");
  status.textContent = "Prüfe Zugang …";
  const result = await api("/api/untis", {
    method: "POST",
    body: JSON.stringify({
      username: document.getElementById("setting-untis-username").value.trim(),
      password: document.getElementById("setting-untis-password").value,
      server: document.getElementById("setting-untis-server").value.trim(),
      school: document.getElementById("setting-untis-school").value.trim(),
      student_firstname: document.getElementById("setting-untis-student-firstname").value.trim(),
      student_surname: document.getElementById("setting-untis-student-surname").value.trim(),
    }),
  });
  if (!result.ok) {
    status.textContent = result.error || "WebUntis-Verbindung fehlgeschlagen.";
    return;
  }
  document.getElementById("setting-untis-password").value = "";
  status.textContent = "✅ WebUntis erfolgreich verbunden.";
  await loadAll();
});

document.getElementById("test-untis-btn").addEventListener("click", async () => {
  const status = document.getElementById("untis-status");
  const btn = document.getElementById("test-untis-btn");
  btn.disabled = true;
  status.textContent = "Prüfe Login + Schülerplan + Schuljahresgrenzen …";
  const result = await api("/api/untis/test", { method: "POST" });

  const login = result.login || {};
  const loginInfo = result.login_ok
    ? `personType=${login.personType ?? "?"}, personId=${login.personId ?? "?"}, klasseId=${login.klasseId ?? "?"}`
    : "";
  const attempts = (result.attempts || []).map((a) => {
    if (a.ok) return `• ${a.source}: ${a.count}${a.note ? ` (${a.note})` : ""}`;
    return `• ${a.source}: FEHLER ${a.error || "unbekannt"}`;
  }).join("\n");

  if (result.ok) {
    const segments = (result.schoolyear_segments || [])
      .map((s) => `${s.start}–${s.end} (${s.schoolyear})`)
      .join(", ");
    status.textContent = `✅ ${result.count} Stunden gefunden über ${result.source}. ${loginInfo}${segments ? `\nSchuljahre: ${segments}` : ""}`;
    state.weekTimetable = [];
    await loadAll();
    if (planMode === "woche") await loadWeekTimetable();
  } else {
    status.textContent = `❌ ${result.error || "Verbindung fehlgeschlagen."}${loginInfo ? `\n${loginInfo}` : ""}${attempts ? `\n${attempts}` : ""}`;
  }
  btn.disabled = false;
});

document.getElementById("disconnect-untis-btn").addEventListener("click", async () => {
  await api("/api/untis", { method: "DELETE" });
  document.getElementById("setting-untis-username").value = "";
  document.getElementById("setting-untis-password").value = "";
  document.getElementById("untis-status").textContent = "WebUntis wurde getrennt.";
  await loadAll();
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

// ==================== Neue Aufgabe / Klausur / Note ====================

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
  if (planMode === "woche") await loadWeekTimetable();
});


// ==================== Smart-Schulassistent ====================

function normalizedDashboardWidgets() {
  const v = state.settings.dashboard_widgets;
  if (Array.isArray(v)) return v;
  if (typeof v === "string") { try { const x = JSON.parse(v); if (Array.isArray(x)) return x; } catch (_) {} }
  return ["morning", "today", "tasks", "load"];
}

function applyDashboardVisibility() {
  const widgets = normalizedDashboardWidgets();
  const morning = document.getElementById("dashboard-morning-wrap");
  const load = document.getElementById("dashboard-load-wrap");
  const tasks = document.getElementById("dashboard-tasks")?.previousElementSibling;
  if (morning) morning.style.display = widgets.includes("morning") ? "block" : "none";
  if (load) load.style.display = widgets.includes("load") ? "block" : "none";
  const due = document.getElementById("dashboard-tasks");
  if (due) { due.style.display = widgets.includes("tasks") ? "block" : "none"; if (tasks) tasks.style.display = widgets.includes("tasks") ? "block" : "none"; }
}

document.querySelectorAll(".dashboard-widget-switch").forEach((sw) => {
  sw.addEventListener("click", async () => {
    const widget = sw.dataset.widget;
    const widgets = normalizedDashboardWidgets();
    const on = !widgets.includes(widget);
    const next = on ? [...widgets, widget] : widgets.filter((x) => x !== widget);
    state.settings.dashboard_widgets = next;
    sw.classList.toggle("on", on);
    await api("/api/settings", { method:"POST", body:JSON.stringify({ dashboard_widgets: next }) });
    applyDashboardVisibility();
  });
});

function uniqueSubjects() {
  const set = new Set();
  [...(state.timetable || []), ...(state.weekTimetable || [])].forEach((p) => { if (p.subject && p.subject !== "?") p.subject.split(",").forEach((x) => set.add(x.trim())); });
  state.tasks.forEach((x) => x.fach && set.add(x.fach.trim()));
  state.grades.forEach((x) => x.fach && set.add(x.fach.trim()));
  state.materials.forEach((x) => x.fach && set.add(x.fach.trim()));
  state.lessonNotes.forEach((x) => x.fach && set.add(x.fach.trim()));
  return [...set].filter(Boolean).sort((a,b)=>a.localeCompare(b,"de"));
}

function allExamsSmart() {
  const manual = state.tasks.filter((t) => t.typ === "pruefung" && t.faellig).map((t) => ({ name: `${t.fach}: ${t.text}`, fach:t.fach, date:t.faellig, manual:true }));
  const untis = (state.exams || []).map((e) => ({...e, fach:e.name || "Prüfung"}));
  const seen = new Set();
  return [...untis, ...manual].filter((e) => {
    const k = `${e.date}|${e.name}`; if (seen.has(k)) return false; seen.add(k); return e.date >= todayISO();
  }).sort((a,b)=>a.date.localeCompare(b.date));
}

function dayLoad(date) {
  const lessons = state.timetable.filter((p) => p.date === date && p.code !== "cancelled").length;
  const tasks = state.tasks.filter((t) => t.faellig === date && t.typ !== "pruefung").length;
  const exams = allExamsSmart().filter((e) => e.date === date).length;
  const study = state.studySessions.filter((x) => x.date === date && !x.done).length;
  const score = Math.min(10, Math.round((lessons * .65 + tasks * 1.7 + exams * 3.2 + study * .8) * 10) / 10);
  return {score, lessons, tasks, exams, study};
}

function renderDashboardSmart(todaysLessons, nowMinutes) {
  const brief = document.getElementById("morning-brief");
  const active = todaysLessons.filter((x)=>x.code !== "cancelled");
  let targetLessons = active, targetDate = todayISO(), lead = "Heute";
  if (!active.length) {
    const nextDate = [...new Set(state.timetable.filter((x)=>x.code!=="cancelled" && x.date>todayISO()).map((x)=>x.date))].sort()[0];
    if (nextDate) { targetDate = nextDate; targetLessons = state.timetable.filter((x)=>x.date===nextDate && x.code!=="cancelled").sort((a,b)=>a.start.localeCompare(b.start)); lead = `${weekdayName(nextDate)}, ${fmtDate(nextDate)}`; }
  }
  const due = state.tasks.filter((t)=>t.faellig===targetDate).length;
  const exam = allExamsSmart().find((e)=>e.date===targetDate);
  if (!targetLessons.length) brief.innerHTML = `<div class="smart-kicker">${lead}</div><div class="smart-title">Kein Unterricht gefunden</div><div class="sub">Offene Aufgaben: ${state.tasks.length}</div>`;
  else {
    const first=targetLessons[0], last=targetLessons[targetLessons.length-1];
    const changes=targetLessons.filter((x)=>x.code==="irregular" || x.code==="cancelled").length;
    brief.innerHTML = `<div class="smart-kicker">${lead}</div><div class="smart-title">${escapeHtml(first.subject)} startet um ${first.start}</div><div class="sub">${targetLessons.length} Stunden · Schluss ${last.end}${due?` · ${due} fällig`:""}${exam?` · Prüfung: ${escapeHtml(exam.name)}`:""}${changes?` · ${changes} Änderung(en)`:""}</div>`;
  }
  const load=dayLoad(todayISO());
  document.getElementById("today-load-score").textContent = load.score.toFixed(1);
  document.getElementById("today-load-label").textContent = load.score >= 7 ? "voller Tag" : load.score >= 4 ? "mittel" : "eher entspannt";
  const endEl=document.getElementById("school-end-countdown");
  if (active.length) {
    const end=toMinutes(active[active.length-1].end); const diff=end-nowMinutes;
    endEl.textContent = diff>0 ? `${Math.floor(diff/60)}:${String(diff%60).padStart(2,"0")}` : "Fertig";
  } else endEl.textContent="–";
}

function getNextSchoolDay() {
  const dates=[...new Set(state.timetable.filter((p)=>p.code!=="cancelled" && p.date>todayISO()).map((p)=>p.date))].sort();
  return dates[0] || addDaysISO(1);
}

function defaultMaterialsForSubject(subject) {
  const s=subject.toLowerCase(); const list=[];
  if (s.includes("sport")) list.push("Sportsachen");
  if (s.includes("kunst")) list.push("Kunstmaterial");
  if (s.includes("musik")) list.push("Musikmaterial / Instrument");
  return list;
}

function packItemsForDate(date) {
  const subjects=[...new Set(state.timetable.filter((p)=>p.date===date && p.code!=="cancelled").flatMap((p)=>p.subject.split(",").map((x)=>x.trim())))].filter(Boolean);
  const items=["Hausaufgaben geprüft"];
  for (const subject of subjects) {
    for (const m of state.materials.filter((x)=>x.fach.toLowerCase()===subject.toLowerCase()).map((x)=>x.item)) if (!items.includes(m)) items.push(m);
    for (const m of defaultMaterialsForSubject(subject)) if (!items.includes(m)) items.push(m);
  }
  return {subjects,items};
}

function renderPacklist() {
  const el=document.getElementById("packlist-card"); if(!el) return;
  const date=getNextSchoolDay(); const {subjects,items}=packItemsForDate(date);
  if(!subjects.length){el.innerHTML=`<div class="empty-state">Für die nächsten Tage wurde kein Unterricht gefunden.</div>`;return;}
  const key=`schulapp-pack:${date}`; let checked={}; try{checked=JSON.parse(localStorage.getItem(key)||"{}");}catch(_){ }
  el.innerHTML=`<div class="smart-kicker">${weekdayName(date)} · ${fmtDate(date)}</div><div class="chip-row">${subjects.map((s)=>`<span class="smart-chip">${escapeHtml(s)}</span>`).join("")}</div><div style="margin-top:10px;">${items.map((item,i)=>`<div class="pack-item"><button class="pack-check ${checked[item]?"done":""}" data-pack="${escapeHtml(item)}">✓</button><div>${escapeHtml(item)}</div></div>`).join("")}</div>`;
  el.querySelectorAll("[data-pack]").forEach((b)=>b.addEventListener("click",()=>{checked[b.dataset.pack]=!checked[b.dataset.pack];b.classList.toggle("done",checked[b.dataset.pack]);localStorage.setItem(key,JSON.stringify(checked));}));
}

function renderConflictAndLoad() {
  const conflict=document.getElementById("conflict-card"); const map=document.getElementById("load-map"); if(!conflict||!map)return;
  const due=[...state.tasks.filter((t)=>t.faellig).map((t)=>({date:t.faellig,label:`${t.fach}: ${t.text}`,kind:t.typ})),...allExamsSmart().map((e)=>({date:e.date,label:e.name,kind:"pruefung"}))];
  const byDate={}; due.forEach((x)=>(byDate[x.date]||=[]).push(x));
  const conflicts=Object.entries(byDate).filter(([,x])=>x.length>=2 && x.some((y)=>y.kind==="pruefung"));
  if(conflicts.length) conflict.innerHTML=`<div class="smart-kicker">⚠️ Konfliktwarner</div>${conflicts.slice(0,3).map(([d,x])=>`<div class="smart-list-item"><div class="title">${weekdayName(d)}, ${fmtDate(d)} · ${x.length} wichtige Dinge</div><div class="meta">${x.map((y)=>escapeHtml(y.label)).join(" · ")}</div></div>`).join("")}`;
  else conflict.innerHTML=`<div class="smart-kicker">✓ Konfliktwarner</div><div class="smart-title">Keine kritische Häufung</div><div class="sub">Aktuell liegen Prüfungen und Abgaben ausreichend verteilt.</div>`;
  map.innerHTML=Array.from({length:7},(_,i)=>{const d=addDaysISO(i);const l=dayLoad(d);return `<div class="load-day" title="${l.lessons} Stunden, ${l.tasks} Aufgaben, ${l.exams} Prüfungen"><div class="d">${weekdayName(d).slice(0,2)}</div><div class="n">${fmtDate(d).slice(0,2)}</div><div class="load-bar"><i style="height:${Math.max(4,l.score*10)}%"></i></div></div>`}).join("");
}

function renderFreePeriods() {
  const el=document.getElementById("free-periods-card"); if(!el)return;
  const date=todayISO(); const lessons=state.timetable.filter((p)=>p.date===date&&p.code!=="cancelled").sort((a,b)=>a.start.localeCompare(b.start)); const gaps=[];
  for(let i=0;i<lessons.length-1;i++){const mins=toMinutes(lessons[i+1].start)-toMinutes(lessons[i].end); if(mins>=45) gaps.push({start:lessons[i].end,end:lessons[i+1].start,mins});}
  if(!gaps.length){el.innerHTML=`<div class="empty-state">Heute keine längere Freistunde erkannt.</div>`;return;}
  const suggestion=chooseNextAction(25);
  el.innerHTML=gaps.map((g)=>`<div class="smart-list-item"><div class="title">${g.start}–${g.end} · ${g.mins} Minuten frei</div><div class="meta">${suggestion?`Gute Gelegenheit: ${escapeHtml(suggestion.title)}`:"Zeit für Pause oder Vorbereitung."}</div></div>`).join("");
}

function chooseNextAction(minutes=25) {
  const study=state.studySessions.filter((x)=>!x.done && x.date<=addDaysISO(3)).sort((a,b)=>a.date.localeCompare(b.date));
  const tasks=[...state.tasks].sort((a,b)=>(a.faellig||"9999").localeCompare(b.faellig||"9999"));
  const urgent=tasks.find((t)=>t.faellig && daysUntil(t.faellig)<=1);
  if(urgent) return {kind:"task",id:urgent.id,title:`${urgent.fach}: ${urgent.text}`,sub:urgent.faellig?`fällig ${fmtDate(urgent.faellig)}`:"offen"};
  const session=study.find((x)=>x.minutes<=minutes+15) || study[0];
  if(session) return {kind:"study",id:session.id,title:`${session.fach}: ${session.title}`,sub:`${session.minutes} Min. · ${weekdayName(session.date)} ${fmtDate(session.date)}`};
  const task=tasks[0]; if(task) return {kind:"task",id:task.id,title:`${task.fach}: ${task.text}`,sub:task.faellig?`fällig ${fmtDate(task.faellig)}`:"ohne Datum"};
  return null;
}

let activeNextAction=null;
function renderNextAction() {
  const selected=document.querySelector("#timebox-segmented button.active"); const mins=Number(selected?.dataset.minutes||state.settings.assistant_timebox||25);
  const action=chooseNextAction(mins); activeNextAction=action;
  const title=document.getElementById("next-action-title"), sub=document.getElementById("next-action-sub"), done=document.getElementById("next-action-done"); if(!title)return;
  if(!action){title.textContent="Alles Wichtige erledigt";sub.textContent="Nutze die Zeit für Pause, Wiederholung oder Vorbereitung.";done.style.display="none";}
  else {title.textContent=action.title;sub.textContent=`Für dein ${mins}-Minuten-Fenster · ${action.sub}`;done.style.display="block";}
}

document.querySelectorAll("#timebox-segmented button").forEach((b)=>b.addEventListener("click",async()=>{document.querySelectorAll("#timebox-segmented button").forEach((x)=>x.classList.toggle("active",x===b));state.settings.assistant_timebox=b.dataset.minutes;await api("/api/settings",{method:"POST",body:JSON.stringify({assistant_timebox:b.dataset.minutes})});renderNextAction();}));

document.getElementById("next-action-done")?.addEventListener("click",async()=>{if(!activeNextAction)return;if(activeNextAction.kind==="task")await api(`/api/tasks/${activeNextAction.id}`,{method:"PATCH",body:JSON.stringify({erledigt:true})});else await api(`/api/study-sessions/${activeNextAction.id}`,{method:"PATCH",body:JSON.stringify({done:true})});await loadAll();});

function renderDayPlan() {
  const el=document.getElementById("day-plan-card"); if(!el)return;
  const lessons=state.timetable.filter((x)=>x.date===todayISO()&&x.code!=="cancelled").sort((a,b)=>a.end.localeCompare(b.end)); let start=lessons.length?toMinutes(lessons[lessons.length-1].end)+30:Math.max(14*60,new Date().getHours()*60+new Date().getMinutes());
  const work=[]; const candidates=[];
  state.tasks.slice(0,5).forEach((t)=>candidates.push({title:`${t.fach}: ${t.text}`,mins:25}));
  state.studySessions.filter((x)=>!x.done&&x.date<=todayISO()).slice(0,4).forEach((x)=>candidates.push({title:`${x.fach}: ${x.title}`,mins:x.minutes}));
  candidates.slice(0,5).forEach((x,i)=>{if(i>0){start+=10;} const end=start+x.mins; work.push({start,end,...x}); start=end;});
  if(!work.length){el.innerHTML=`<div class="empty-state">Nach der Schule ist aktuell nichts eingeplant. 🎉</div>`;return;}
  const hm=(m)=>`${String(Math.floor(m/60)%24).padStart(2,"0")}:${String(m%60).padStart(2,"0")}`;
  el.innerHTML=work.map((x)=>`<div class="smart-list-item"><div class="title">${hm(x.start)}–${hm(x.end)} · ${escapeHtml(x.title)}</div><div class="meta">${x.mins} Minuten Fokus${x.mins>=40?" · danach kurze Pause":""}</div></div>`).join("");
}

function renderStudyPlanner() {
  const examEl=document.getElementById("study-planner-exams"), sessions=document.getElementById("study-sessions-card"); if(!examEl||!sessions)return;
  const exams=allExamsSmart().slice(0,5);
  examEl.innerHTML=exams.length?exams.map((e,i)=>`<div class="card" style="margin-bottom:10px;"><div class="subject-top"><div><div class="task-fach">${escapeHtml(e.fach||e.name)}</div><div class="task-text">${escapeHtml(e.name)}</div><div class="task-due soon">${fmtDate(e.date)} · in ${Math.max(0,daysUntil(e.date))} Tagen</div></div><button class="icon-btn" data-generate-plan="${i}" title="Lernplan erstellen">＋</button></div></div>`).join(""):`<div class="empty-state">Keine kommende Prüfung gefunden.</div>`;
  examEl.querySelectorAll("[data-generate-plan]").forEach((b)=>b.addEventListener("click",async()=>{const e=exams[Number(b.dataset.generatePlan)];await api("/api/study-plan/generate",{method:"POST",body:JSON.stringify({fach:e.fach||e.name,title:`Vorbereitung ${e.name}`,exam_date:e.date,minutes:30})});await loadAll();}));
  const open=state.studySessions.filter((x)=>!x.done).slice(0,12);
  sessions.innerHTML=open.length?open.map((x)=>`<div class="smart-list-item"><div class="title">${escapeHtml(x.fach)} · ${escapeHtml(x.title)}</div><div class="meta">${weekdayName(x.date)} ${fmtDate(x.date)} · ${x.minutes} Min.</div><button class="btn-primary study-done" data-study-done="${x.id}" style="margin-top:8px;padding:9px;">Einheit erledigt</button></div>`).join(""):`<div class="empty-state">Noch kein Lernplan aktiv.</div>`;
  sessions.querySelectorAll("[data-study-done]").forEach((b)=>b.addEventListener("click",async()=>{await api(`/api/study-sessions/${b.dataset.studyDone}`,{method:"PATCH",body:JSON.stringify({done:true})});await loadAll();}));
}

function renderAbsences() {
  const el=document.getElementById("absence-list"); if(!el)return;
  el.innerHTML=state.absences.length?state.absences.map((a)=>{const notes=state.lessonNotes.filter((n)=>n.date>=a.start_date&&n.date<=a.end_date);const tasks=state.tasks.filter((t)=>t.faellig&&t.faellig>=a.start_date&&t.faellig<=addDaysToISO(a.end_date,3));return `<div class="card ${a.caught_up?"":"alert-card"}" style="margin-top:10px;"><div class="subject-top"><div><div class="task-fach">${fmtDate(a.start_date)}${a.end_date!==a.start_date?`–${fmtDate(a.end_date)}`:""}</div><div class="task-text">${escapeHtml(a.note||"Fehlzeit")}</div></div><span class="smart-chip">${a.caught_up?"✓ aufgeholt":"offen"}</span></div><div class="sub" style="margin-top:8px;">${notes.length} Unterrichtsnotizen · ${tasks.length} relevante offene Aufgaben</div>${notes.slice(0,3).map((n)=>`<div class="smart-list-item"><div class="title">${escapeHtml(n.fach)}</div><div class="meta">${escapeHtml(n.text)}</div></div>`).join("")}<div class="smart-grid" style="margin-top:8px;"><button class="btn-primary" data-absence-toggle="${a.id}" data-current="${a.caught_up}">${a.caught_up?"Wieder öffnen":"Als aufgeholt markieren"}</button><button class="btn-primary" data-absence-delete="${a.id}">Löschen</button></div></div>`}).join(""):`<div class="empty-state">Keine Fehlzeiten eingetragen.</div>`;
  el.querySelectorAll("[data-absence-toggle]").forEach((b)=>b.addEventListener("click",async()=>{await api(`/api/absences/${b.dataset.absenceToggle}`,{method:"PATCH",body:JSON.stringify({caught_up:b.dataset.current!=="1"})});await loadAll();}));
  el.querySelectorAll("[data-absence-delete]").forEach((b)=>b.addEventListener("click",async()=>{await api(`/api/absences/${b.dataset.absenceDelete}`,{method:"DELETE"});await loadAll();}));
}

document.getElementById("add-absence-btn")?.addEventListener("click",async()=>{const start=document.getElementById("absence-start").value||todayISO();const end=document.getElementById("absence-end").value||start;const note=document.getElementById("absence-note").value.trim();await api("/api/absences",{method:"POST",body:JSON.stringify({start_date:start,end_date:end,note})});document.getElementById("absence-note").value="";await loadAll();});

document.getElementById("add-lesson-note-btn")?.addEventListener("click",async()=>{const fach=document.getElementById("lesson-note-subject").value.trim();const date=document.getElementById("lesson-note-date").value||todayISO();const text=document.getElementById("lesson-note-text").value.trim();if(!fach||!text)return;await api("/api/lesson-notes",{method:"POST",body:JSON.stringify({fach,date,text})});document.getElementById("lesson-note-text").value="";await loadAll();});

let activeSubject=null;
function renderSubjects() {
  const el=document.getElementById("subject-cards"); if(!el)return;
  const subjects=uniqueSubjects();
  el.innerHTML=subjects.length?subjects.map((fach)=>{const grades=state.grades.filter((g)=>g.fach.toLowerCase()===fach.toLowerCase());const tasks=state.tasks.filter((t)=>t.fach.toLowerCase()===fach.toLowerCase()).length;let avg="–";if(grades.length){const w=grades.reduce((s,g)=>s+g.note*g.gewichtung,0), ws=grades.reduce((s,g)=>s+g.gewichtung,0);avg=(w/ws).toFixed(state.settings.notenskala==="oberstufe"?1:2);}return `<div class="card subject-card" data-subject="${escapeHtml(fach)}"><div class="subject-top"><div><div class="task-fach">${escapeHtml(fach)}</div><div class="sub">${tasks} offene Aufgaben · ${grades.length} Noten</div></div><div class="subject-stat">Ø ${avg}</div></div></div>`}).join(""):`<div class="empty-state">Noch keine Fächer erkannt.</div>`;
  el.querySelectorAll("[data-subject]").forEach((card)=>card.addEventListener("click",()=>openSubject(card.dataset.subject)));
}

function openSubject(fach) {
  activeSubject=fach; const body=document.getElementById("subject-sheet-body"); document.getElementById("subject-sheet-title").textContent=fach;
  const eq=(x)=>(x||"").toLowerCase()===fach.toLowerCase(); const tasks=state.tasks.filter((t)=>eq(t.fach)); const grades=state.grades.filter((g)=>eq(g.fach)); const mats=state.materials.filter((m)=>eq(m.fach)); const notes=state.lessonNotes.filter((n)=>eq(n.fach)); const next=state.timetable.filter((p)=>p.date>=todayISO()&&p.subject.toLowerCase().includes(fach.toLowerCase())&&p.code!=="cancelled").sort((a,b)=>(a.date+a.start).localeCompare(b.date+b.start))[0];
  let avg="–";if(grades.length){const w=grades.reduce((s,g)=>s+g.note*g.gewichtung,0),ws=grades.reduce((s,g)=>s+g.gewichtung,0);avg=(w/ws).toFixed(state.settings.notenskala==="oberstufe"?1:2);}
  body.innerHTML=`<div class="smart-grid"><div class="card smart-mini"><div class="smart-kicker">Schnitt</div><div class="smart-big">${avg}</div></div><div class="card smart-mini"><div class="smart-kicker">Nächste Stunde</div><div class="smart-big" style="font-size:16px;">${next?`${weekdayName(next.date)} ${next.start}`:"–"}</div></div></div><div class="section-title">Offene Aufgaben</div>${tasks.length?tasks.map((t)=>`<div class="smart-list-item"><div class="title">${escapeHtml(t.text)}</div><div class="meta">${t.faellig?fmtDate(t.faellig):"kein Datum"}</div></div>`).join(""):`<div class="sub">Keine offenen Aufgaben.</div>`}<div class="section-title">Material</div>${mats.length?mats.map((m)=>`<div class="pack-item"><div style="flex:1">${escapeHtml(m.item)}</div><button class="icon-btn" data-delete-material="${m.id}" style="width:30px;height:30px;">×</button></div>`).join(""):`<div class="sub">Noch kein festes Material hinterlegt.</div>`}<div class="section-title">Unterrichtsnotizen</div>${notes.slice(0,8).map((n)=>`<div class="smart-list-item"><div class="title">${fmtDate(n.date)}</div><div class="meta">${escapeHtml(n.text)}</div></div>`).join("")||`<div class="sub">Noch keine Notizen.</div>`}`;
  body.querySelectorAll("[data-delete-material]").forEach((b)=>b.addEventListener("click",async()=>{await api(`/api/materials/${b.dataset.deleteMaterial}`,{method:"DELETE"});await loadAll();openSubject(fach);}));
  document.getElementById("subject-backdrop").classList.add("open");document.getElementById("subject-sheet").classList.add("open");
}
function closeSubject(){document.getElementById("subject-backdrop").classList.remove("open");document.getElementById("subject-sheet").classList.remove("open");}
document.getElementById("subject-backdrop")?.addEventListener("click",closeSubject);
document.getElementById("subject-material-add")?.addEventListener("click",async()=>{const item=document.getElementById("subject-material-input").value.trim();if(!item||!activeSubject)return;await api("/api/materials",{method:"POST",body:JSON.stringify({fach:activeSubject,item})});document.getElementById("subject-material-input").value="";await loadAll();openSubject(activeSubject);});

function renderGradeCalculatorOptions() {
  const sel=document.getElementById("grade-calc-subject");if(!sel)return;const current=sel.value;const subjects=[...new Set(state.grades.map((g)=>g.fach))].sort();sel.innerHTML=`<option value="">Fach wählen</option>`+subjects.map((s)=>`<option ${s===current?"selected":""}>${escapeHtml(s)}</option>`).join("");
  const skala=state.settings.notenskala||"unterstufe";const next=document.getElementById("grade-calc-next");if(next){next.min=skala==="oberstufe"?0:1;next.max=skala==="oberstufe"?15:6;next.step=skala==="oberstufe"?1:.5;}
}

document.getElementById("grade-calc-btn")?.addEventListener("click",()=>{const fach=document.getElementById("grade-calc-subject").value;const next=Number(document.getElementById("grade-calc-next").value),weight=Number(document.getElementById("grade-calc-weight").value||1),target=Number(document.getElementById("grade-calc-target").value);const out=document.getElementById("grade-calc-result");const grades=state.grades.filter((g)=>g.fach===fach);if(!fach||!grades.length||Number.isNaN(next)){out.textContent="Bitte Fach und nächste Note eintragen.";return;}const sum=grades.reduce((s,g)=>s+g.note*g.gewichtung,0),w=grades.reduce((s,g)=>s+g.gewichtung,0),after=(sum+next*weight)/(w+weight);let text=`Neuer Schnitt: ${after.toFixed(2)} (vorher ${(sum/w).toFixed(2)}).`;if(target){const needed=(target*(w+weight)-sum)/weight;const skala=state.settings.notenskala||"unterstufe";if(skala==="oberstufe") text+=` Für Ø ${target} wären rechnerisch ${needed.toFixed(1)} NP nötig.`;else text+=` Für Ø ${target} wäre rechnerisch Note ${needed.toFixed(2)} nötig.`;}out.textContent=text;});

function renderNotificationPriority() {
  // Vorhandenes Notification-Center bleibt die Quelle; Smart-Ansicht priorisiert nur visuell.
  const list=document.getElementById("notif-list"); if(!list)return;
  list.querySelectorAll(".notif-item").forEach((el)=>{const txt=el.textContent.toLowerCase();if(txt.includes("fällt aus")||txt.includes("prüfung")||txt.includes("verschoben")) el.classList.add("alert-card");});
}

function renderSmartFeatures() {
  const saved=String(state.settings.assistant_timebox||"25");document.querySelectorAll("#timebox-segmented button").forEach((b)=>b.classList.toggle("active",b.dataset.minutes===saved));
  const aStart=document.getElementById("absence-start"),aEnd=document.getElementById("absence-end"),nDate=document.getElementById("lesson-note-date");if(aStart&&!aStart.value)aStart.value=todayISO();if(aEnd&&!aEnd.value)aEnd.value=todayISO();if(nDate&&!nDate.value)nDate.value=todayISO();
  renderNextAction();renderPacklist();renderConflictAndLoad();renderFreePeriods();renderDayPlan();renderStudyPlanner();renderAbsences();renderSubjects();renderGradeCalculatorOptions();renderNotificationPriority();applyDashboardVisibility();
}

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
  btn.textContent = result.ok ? "Gesendet! Kommt sie an?" : result.error || "Fehler";
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
      body.untis_server = document.getElementById("auth-untis-server").value.trim();
      body.untis_school = document.getElementById("auth-untis-school").value.trim();
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

// ==================== Start ====================

checkAuth();
setInterval(() => { if (document.getElementById("app").classList.contains("visible")) loadAll(); }, 5 * 60 * 1000);
