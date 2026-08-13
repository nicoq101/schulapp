#!/usr/bin/env python3
"""
Schulapp - Backend

Eine kleine Flask-App, die:
  - den WebUntis-Stundenplan überwacht und bei Änderungen Push-Benachrichtigungen schickt
  - Hausaufgaben & Klausuren verwaltet (REST-API)
  - dreimal täglich (einstellbar) an offene Aufgaben erinnert
  - die PWA (index.html + Assets) ausliefert

Einrichtung: siehe README.md im selben Ordner.
"""

import os
import json
import sqlite3
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory, render_template
from apscheduler.schedulers.background import BackgroundScheduler
from pywebpush import webpush, WebPushException
import webuntis

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "schulapp.db"
TZ = ZoneInfo("Europe/Berlin")

# ==================== Konfiguration (über Umgebungsvariablen) ====================

UNTIS_SCHOOL = os.environ.get("UNTIS_SCHOOL", "csgb")
UNTIS_SERVER = os.environ.get("UNTIS_SERVER", "csgb.webuntis.com")
UNTIS_USERNAME = os.environ.get("UNTIS_USERNAME", "")
UNTIS_PASSWORD = os.environ.get("UNTIS_PASSWORD", "")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:test@example.com")

app = Flask(__name__, static_folder="static", template_folder="templates")

