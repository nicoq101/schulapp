#!/usr/bin/env python3
"""
Schulapp - Backend (Multi-User)

Eine kleine Flask-App, die:
  - mehreren Nutzern eigene Accounts mit eigenem WebUntis-Zugang bietet
  - pro Nutzer den Stundenplan überwacht und bei Änderungen Push-Benachrichtigungen schickt
  - Hausaufgaben, Klausuren & Noten verwaltet (REST-API, pro Nutzer getrennt)
  - mehrmals täglich (einstellbar) an offene Aufgaben erinnert
  - die PWA (index.html + Assets) ausliefert

Einrichtung: siehe README.md im selben Ordner.
"""

import os
import json
import sqlite3
import datetime as dt
from pathlib import Path
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from pywebpush import webpush, WebPushException
from cryptography.fernet import Fernet
import pyotp
import webuntis
import requests

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "schulapp.db"
TZ = ZoneInfo("Europe/Berlin")

# ==================== Konfiguration (über Umgebungsvariablen) ====================

UNTIS_SCHOOL = os.environ.get("UNTIS_SCHOOL", "csgb")
UNTIS_SERVER = os.environ.get("UNTIS_SERVER", "csgb.webuntis.com")

# Nur für die automatische Migration deines bisherigen Einzel-Accounts (siehe migrate_legacy_user)
LEGACY_UNTIS_USERNAME = os.environ.get("UNTIS_USERNAME", "")
LEGACY_UNTIS_PASSWORD = os.environ.get("UNTIS_PASSWORD", "")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:test@example.com")

SECRET_KEY = os.environ.get("SECRET_KEY", "bitte-in-render-setzen-dev-only")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_TOTP_SECRET = os.environ.get("ADMIN_TOTP_SECRET", "")
fernet = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None

# KI-Lernassistent
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TUTOR_MODEL = os.environ.get("TUTOR_MODEL", "claude-sonnet-5")  # z.B. "claude-haiku-4-5-20251001" für günstiger/schneller
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def try_untis_login(untis_username, untis_password):
    """Prüft WebUntis-Zugangsdaten sofort, ohne dauerhafte Session. Gibt (ok, fehlertext) zurück."""
    try:
        s = webuntis.Session(
            server=UNTIS_SERVER, username=untis_username, password=untis_password,
            school=UNTIS_SCHOOL, useragent="Schulapp/2.0",
        ).login()
        s.logout()
        return True, None
    except Exception as e:
        return False, str(e)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
)