# ==================== Datenbank ====================


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            typ TEXT NOT NULL,              -- 'hausaufgabe' oder 'pruefung'
            fach TEXT NOT NULL,
            text TEXT NOT NULL,
            faellig TEXT,                   -- ISO-Datum oder NULL
            erledigt INTEGER NOT NULL DEFAULT 0,
            erstellt TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS timetable_snapshot (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titel TEXT NOT NULL,
            text TEXT NOT NULL,
            typ TEXT NOT NULL,
            gelesen INTEGER NOT NULL DEFAULT 0,
            erstellt TEXT NOT NULL
        );
        """
    )
    conn.commit()

    defaults = {
        "notify_stundenplan": "true",
        "notify_lernen": "true",
        "notify_pruefungen": "true",
        "reminder_times": json.dumps(["17:30", "19:00", "21:30"]),
        "theme": "system",
        "name": "Nico",
        "klasse": "",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ==================== WebUntis ====================


def untis_login():
    return webuntis.Session(
        server=UNTIS_SERVER,
        username=UNTIS_USERNAME,
        password=UNTIS_PASSWORD,
        school=UNTIS_SCHOOL,
        useragent="Schulapp/1.0",
    ).login()


def fetch_timetable_days(days_ahead=5):
    """Holt den Stundenplan, gruppiert nach Tag. Gibt [] zurück falls nicht verfügbar."""
    try:
        session = untis_login()
    except Exception as e:
        print(f"WebUntis-Login fehlgeschlagen: {e}")
        return []

    start = dt.date.today()
    end = start + dt.timedelta(days=days_ahead)

    try:
        table = session.my_timetable(start=start, end=end)
    except webuntis.errors.DateNotAllowed:
        session.logout()
        return []
    except Exception as e:
        print(f"Stundenplan-Abruf fehlgeschlagen: {e}")
        session.logout()
        return []

    result = []
    for p in table:
        result.append(
            {
                "date": p.start.date().isoformat(),
                "start": p.start.strftime("%H:%M"),
                "end": p.end.strftime("%H:%M"),
                "subject": ", ".join(s.name for s in p.subjects) or "?",
                "room": ", ".join(r.name for r in p.rooms) or "?",
                "teacher": ", ".join(t.surname for t in p.teachers) or "?",
                "code": p.code or "",
                "info": p.text or "",
            }
        )
    session.logout()
    return result


def fetch_exams(days_ahead=90):
    try:
        session = untis_login()
        exams = session.exams(start=dt.date.today(), end=dt.date.today() + dt.timedelta(days=days_ahead))
        session.logout()
    except Exception as e:
        print(f"Klausuren-Abruf fehlgeschlagen: {e}")
        return []

    result = []
    for e in exams:
        result.append(
            {
                "name": getattr(e, "name", None) or getattr(e, "subject", "Klausur"),
                "date": e.start.date().isoformat(),
                "time": e.start.strftime("%H:%M"),
            }
        )
    return result


def entry_key(entry):
    return f"{entry['date']}_{entry['start']}_{entry['subject']}"


def diff_timetable(old_entries, new_entries):
    old_map = {entry_key(e): e for e in old_entries}
    new_map = {entry_key(e): e for e in new_entries}

    added = [new_map[k] for k in new_map if k not in old_map]
    removed = [old_map[k] for k in old_map if k not in new_map]
    changed = [(old_map[k], new_map[k]) for k in new_map if k in old_map and old_map[k] != new_map[k]]
    return added, removed, changed


def describe_change(old, new):
    """Baut einen konkreten, lesbaren Satz für eine erkannte Änderung."""
    if new["code"] == "cancelled" and old["code"] != "cancelled":
        return f"{new['subject']} um {new['start']} Uhr fällt heute aus."
    if old["start"] != new["start"]:
        return f"{new['subject']} wurde von {old['start']} auf {new['start']} Uhr verschoben."
    if old["room"] != new["room"]:
        return f"{new['subject']} ist jetzt in Raum {new['room']} (statt {old['room']})."
    if old["teacher"] != new["teacher"]:
        return f"{new['subject']} wird jetzt von {new['teacher']} unterrichtet (statt {old['teacher']})."
    if old["code"] != new["code"] and new["code"] == "irregular":
        return f"{new['subject']} um {new['start']} Uhr ist eine Vertretung."
    return f"{new['subject']} um {new['start']} Uhr hat sich geändert."


# ==================== Push-Benachrichtigungen ====================


def send_push(title, body, tag="allgemein"):
    if not VAPID_PRIVATE_KEY:
        print(f"[Push nicht konfiguriert] {title}: {body}")
        return

    conn = get_db()
    subs = conn.execute("SELECT endpoint, data FROM subscriptions").fetchall()
    conn.close()

    payload = json.dumps({"title": title, "body": body, "tag": tag})

    for sub in subs:
        subscription_info = json.loads(sub["data"])
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
            )
        except WebPushException as e:
            print(f"Push fehlgeschlagen für {sub['endpoint'][:40]}...: {e}")
            if "410" in str(e) or "404" in str(e):
                conn = get_db()
                conn.execute("DELETE FROM subscriptions WHERE endpoint = ?", (sub["endpoint"],))
                conn.commit()
                conn.close()


def log_notification(titel, text, typ):
    conn = get_db()
    conn.execute(
        "INSERT INTO notifications (titel, text, typ, erstellt) VALUES (?, ?, ?, ?)",
        (titel, text, typ, dt.datetime.now(TZ).isoformat()),
    )
    conn.commit()
    conn.close()


# ==================== Scheduler-Jobs ====================


def job_check_timetable():
    if get_setting("notify_stundenplan") != "true":
        return

    new_entries = fetch_timetable_days()
    if not new_entries:
        return

    conn = get_db()
    row = conn.execute("SELECT data FROM timetable_snapshot WHERE key = 'current'").fetchone()
    old_entries = json.loads(row["data"]) if row else []

    if old_entries:
        added, removed, changed = diff_timetable(old_entries, new_entries)

        for old, new in changed:
            text = describe_change(old, new)
            title = "⚠️ Unterricht fällt aus" if new["code"] == "cancelled" else "🔔 Stundenplan geändert"
            send_push(title, text, tag="stundenplan")
            log_notification(title, text, "stundenplan")

        for e in added:
            text = f"Neu im Plan: {e['subject']} am {e['date']} um {e['start']} Uhr."
            send_push("🔔 Stundenplan geändert", text, tag="stundenplan")
            log_notification("🔔 Stundenplan geändert", text, "stundenplan")

    conn.execute(
        "INSERT INTO timetable_snapshot (key, data) VALUES ('current', ?) "
        "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
        (json.dumps(new_entries),),
    )
    conn.commit()
    conn.close()


def job_reminder():
    """Wird zu jeder in reminder_times eingetragenen Uhrzeit aufgerufen."""
    if get_setting("notify_lernen") != "true":
        return

    conn = get_db()
    open_tasks = conn.execute(
        "SELECT fach, faellig FROM tasks WHERE typ = 'hausaufgabe' AND erledigt = 0"
    ).fetchall()
    conn.close()

    if not open_tasks:
        title = "🌙 Kurzer Check"
        body = "Aktuell stehen keine offenen Hausaufgaben in der App. Trotzdem alles vorbereitet für morgen?"
    else:
        faecher = sorted({t["fach"] for t in open_tasks})
        anzahl = len(open_tasks)
        if anzahl == 1:
            title = "📚 Noch 1 Aufgabe offen"
        else:
            title = f"📚 Noch {anzahl} Aufgaben offen"
        body = f"Du hast noch {', '.join(faecher)} offen. Willst du jetzt kurz Zeit dafür einplanen?"

    send_push(title, body, tag="lernen")
    log_notification(title, body, "lernen")


def job_exam_countdown():
    """Läuft einmal täglich morgens - kündigt Klausuren in den nächsten Tagen an."""
    if get_setting("notify_pruefungen") != "true":
        return

    today = dt.date.today()

    # Automatisch von WebUntis (falls die Schule den Zugriff erlaubt)
    exams = [{"name": e["name"], "date": e["date"]} for e in fetch_exams()]

    # Manuell in der App eingetragene Klausuren
    conn = get_db()
    manual = conn.execute(
        "SELECT fach, text, faellig FROM tasks WHERE typ = 'pruefung' AND faellig IS NOT NULL AND erledigt = 0"
    ).fetchall()
    conn.close()
    exams += [{"name": f"{m['fach']}: {m['text']}", "date": m["faellig"]} for m in manual]

    for e in exams:
        exam_date = dt.date.fromisoformat(e["date"])
        days_left = (exam_date - today).days
        if days_left in (7, 3, 1):
            title = f"📅 {e['name']} in {days_left} Tag{'en' if days_left != 1 else ''}"
            body = f"Am {exam_date.strftime('%d.%m.')}."
            send_push(title, body, tag="pruefung")
            log_notification(title, body, "pruefung")


scheduler = BackgroundScheduler(timezone=TZ)


def setup_scheduler():
    scheduler.add_job(job_check_timetable, "interval", minutes=15, id="timetable_check")
    scheduler.add_job(job_exam_countdown, "cron", hour=7, minute=0, id="exam_countdown")

    times = json.loads(get_setting("reminder_times", "[]"))
    reschedule_reminders(times)

    scheduler.start()


def reschedule_reminders(times):
    for job in scheduler.get_jobs():
        if job.id.startswith("reminder_"):
            scheduler.remove_job(job.id)

    for i, t in enumerate(times):
        hour, minute = map(int, t.split(":"))
        scheduler.add_job(job_reminder, "cron", hour=hour, minute=minute, id=f"reminder_{i}")


# ==================== API-Routen ====================


@app.route("/api/test-push", methods=["POST"])
def api_test_push():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM subscriptions").fetchone()["c"]
    conn.close()

    if count == 0:
        return jsonify({"ok": False, "error": "Keine Push-Registrierung gefunden. Erst 'Push aktivieren' antippen und Berechtigung erlauben."})

    send_push("🔔 Testnachricht", "Wenn du das liest, funktioniert alles!", tag="test")
    log_notification("🔔 Testnachricht", "Wenn du das liest, funktioniert alles!", "test")
    return jsonify({"ok": True, "subscriptions": count})


@app.route("/api/debug/exams-raw")
def api_debug_exams_raw():
    """Diagnose-Route: zeigt genau, was beim Klausuren-Abruf passiert."""
    try:
        session = untis_login()
    except Exception as e:
        return jsonify({"step": "login", "error": str(e)})

    try:
        exams = session.exams(start=dt.date.today(), end=dt.date.today() + dt.timedelta(days=90))
        session.logout()
        return jsonify({"step": "ok", "count": len(exams), "raw": [str(e) for e in exams[:5]]})
    except Exception as e:
        session.logout()
        return jsonify({"step": "exams_call", "error": str(e)})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(app.static_folder, "js/sw.js", mimetype="application/javascript")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(app.static_folder, "manifest.json")


@app.route("/api/vapid-public-key")
def vapid_public_key():
    return jsonify({"key": VAPID_PUBLIC_KEY})


@app.route("/api/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO subscriptions (endpoint, data) VALUES (?, ?)",
        (sub["endpoint"], json.dumps(sub)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/timetable")
def api_timetable():
    return jsonify(fetch_timetable_days())


@app.route("/api/exams")
def api_exams():
    return jsonify(fetch_exams())


@app.route("/api/tasks", methods=["GET", "POST"])
def api_tasks():
    conn = get_db()
    if request.method == "POST":
        data = request.get_json()
        conn.execute(
            "INSERT INTO tasks (typ, fach, text, faellig, erstellt) VALUES (?, ?, ?, ?, ?)",
            (
                data.get("typ", "hausaufgabe"),
                data["fach"],
                data["text"],
                data.get("faellig"),
                dt.datetime.now(TZ).isoformat(),
            ),
        )
        conn.commit()

    rows = conn.execute("SELECT * FROM tasks WHERE erledigt = 0 ORDER BY faellig IS NULL, faellig").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks/<int:task_id>", methods=["PATCH", "DELETE"])
def api_task_detail(task_id):
    conn = get_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    else:
        data = request.get_json()
        if "erledigt" in data:
            conn.execute("UPDATE tasks SET erledigt = ? WHERE id = ?", (int(data["erledigt"]), task_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.get_json()
        for key, value in data.items():
            set_setting(key, json.dumps(value) if isinstance(value, (list, dict)) else str(value))
        if "reminder_times" in data:
            reschedule_reminders(data["reminder_times"])
        return jsonify({"ok": True})

    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    settings = {}
    for r in rows:
        try:
            settings[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            settings[r["key"]] = r["value"]
    return jsonify(settings)


@app.route("/api/notifications")
def api_notifications():
    conn = get_db()
    rows = conn.execute("SELECT * FROM notifications ORDER BY erstellt DESC LIMIT 50").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/notifications/<int:note_id>/read", methods=["POST"])
def api_notification_read(note_id):
    conn = get_db()
    conn.execute("UPDATE notifications SET gelesen = 1 WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


init_db()
setup_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