# ==================== Datenbank ====================


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table, column):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            klasse TEXT,
            untis_username TEXT NOT NULL,
            untis_password_enc TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            typ TEXT NOT NULL,              -- 'hausaufgabe' oder 'pruefung'
            fach TEXT NOT NULL,
            text TEXT NOT NULL,
            faellig TEXT,
            erledigt INTEGER NOT NULL DEFAULT 0,
            erstellt TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS timetable_snapshot (
            user_id INTEGER PRIMARY KEY,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            titel TEXT NOT NULL,
            text TEXT NOT NULL,
            typ TEXT NOT NULL,
            gelesen INTEGER NOT NULL DEFAULT 0,
            erstellt TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fach TEXT NOT NULL,
            note REAL NOT NULL,
            gewichtung REAL NOT NULL DEFAULT 1,
            art TEXT,                        -- z.B. 'schriftlich', 'mündlich'
            beschreibung TEXT,
            datum TEXT,
            erstellt TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS failed_logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'user',
            attempt_time TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tutor_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,              -- 'user' oder 'assistant'
            content TEXT NOT NULL,
            level TEXT,                      -- 'hinweis' | 'schritt' | 'erklaerung' | 'loesung' | ''
            fach TEXT,
            erstellt TEXT NOT NULL
        );
        """
    )
    conn.commit()

    # --- Migration: falls eine ältere Version dieser App (ohne Login) schon
    # Daten angelegt hat, diese automatisch dem ersten Nutzer zuordnen. ---
    migrate_legacy_data(conn)

    conn.close()


def migrate_legacy_data(conn):
    """Ordnet Daten aus der Einzel-Nutzer-Version einem echten Account zu,
    damit beim Umstieg auf Accounts nichts verloren geht."""
    has_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    if has_users > 0:
        return  # schon migriert oder gar keine alten Daten

    has_old_tasks = conn.execute(
        "SELECT COUNT(*) c FROM tasks WHERE user_id IS NULL"
    ).fetchone()["c"] if column_exists(conn, "tasks", "user_id") else 0

    if not LEGACY_UNTIS_USERNAME:
        return  # nichts zu migrieren / kein Alt-Account bekannt

    enc_pw = fernet.encrypt(LEGACY_UNTIS_PASSWORD.encode()).decode() if fernet else LEGACY_UNTIS_PASSWORD
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, display_name, untis_username, untis_password_enc, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            LEGACY_UNTIS_USERNAME,
            generate_password_hash("bitte-aendern"),
            "Nico",
            LEGACY_UNTIS_USERNAME,
            enc_pw,
            dt.datetime.now(TZ).isoformat(),
        ),
    )
    legacy_user_id = cur.lastrowid

    for table in ("tasks", "settings", "subscriptions", "notifications", "timetable_snapshot"):
        conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (legacy_user_id,))

    conn.commit()
    print(
        f"Alt-Daten migriert zu Account '{LEGACY_UNTIS_USERNAME}'. "
        f"Vorläufiges Passwort: 'bitte-aendern' - bitte gleich nach dem ersten Login ändern!"
    )


# ==================== Auth-Hilfsfunktionen ====================


MAX_ATTEMPTS = 5
LOCKOUT_HOURS = 24


def get_client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_locked_out(ip, scope="user"):
    conn = get_db()
    cutoff = (dt.datetime.now(TZ) - dt.timedelta(hours=LOCKOUT_HOURS)).isoformat()
    count = conn.execute(
        "SELECT COUNT(*) c FROM failed_logins WHERE ip = ? AND scope = ? AND attempt_time > ?", (ip, scope, cutoff)
    ).fetchone()["c"]
    conn.close()
    return count >= MAX_ATTEMPTS


def record_failed_login(ip, scope="user"):
    conn = get_db()
    conn.execute(
        "INSERT INTO failed_logins (ip, scope, attempt_time) VALUES (?, ?, ?)",
        (ip, scope, dt.datetime.now(TZ).isoformat()),
    )
    conn.commit()
    conn.close()


def clear_failed_logins(ip, scope="user"):
    conn = get_db()
    conn.execute("DELETE FROM failed_logins WHERE ip = ? AND scope = ?", (ip, scope))
    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "not_authenticated"}), 401
        return f(*args, **kwargs)

    return wrapper


def current_user():
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    return dict(row) if row else None


def decrypt_untis_password(enc):
    if fernet is None:
        return enc
    return fernet.decrypt(enc.encode()).decode()


def get_setting(user_id, key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(user_id, key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
        (user_id, key, value),
    )
    conn.commit()
    conn.close()


DEFAULT_SETTINGS = {
    "notify_stundenplan": "true",
    "notify_lernen": "true",
    "notify_pruefungen": "true",
    "reminder_times": json.dumps(["17:30", "19:00", "21:30"]),
    "theme": "system",
    "klasse": "",
    "notenskala": "unterstufe",  # oder "oberstufe" (0-15 Notenpunkte)
}


def ensure_default_settings(user_id):
    conn = get_db()
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, ?, ?)", (user_id, k, v))
    conn.commit()
    conn.close()


# ==================== WebUntis ====================


def untis_login(user):
    return webuntis.Session(
        server=UNTIS_SERVER,
        username=user["untis_username"],
        password=decrypt_untis_password(user["untis_password_enc"]),
        school=UNTIS_SCHOOL,
        useragent="Schulapp/2.0",
    ).login()


def fetch_timetable_days(user, days_ahead=5):
    if not user.get("untis_username"):
        return []
    try:
        session_ = untis_login(user)
    except Exception as e:
        print(f"WebUntis-Login fehlgeschlagen ({user['username']}): {e}")
        return []

    start = dt.date.today()
    end = start + dt.timedelta(days=days_ahead)

    try:
        table = session_.my_timetable(start=start, end=end)
    except webuntis.errors.DateNotAllowed:
        session_.logout()
        return []
    except Exception as e:
        print(f"Stundenplan-Abruf fehlgeschlagen ({user['username']}): {e}")
        session_.logout()
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
    session_.logout()
    return result


def fetch_exams(user, days_ahead=90):
    if not user.get("untis_username"):
        return []
    try:
        session_ = untis_login(user)
        exams = session_.exams(start=dt.date.today(), end=dt.date.today() + dt.timedelta(days=days_ahead))
        session_.logout()
    except Exception as e:
        print(f"Klausuren-Abruf fehlgeschlagen ({user['username']}): {e}")
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
    """Vergleicht zwei Stundenplan-Stände.

    Schritt 1: exakter Abgleich über (Datum, Startzeit, Fach). Das erkennt
    Raum-, Lehrer- und Ausfall-Änderungen zuverlässig, da diese Felder
    nicht im Schlüssel stecken.

    Schritt 2: für alle dabei nicht zugeordneten Einträge folgt ein
    zweiter Abgleich pro (Datum, Fach) in Start-Reihenfolge. Das fängt
    genau den Fall ab, dass sich die Startzeit einer Stunde ändert –
    ohne Schritt 2 würde eine verschobene Stunde fälschlich als
    'entfernt' + 'neu hinzugekommen' gewertet, statt als 'geändert' mit
    der konkreten Meldung ('X wurde von 10:15 auf 11:05 verschoben').
    """
    old_map = {entry_key(e): e for e in old_entries}
    new_map = {entry_key(e): e for e in new_entries}

    changed = [(old_map[k], new_map[k]) for k in new_map if k in old_map and old_map[k] != new_map[k]]

    remaining_old = [e for k, e in old_map.items() if k not in new_map]
    remaining_new = [e for k, e in new_map.items() if k not in old_map]

    def group_by_date_subject(entries):
        groups = {}
        for e in entries:
            groups.setdefault((e["date"], e["subject"]), []).append(e)
        for lst in groups.values():
            lst.sort(key=lambda e: e["start"])
        return groups

    old_groups = group_by_date_subject(remaining_old)
    new_groups = group_by_date_subject(remaining_new)

    added, removed = [], []
    for key in set(old_groups) | set(new_groups):
        old_list = old_groups.get(key, [])
        new_list = new_groups.get(key, [])
        paired = min(len(old_list), len(new_list))
        for i in range(paired):
            if old_list[i] != new_list[i]:
                changed.append((old_list[i], new_list[i]))
        removed.extend(old_list[paired:])
        added.extend(new_list[paired:])

    return added, removed, changed


def describe_change(old, new):
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


def send_push(user_id, title, body, tag="allgemein"):
    if not VAPID_PRIVATE_KEY:
        print(f"[Push nicht konfiguriert] {title}: {body}")
        return

    conn = get_db()
    subs = conn.execute("SELECT endpoint, data FROM subscriptions WHERE user_id = ?", (user_id,)).fetchall()
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


def log_notification(user_id, titel, text, typ):
    conn = get_db()
    conn.execute(
        "INSERT INTO notifications (user_id, titel, text, typ, erstellt) VALUES (?, ?, ?, ?, ?)",
        (user_id, titel, text, typ, dt.datetime.now(TZ).isoformat()),
    )
    conn.commit()
    conn.close()


# ==================== KI-Lernassistent ====================

TUTOR_LEVEL_INSTRUCTIONS = {
    "hinweis": "Der/die Lernende möchte gerade nur einen kleinen 💡 Hinweis, keine Lösung. Gib einen kurzen Denkanstoß, keine Schritt-für-Schritt-Anleitung und keine fertige Antwort.",
    "schritt": "Der/die Lernende möchte den 🧩 nächsten Schritt sehen, nicht die komplette Lösung. Erkläre nur, was als Nächstes zu tun ist, und lass den Rest offen.",
    "erklaerung": "Der/die Lernende möchte eine 📖 Erklärung des Konzepts – verständlich, altersgerecht, mit einem kurzen Beispiel.",
    "loesung": "Der/die Lernende möchte jetzt die ✅ vollständige Lösung sehen, inklusive Lösungsweg. Erkläre trotzdem kurz, wie man darauf kommt, statt nur das Ergebnis hinzuwerfen.",
}


def build_tutor_context(user, fach):
    """Sammelt ein paar Signale aus den vorhandenen Nutzerdaten für Personalisierung."""
    lines = []
    if user.get("klasse"):
        lines.append(f"Klasse: {user['klasse']}")
    if fach:
        lines.append(f"Aktuelles Fach: {fach}")

    conn = get_db()
    open_tasks = conn.execute(
        "SELECT fach, text FROM tasks WHERE user_id=? AND erledigt=0 AND typ='hausaufgabe' "
        "ORDER BY faellig IS NULL, faellig LIMIT 5",
        (user["id"],),
    ).fetchall()
    weak = conn.execute(
        "SELECT fach, AVG(note) avg_note, COUNT(*) c FROM grades WHERE user_id=? "
        "GROUP BY fach HAVING c >= 2 ORDER BY avg_note DESC LIMIT 2",
        (user["id"],),
    ).fetchall()
    conn.close()

    if open_tasks:
        lines.append("Offene Hausaufgaben: " + "; ".join(f"{t['fach']}: {t['text']}" for t in open_tasks))
    if weak and get_setting(user["id"], "notenskala", "unterstufe") == "unterstufe":
        # Bei der Notenskala 1-6 ist ein höherer Wert eine schlechtere Note.
        lines.append("Fächer mit tendenziell schwächeren Noten: " + ", ".join(w["fach"] for w in weak))

    return "\n".join(lines) if lines else "Keine weiteren Infos bekannt."


def build_tutor_system_prompt(user, fach, level):
    level_instruction = TUTOR_LEVEL_INSTRUCTIONS.get(
        level, "Falls keine Hilfestufe angegeben ist: beginne mit einem Hinweis bzw. einer kurzen Erklärung "
              "und biete an, bei Bedarf tiefer zu gehen."
    )
    return f"""Du bist der KI-Lernassistent in der Schulapp von {user.get('display_name') or 'einem Schüler/einer Schülerin'}. \
Du bist ein freundlicher, geduldiger, persönlicher Tutor – kein gewöhnlicher Chatbot.

PERSÖNLICHKEIT
- Freundlich, motivierend, geduldig; natürlich, nicht roboterhaft
- Verständlich und altersgerecht erklären, ohne unnötig lange Antworten
- Fehler freundlich korrigieren, nichts erfinden – bei Unsicherheit ehrlich sagen

LERNVERHALTEN
Bei Aufgaben nicht sofort die Lösung geben. Erst verstehen, was der/die Lernende schon weiß, dann mit kleinen \
Hinweisen arbeiten (z.B. "Was denkst du, wäre der erste Schritt?"), selbst nachdenken lassen und erst danach die \
vollständige Lösung zeigen – außer die aktuelle Hilfestufe verlangt direkt danach (siehe unten).

AKTUELLE HILFESTUFE
{level_instruction}

MOTIVATION
Dezent und authentisch, nicht übertrieben (kein "Super! 🎉 Du bist unglaublich!"). Eher konkret: "Das war diesmal \
deutlich besser." oder "Du hast den schwierigsten Schritt jetzt richtig gelöst."

ANTWORTLÄNGE
Kurz und gut lesbar, den/die Lernende nicht mit langen Antworten überfordern. Formeln/Code sauber in Markdown.

KONTEXT ZUM NUTZER (nur verwenden, wenn gerade relevant – nicht aufdrängen)
{build_tutor_context(user, fach)}"""


def call_claude_tutor(system_prompt, history):
    if not ANTHROPIC_API_KEY:
        return None, "Der KI-Tutor ist noch nicht eingerichtet (ANTHROPIC_API_KEY fehlt in den Umgebungsvariablen)."
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": TUTOR_MODEL, "max_tokens": 1000, "system": system_prompt, "messages": history},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return text.strip(), None
    except requests.exceptions.RequestException as e:
        return None, f"KI-Tutor gerade nicht erreichbar: {e}"


# ==================== Scheduler-Jobs (laufen für ALLE Nutzer) ====================


def all_users():
    conn = get_db()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def job_check_timetable():
    for user in all_users():
        uid = user["id"]
        if get_setting(uid, "notify_stundenplan") != "true":
            continue

        new_entries = fetch_timetable_days(user)
        if not new_entries:
            continue

        conn = get_db()
        row = conn.execute("SELECT data FROM timetable_snapshot WHERE user_id = ?", (uid,)).fetchone()
        old_entries = json.loads(row["data"]) if row else []

        if old_entries:
            added, removed, changed = diff_timetable(old_entries, new_entries)

            for old, new in changed:
                text = describe_change(old, new)
                title = "⚠️ Unterricht fällt aus" if new["code"] == "cancelled" else "🔔 Stundenplan geändert"
                send_push(uid, title, text, tag="stundenplan")
                log_notification(uid, title, text, "stundenplan")

            for e in added:
                text = f"Neu im Plan: {e['subject']} am {e['date']} um {e['start']} Uhr."
                send_push(uid, "🔔 Stundenplan geändert", text, tag="stundenplan")
                log_notification(uid, "🔔 Stundenplan geändert", text, "stundenplan")

        conn.execute(
            "INSERT INTO timetable_snapshot (user_id, data) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
            (uid, json.dumps(new_entries)),
        )
        conn.commit()
        conn.close()


def job_reminder():
    for user in all_users():
        uid = user["id"]
        if get_setting(uid, "notify_lernen") != "true":
            continue

        conn = get_db()
        open_tasks = conn.execute(
            "SELECT fach FROM tasks WHERE user_id = ? AND typ = 'hausaufgabe' AND erledigt = 0", (uid,)
        ).fetchall()
        conn.close()

        if not open_tasks:
            title = "🌙 Kurzer Check"
            body = "Aktuell stehen keine offenen Hausaufgaben in der App. Trotzdem alles vorbereitet für morgen?"
        else:
            faecher = sorted({t["fach"] for t in open_tasks})
            anzahl = len(open_tasks)
            title = "📚 Noch 1 Aufgabe offen" if anzahl == 1 else f"📚 Noch {anzahl} Aufgaben offen"
            body = f"Du hast noch {', '.join(faecher)} offen. Willst du jetzt kurz Zeit dafür einplanen?"

        send_push(uid, title, body, tag="lernen")
        log_notification(uid, title, body, "lernen")


def job_exam_countdown():
    today = dt.date.today()
    for user in all_users():
        uid = user["id"]
        if get_setting(uid, "notify_pruefungen") != "true":
            continue

        exams = [{"name": e["name"], "date": e["date"]} for e in fetch_exams(user)]

        conn = get_db()
        manual = conn.execute(
            "SELECT fach, text, faellig FROM tasks WHERE user_id = ? AND typ = 'pruefung' "
            "AND faellig IS NOT NULL AND erledigt = 0",
            (uid,),
        ).fetchall()
        conn.close()
        exams += [{"name": f"{m['fach']}: {m['text']}", "date": m["faellig"]} for m in manual]

        for e in exams:
            exam_date = dt.date.fromisoformat(e["date"])
            days_left = (exam_date - today).days
            if days_left in (7, 3, 1):
                title = f"📅 {e['name']} in {days_left} Tag{'en' if days_left != 1 else ''}"
                body = f"Am {exam_date.strftime('%d.%m.')}."
                send_push(uid, title, body, tag="pruefung")
                log_notification(uid, title, body, "pruefung")


scheduler = BackgroundScheduler(timezone=TZ)


def setup_scheduler():
    scheduler.add_job(job_check_timetable, "interval", minutes=15, id="timetable_check")
    scheduler.add_job(job_exam_countdown, "cron", hour=7, minute=0, id="exam_countdown")
    reschedule_all_reminders()
    scheduler.start()


def reschedule_all_reminders():
    """Sammelt alle einzigartigen Erinnerungszeiten über alle Nutzer hinweg.
    (job_reminder selbst filtert dann pro Nutzer nach dessen eigenen Einstellungen.)"""
    for job in scheduler.get_jobs():
        if job.id.startswith("reminder_"):
            scheduler.remove_job(job.id)

    all_times = set()
    for user in all_users():
        times = json.loads(get_setting(user["id"], "reminder_times", "[]"))
        all_times.update(times)

    for i, t in enumerate(sorted(all_times)):
        hour, minute = map(int, t.split(":"))
        scheduler.add_job(job_reminder, "cron", hour=hour, minute=minute, id=f"reminder_{i}")


# ==================== Auth-Routen ====================


@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    display_name = data.get("display_name", "").strip() or username
    untis_username = data.get("untis_username", "").strip()
    untis_password = data.get("untis_password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "Bitte Benutzername und Passwort ausfüllen."}), 400

    has_untis = bool(untis_username and untis_password)

    conn = get_db()
    exists = conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
    if exists:
        conn.close()
        return jsonify({"ok": False, "error": "Dieser Benutzername ist schon vergeben."}), 400

    if has_untis:
        untis_taken = conn.execute(
            "SELECT 1 FROM users WHERE lower(untis_username) = lower(?) AND untis_username != ''", (untis_username,)
        ).fetchone()
        if untis_taken:
            conn.close()
            return jsonify({"ok": False, "error": "Für diesen WebUntis-Zugang existiert bereits ein App-Account."}), 400
    conn.close()

    if has_untis:
        ok, err = try_untis_login(untis_username, untis_password)
        if not ok:
            return jsonify({"ok": False, "error": f"WebUntis-Zugangsdaten konnten nicht bestätigt werden. ({err})"}), 400

    conn = get_db()

    enc_pw = (fernet.encrypt(untis_password.encode()).decode() if fernet else untis_password) if has_untis else ""
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, display_name, untis_username, untis_password_enc, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), display_name, untis_username if has_untis else "", enc_pw, dt.datetime.now(TZ).isoformat()),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    ensure_default_settings(user_id)
    reschedule_all_reminders()

    session["user_id"] = user_id
    return jsonify({"ok": True, "username": username, "display_name": display_name})


@app.route("/api/login", methods=["POST"])
def api_login():
    ip = get_client_ip()
    if is_locked_out(ip, "user"):
        return jsonify({"ok": False, "error": f"Zu viele Fehlversuche. Bitte in {LOCKOUT_HOURS} Stunden erneut probieren."}), 429

    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")

    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not row or not check_password_hash(row["password_hash"], password):
        record_failed_login(ip, "user")
        return jsonify({"ok": False, "error": "Benutzername oder Passwort falsch."}), 401

    clear_failed_logins(ip, "user")
    session["user_id"] = row["id"]
    return jsonify({"ok": True, "username": row["username"], "display_name": row["display_name"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    if "user_id" not in session:
        return jsonify({"authenticated": False})
    user = current_user()
    if not user:
        session.clear()
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "username": user["username"], "display_name": user["display_name"]})


# ==================== API-Routen (Daten) ====================


@app.route("/")
def index():
    return render_template("index.html")


def admin_authorized():
    return session.get("is_admin") is True


def get_users_overview():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    rows = []
    for u in users:
        tasks_c = conn.execute("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND erledigt=0", (u["id"],)).fetchone()["c"]
        grades_c = conn.execute("SELECT COUNT(*) c FROM grades WHERE user_id=?", (u["id"],)).fetchone()["c"]
        push_c = conn.execute("SELECT COUNT(*) c FROM subscriptions WHERE user_id=?", (u["id"],)).fetchone()["c"]
        rows.append({
            "id": u["id"], "username": u["username"], "display_name": u["display_name"],
            "untis_username": u["untis_username"], "created_at": u["created_at"],
            "tasks": tasks_c, "grades": grades_c, "push": push_c,
        })
    conn.close()
    return rows


@app.route("/admin")
def admin_dashboard():
    if not admin_authorized():
        return render_template("admin_login.html", error=None)
    return render_template("admin.html")


@app.route("/admin/users")
def admin_users_json():
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 403
    return jsonify(get_users_overview())


@app.route("/admin/login", methods=["POST"])
def admin_login():
    ip = get_client_ip()
    if is_locked_out(ip, "admin"):
        return render_template("admin_login.html", error=f"Zu viele Fehlversuche. Bitte in {LOCKOUT_HOURS} Stunden erneut probieren.")

    password = request.form.get("password", "")
    code = request.form.get("code", "")

    pw_ok = ADMIN_PASSWORD_HASH and check_password_hash(ADMIN_PASSWORD_HASH, password)
    totp_ok = ADMIN_TOTP_SECRET and pyotp.TOTP(ADMIN_TOTP_SECRET).verify(code, valid_window=1)

    if not (pw_ok and totp_ok):
        record_failed_login(ip, "admin")
        return render_template("admin_login.html", error="Passwort oder Code falsch.")

    clear_failed_logins(ip, "admin")
    session["is_admin"] = True
    return admin_dashboard()


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return jsonify({"ok": True})


@app.route("/admin/lockouts")
def admin_lockouts():
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db()
    cutoff = (dt.datetime.now(TZ) - dt.timedelta(hours=LOCKOUT_HOURS)).isoformat()
    rows = conn.execute(
        "SELECT ip, scope, COUNT(*) c, MAX(attempt_time) last FROM failed_logins "
        "WHERE attempt_time > ? GROUP BY ip, scope HAVING c >= ? ORDER BY last DESC",
        (cutoff, MAX_ATTEMPTS),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/admin/unlock", methods=["POST"])
def admin_unlock():
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 403
    data = request.get_json()
    clear_failed_logins(data["ip"], data.get("scope", "user"))
    return jsonify({"ok": True})


@app.route("/admin/check/<int:user_id>")
def admin_check(user_id):
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"ok": False, "error": "Nutzer nicht gefunden."})
    if not row["untis_username"]:
        return jsonify({"ok": False, "error": "Kein WebUntis verknüpft."})
    pw = decrypt_untis_password(row["untis_password_enc"])
    ok, err = try_untis_login(row["untis_username"], pw)
    return jsonify({"ok": ok, "error": err})


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
def admin_delete(user_id):
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db()
    for t in ("tasks", "settings", "subscriptions", "notifications", "timetable_snapshot", "grades"):
        conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


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
@login_required
def subscribe():
    sub = request.get_json()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO subscriptions (user_id, endpoint, data) VALUES (?, ?, ?)",
        (session["user_id"], sub["endpoint"], json.dumps(sub)),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/test-push", methods=["POST"])
@login_required
def api_test_push():
    uid = session["user_id"]
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM subscriptions WHERE user_id = ?", (uid,)).fetchone()["c"]
    conn.close()

    if count == 0:
        return jsonify({"ok": False, "error": "Keine Push-Registrierung gefunden. Erst 'Push aktivieren' antippen."})

    send_push(uid, "🔔 Testnachricht", "Wenn du das liest, funktioniert alles!", tag="test")
    log_notification(uid, "🔔 Testnachricht", "Wenn du das liest, funktioniert alles!", "test")
    return jsonify({"ok": True, "subscriptions": count})


@app.route("/api/debug/exams-raw")
@login_required
def api_debug_exams_raw():
    user = current_user()
    try:
        session_ = untis_login(user)
    except Exception as e:
        return jsonify({"step": "login", "error": str(e)})
    try:
        exams = session_.exams(start=dt.date.today(), end=dt.date.today() + dt.timedelta(days=90))
        session_.logout()
        return jsonify({"step": "ok", "count": len(exams), "raw": [str(e) for e in exams[:5]]})
    except Exception as e:
        session_.logout()
        return jsonify({"step": "exams_call", "error": str(e)})


@app.route("/api/timetable")
@login_required
def api_timetable():
    return jsonify(fetch_timetable_days(current_user()))


@app.route("/api/exams")
@login_required
def api_exams():
    return jsonify(fetch_exams(current_user()))


@app.route("/api/tasks", methods=["GET", "POST"])
@login_required
def api_tasks():
    uid = session["user_id"]
    conn = get_db()
    if request.method == "POST":
        data = request.get_json()
        conn.execute(
            "INSERT INTO tasks (user_id, typ, fach, text, faellig, erstellt) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, data.get("typ", "hausaufgabe"), data["fach"], data["text"], data.get("faellig"), dt.datetime.now(TZ).isoformat()),
        )
        conn.commit()

    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? AND erledigt = 0 ORDER BY faellig IS NULL, faellig", (uid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/tasks/<int:task_id>", methods=["PATCH", "DELETE"])
@login_required
def api_task_detail(task_id):
    uid = session["user_id"]
    conn = get_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, uid))
    else:
        data = request.get_json()
        if "erledigt" in data:
            conn.execute("UPDATE tasks SET erledigt = ? WHERE id = ? AND user_id = ?", (int(data["erledigt"]), task_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    uid = session["user_id"]
    if request.method == "POST":
        data = request.get_json()
        for key, value in data.items():
            set_setting(uid, key, json.dumps(value) if isinstance(value, (list, dict)) else str(value))
        if "reminder_times" in data:
            reschedule_all_reminders()
        return jsonify({"ok": True})

    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (uid,)).fetchall()
    conn.close()
    settings = {}
    for r in rows:
        try:
            settings[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            settings[r["key"]] = r["value"]
    user = current_user()
    settings["display_name"] = user["display_name"]
    return jsonify(settings)


@app.route("/api/notifications")
@login_required
def api_notifications():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY erstellt DESC LIMIT 50", (session["user_id"],)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/notifications/<int:note_id>/read", methods=["POST"])
@login_required
def api_notification_read(note_id):
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET gelesen = 1 WHERE id = ? AND user_id = ?", (note_id, session["user_id"])
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ==================== Noten-Tracker ====================


@app.route("/api/grades", methods=["GET", "POST"])
@login_required
def api_grades():
    uid = session["user_id"]
    conn = get_db()
    if request.method == "POST":
        data = request.get_json()
        conn.execute(
            "INSERT INTO grades (user_id, fach, note, gewichtung, art, beschreibung, datum, erstellt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                data["fach"],
                float(data["note"]),
                float(data.get("gewichtung", 1)),
                data.get("art", ""),
                data.get("beschreibung", ""),
                data.get("datum") or dt.date.today().isoformat(),
                dt.datetime.now(TZ).isoformat(),
            ),
        )
        conn.commit()

    rows = conn.execute("SELECT * FROM grades WHERE user_id = ? ORDER BY datum DESC", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/grades/<int:grade_id>", methods=["DELETE"])
@login_required
def api_grade_delete(grade_id):
    conn = get_db()
    conn.execute("DELETE FROM grades WHERE id = ? AND user_id = ?", (grade_id, session["user_id"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ==================== KI-Lernassistent (Routen) ====================


@app.route("/api/tutor/history", methods=["GET", "DELETE"])
@login_required
def api_tutor_history():
    uid = session["user_id"]
    if request.method == "DELETE":
        conn = get_db()
        conn.execute("DELETE FROM tutor_messages WHERE user_id=?", (uid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    conn = get_db()
    rows = conn.execute(
        "SELECT id, role, content, level, fach, erstellt FROM tutor_messages WHERE user_id=? "
        "ORDER BY id DESC LIMIT 40",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in reversed(rows)])


@app.route("/api/tutor/chat", methods=["POST"])
@login_required
def api_tutor_chat():
    data = request.get_json()
    message = (data.get("message") or "").strip()
    level = data.get("level") or ""
    fach = (data.get("fach") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Leere Nachricht."}), 400

    user = current_user()
    uid = user["id"]

    conn = get_db()
    conn.execute(
        "INSERT INTO tutor_messages (user_id, role, content, level, fach, erstellt) VALUES (?,?,?,?,?,?)",
        (uid, "user", message, level, fach, dt.datetime.now(TZ).isoformat()),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT role, content FROM tutor_messages WHERE user_id=? ORDER BY id DESC LIMIT 16", (uid,)
    ).fetchall()
    conn.close()
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    system_prompt = build_tutor_system_prompt(user, fach, level)
    reply, error = call_claude_tutor(system_prompt, history)

    if error:
        return jsonify({"ok": False, "error": error})

    conn = get_db()
    conn.execute(
        "INSERT INTO tutor_messages (user_id, role, content, level, fach, erstellt) VALUES (?,?,?,?,?,?)",
        (uid, "assistant", reply, level, fach, dt.datetime.now(TZ).isoformat()),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "reply": reply})


init_db()
setup_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
