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

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "schulapp.db"
TZ = ZoneInfo("Europe/Berlin")

# ==================== Konfiguration (über Umgebungsvariablen) ====================

UNTIS_SCHOOL = os.environ.get("UNTIS_SCHOOL", "csgb")
UNTIS_SERVER = os.environ.get("UNTIS_SERVER", "csgb.webuntis.com")

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


def try_untis_login(untis_username, untis_password, server=None, school=None):
    try:
        s = webuntis.Session(
            server=(server or UNTIS_SERVER).strip(),
            username=untis_username,
            password=untis_password,
            school=(school or UNTIS_SCHOOL).strip(),
            useragent="Schulapp/2.1",
        ).login()
        s.logout()
        return True, None
    except Exception as e:
        return False, str(e)


app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = SECRET_KEY
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True)

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
            typ TEXT NOT NULL,
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
            art TEXT,
            beschreibung TEXT,
            datum TEXT,
            erstellt TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            note TEXT,
            caught_up INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fach TEXT NOT NULL,
            item TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lesson_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            fach TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fach TEXT NOT NULL,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            minutes INTEGER NOT NULL DEFAULT 25,
            done INTEGER NOT NULL DEFAULT 0,
            exam_date TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS failed_logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'user',
            attempt_time TEXT NOT NULL
        );
        """
    )
    conn.commit()
    migrate_legacy_data(conn)
    conn.close()


def migrate_legacy_data(conn):
    has_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    if has_users > 0:
        return
    if not LEGACY_UNTIS_USERNAME:
        return

    enc_pw = fernet.encrypt(LEGACY_UNTIS_PASSWORD.encode()).decode() if fernet else LEGACY_UNTIS_PASSWORD
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, display_name, untis_username, untis_password_enc, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (LEGACY_UNTIS_USERNAME, generate_password_hash("bitte-aendern"), "Nico",
         LEGACY_UNTIS_USERNAME, enc_pw, dt.datetime.now(TZ).isoformat()),
    )
    legacy_user_id = cur.lastrowid
    for table in ("tasks", "settings", "subscriptions", "notifications", "timetable_snapshot"):
        conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (legacy_user_id,))
    conn.commit()
    print(f"Alt-Daten migriert zu Account '{LEGACY_UNTIS_USERNAME}'. Vorläufiges Passwort: 'bitte-aendern'")


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
    conn.execute("INSERT INTO failed_logins (ip, scope, attempt_time) VALUES (?, ?, ?)",
                 (ip, scope, dt.datetime.now(TZ).isoformat()))
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
    "layout_mode": "auto",
    "klasse": "",
    "notenskala": "unterstufe",
    "untis_server": UNTIS_SERVER,
    "untis_school": UNTIS_SCHOOL,
    "untis_student_firstname": "",
    "untis_student_surname": "",
    "untis_student_id": "",
    "dashboard_widgets": json.dumps(["morning", "today", "tasks", "load"]),
    "assistant_timebox": "25",
    "notify_packlist": "true",
    "school_day_end_buffer": "20",
}


def ensure_default_settings(user_id):
    conn = get_db()
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, ?, ?)", (user_id, k, v))
    conn.commit()
    conn.close()


# ==================== WebUntis ====================


def untis_login(user):
    server = get_setting(user["id"], "untis_server", UNTIS_SERVER)
    school = get_setting(user["id"], "untis_school", UNTIS_SCHOOL)
    return webuntis.Session(
        server=server,
        username=user["untis_username"],
        password=decrypt_untis_password(user["untis_password_enc"]),
        school=school,
        useragent="Schulapp/2.1",
    ).login()


def _normalize_klasse_name(value):
    """Macht Klassenbezeichnungen vergleichbar, z.B. '10 B' == '10b'."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _raw_period_names(period, raw_key):
    """Liest Namen direkt aus dem getTimetable-Rohobjekt, ohne extra Stammdaten-Rechte."""
    raw = getattr(period, "_data", {}) or {}
    items = raw.get(raw_key, []) or []
    names = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = (
            item.get("name")
            or item.get("longName")
            or item.get("foreName")
            or item.get("displayName")
        )
        if value:
            names.append(str(value))
    return names


def _period_related_names(period, raw_key, attr, teacher=False):
    # Neuere WebUntis-Antworten enthalten die Namen teilweise direkt im
    # Stundenplan. Das ist besser, weil Schülerkonten häufig keine Rechte für
    # getTeachers/getSubjects/getRooms besitzen.
    names = _raw_period_names(period, raw_key)
    if names:
        return names
    try:
        related = getattr(period, attr, []) or []
    except Exception:
        return []
    result = []
    for obj in related:
        if teacher:
            value = (
                getattr(obj, "surname", None)
                or getattr(obj, "long_name", None)
                or getattr(obj, "name", None)
            )
        else:
            value = getattr(obj, "name", None) or getattr(obj, "long_name", None)
        if value:
            result.append(str(value))
    return result


def _periods_to_result(table):
    """Wandelt WebUntis-Perioden robust in das Frontend-Format um."""
    result = []
    for p in table:
        subjects = _period_related_names(p, "su", "subjects")
        rooms = _period_related_names(p, "ro", "rooms")
        teachers = _period_related_names(p, "te", "teachers", teacher=True)
        raw = getattr(p, "_data", {}) or {}
        info = (
            raw.get("lstext")
            or raw.get("info")
            or raw.get("substText")
            or raw.get("bkText")
            or ""
        )
        try:
            code = getattr(p, "code", None) or ""
        except Exception:
            code = raw.get("code") or ""
        result.append({
            "date": p.start.date().isoformat(),
            "start": p.start.strftime("%H:%M"),
            "end": p.end.strftime("%H:%M"),
            "subject": ", ".join(subjects) or "?",
            "room": ", ".join(rooms) or "?",
            "teacher": ", ".join(teachers) or "?",
            "code": code,
            "info": str(info),
        })
    return result


def _as_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    return value


def _split_range_by_schoolyear(session_, start, end):
    """Teilt einen Zeitraum so, dass kein WebUntis-Aufruf zwei Schuljahre kreuzt.

    WebUntis lehnt genau solche Anfragen mit DateNotAllowed ab. Falls die
    Schuljahresliste aus irgendeinem Grund nicht verfügbar ist, wird als sichere
    Notlösung tageweise abgefragt.
    """
    start = _as_date(start)
    end = _as_date(end)
    try:
        years = session_.schoolyears()
        segments = []
        for year in years:
            ys = _as_date(year.start)
            ye = _as_date(year.end)
            seg_start = max(start, ys)
            seg_end = min(end, ye)
            if seg_start <= seg_end:
                segments.append({
                    "start": seg_start,
                    "end": seg_end,
                    "schoolyear": getattr(year, "name", "") or f"ID {int(year)}",
                    "schoolyear_id": int(year),
                })
        segments.sort(key=lambda s: s["start"])

        # Daten, die in keinem gemeldeten Schuljahr liegen, trotzdem tageweise
        # probieren. Das macht die Funktion auch bei ungewöhnlicher Schulkonfiguration robust.
        covered = set()
        for seg in segments:
            d = seg["start"]
            while d <= seg["end"]:
                covered.add(d)
                d += dt.timedelta(days=1)
        d = start
        while d <= end:
            if d not in covered:
                segments.append({"start": d, "end": d, "schoolyear": "unbekannt", "schoolyear_id": None})
            d += dt.timedelta(days=1)
        segments.sort(key=lambda s: s["start"])
        if segments:
            return segments, None
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    else:
        error = "WebUntis hat keine passenden Schuljahre geliefert."

    segments = []
    d = start
    while d <= end:
        segments.append({"start": d, "end": d, "schoolyear": "tageweise", "schoolyear_id": None})
        d += dt.timedelta(days=1)
    return segments, error

def _safe_attempt(attempts, name, fn):
    """Führt eine Stundenplan-Strategie aus und protokolliert das Ergebnis."""
    try:
        table = fn()
        count = len(table)
        attempts.append({"source": name, "count": count, "ok": True})
        if count:
            return table, name
    except Exception as e:
        attempts.append({
            "source": name,
            "count": 0,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        })
    return None, None


def _find_configured_klasse(session_, user, attempts):
    klasse_name = (get_setting(user["id"], "klasse", "") or "").strip()
    if not klasse_name:
        return None

    try:
        klassen = session_.klassen()
    except Exception as e:
        attempts.append({
            "source": "Klassenliste",
            "count": 0,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        })
        return None

    wanted = _normalize_klasse_name(klasse_name)
    candidates = []
    for k in klassen:
        names = [
            getattr(k, "name", ""),
            getattr(k, "long_name", ""),
            getattr(k, "longName", ""),
        ]
        normalized = [_normalize_klasse_name(n) for n in names if n]
        if wanted in normalized:
            return k
        if any(wanted and (wanted in n or n in wanted) for n in normalized):
            candidates.append(k)

    if len(candidates) == 1:
        return candidates[0]

    attempts.append({
        "source": f"Klasse '{klasse_name}' suchen",
        "count": 0,
        "ok": False,
        "error": "Klasse nicht eindeutig gefunden.",
    })
    return None


def _student_raw_value(student, *keys):
    data = getattr(student, "_data", {}) or {}
    for key in keys:
        value = getattr(student, key, None)
        if value not in (None, ""):
            return value
        if isinstance(data, dict) and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _find_configured_student(session_, user, attempts):
    """Findet die echte Schüler-ID unabhängig von der Login-Person.

    Das ist vor allem für Eltern-/Erziehungsberechtigtenkonten wichtig: deren
    Login-Person hat selbst keinen Stundenplan. Auch bei WebUntis-Konten mit
    ungewöhnlichem personType kann so die reale Schüler-ID verwendet werden.
    """
    uid = user["id"]
    saved_id = (get_setting(uid, "untis_student_id", "") or "").strip()
    first = (get_setting(uid, "untis_student_firstname", "") or "").strip()
    surname = (get_setting(uid, "untis_student_surname", "") or "").strip()

    if saved_id:
        try:
            sid = int(saved_id)
            attempts.append({
                "source": f"gespeicherte Schüler-ID {sid}",
                "count": 0,
                "ok": True,
                "note": "ID vorhanden",
            })
            return sid, f"{first} {surname}".strip() or f"Schüler-ID {sid}"
        except ValueError:
            set_setting(uid, "untis_student_id", "")

    if not first or not surname:
        attempts.append({
            "source": "Schüler-Zuordnung",
            "count": 0,
            "ok": False,
            "error": "Vorname und Nachname des Schülers sind noch nicht eingetragen.",
        })
        return None, None

    # Schnellster und datensparsamster Weg: WebUntis getPersonId.
    try:
        student = session_.get_student(surname=surname, fore_name=first)
        sid = int(student)
        set_setting(uid, "untis_student_id", str(sid))
        attempts.append({
            "source": f"Schüler suchen: {first} {surname}",
            "count": 1,
            "ok": True,
            "note": f"ID {sid}",
        })
        return sid, f"{first} {surname}"
    except Exception as e:
        attempts.append({
            "source": f"Schüler suchen: {first} {surname}",
            "count": 0,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        })

    # Fallback: getStudents, aber nur serverseitig filtern. Die komplette
    # Schülerliste wird niemals an den Browser geschickt.
    try:
        students = session_.students()
        wanted_first = _normalize_klasse_name(first)
        wanted_surname = _normalize_klasse_name(surname)
        matches = []
        for st in students:
            st_first = _student_raw_value(st, "fore_name", "foreName") or ""
            st_surname = _student_raw_value(st, "name", "long_name", "longName") or ""
            if (
                _normalize_klasse_name(str(st_first)) == wanted_first
                and _normalize_klasse_name(str(st_surname)) == wanted_surname
            ):
                try:
                    matches.append(int(st))
                except Exception:
                    sid = _student_raw_value(st, "id")
                    if sid is not None:
                        matches.append(int(sid))

        matches = sorted(set(matches))
        if len(matches) == 1:
            sid = matches[0]
            set_setting(uid, "untis_student_id", str(sid))
            attempts.append({
                "source": f"Schülerliste: {first} {surname}",
                "count": 1,
                "ok": True,
                "note": f"ID {sid}",
            })
            return sid, f"{first} {surname}"
        if len(matches) > 1:
            attempts.append({
                "source": f"Schülerliste: {first} {surname}",
                "count": len(matches),
                "ok": False,
                "error": "Mehrere Schüler mit exakt diesem Namen gefunden.",
            })
        else:
            attempts.append({
                "source": f"Schülerliste: {first} {surname}",
                "count": 0,
                "ok": False,
                "error": "Kein Schüler mit exakt diesem Namen gefunden.",
            })
    except Exception as e:
        attempts.append({
            "source": "Schülerliste",
            "count": 0,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        })

    return None, None


def _fetch_timetable_auto(session_, user, start, end):
    """Probiert mehrere WebUntis-Wege, statt Fehler als leeren Plan zu verschlucken.

    Hintergrund: python-webuntis benutzt bei my_timetable() personType direkt aus
    dem Login. Manche WebUntis-Konten liefern dort inzwischen Werte wie 12, obwohl
    getTimetable nur die Elementtypen 1..5 akzeptiert. Dann funktioniert der Login,
    aber my_timetable() nicht. Deshalb versuchen wir explizit STUDENT (Typ 5) und
    danach die Klasse.
    """
    attempts = []
    login_result = getattr(session_, "login_result", {}) or {}
    person_id = login_result.get("personId")
    person_type = login_result.get("personType")
    klasse_id = login_result.get("klasseId")

    # 1) Standardweg der Bibliothek.
    table, source = _safe_attempt(
        attempts,
        "my_timetable",
        lambda: session_.my_timetable(start=start, end=end),
    )
    if table is not None:
        return table, source, attempts

    # 2) Echte Schüler-ID verwenden. Das ist der entscheidende Weg für
    # Elternkonten und Konten, deren Login-personType keinen eigenen Plan hat.
    configured_student_id, configured_student_name = _find_configured_student(
        session_, user, attempts
    )
    if configured_student_id:
        table, source = _safe_attempt(
            attempts,
            f"Schüler {configured_student_name} / ID {configured_student_id} (extended)",
            lambda: session_.timetable_extended(
                student=int(configured_student_id), start=start, end=end
            ),
        )
        if table is not None:
            return table, source, attempts

        table, source = _safe_attempt(
            attempts,
            f"Schüler {configured_student_name} / ID {configured_student_id} (basic)",
            lambda: session_.timetable(
                student=int(configured_student_id), start=start, end=end
            ),
        )
        if table is not None:
            return table, source, attempts

    # 3) Login-Person testweise explizit als Schüler abfragen.
    if person_id:
        table, source = _safe_attempt(
            attempts,
            f"Schüler-ID {person_id} (extended)",
            lambda: session_.timetable_extended(student=int(person_id), start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

        table, source = _safe_attempt(
            attempts,
            f"Schüler-ID {person_id} (basic)",
            lambda: session_.timetable(student=int(person_id), start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

    # 4) Falls der Login eine Klasse liefert, diese direkt verwenden.
    if klasse_id not in (None, "", 0, "0"):
        table, source = _safe_attempt(
            attempts,
            f"Klassen-ID {klasse_id} (extended)",
            lambda: session_.timetable_extended(klasse=int(klasse_id), start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

        table, source = _safe_attempt(
            attempts,
            f"Klassen-ID {klasse_id} (basic)",
            lambda: session_.timetable(klasse=int(klasse_id), start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

    # 5) Manuell in der Schulapp eingetragene Klasse.
    klasse = _find_configured_klasse(session_, user, attempts)
    if klasse is not None:
        table, source = _safe_attempt(
            attempts,
            f"eingetragene Klasse {getattr(klasse, 'name', '')} (extended)",
            lambda: session_.timetable_extended(klasse=klasse, start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

        table, source = _safe_attempt(
            attempts,
            f"eingetragene Klasse {getattr(klasse, 'name', '')} (basic)",
            lambda: session_.timetable(klasse=klasse, start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

    # 6) Wenn es offensichtlich ein Lehrer-Konto ist, auch Lehrer explizit testen.
    if person_id and person_type == 2:
        table, source = _safe_attempt(
            attempts,
            f"Lehrer-ID {person_id}",
            lambda: session_.timetable_extended(teacher=int(person_id), start=start, end=end),
        )
        if table is not None:
            return table, source, attempts

    return [], None, attempts


def _fetch_timetable_across_schoolyears(session_, user, start, end):
    """Holt einen Zeitraum in schuljahresreinen Teilstücken und führt ihn zusammen."""
    segments, split_error = _split_range_by_schoolyear(session_, start, end)
    combined = []
    all_attempts = []
    sources = []

    if split_error:
        all_attempts.append({
            "source": "Schuljahre lesen",
            "count": 0,
            "ok": False,
            "error": split_error + " – sichere tageweise Abfrage wird verwendet.",
        })

    for seg in segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        label = seg["schoolyear"]
        table, source, attempts = _fetch_timetable_auto(session_, user, seg_start, seg_end)
        prefix = f"{seg_start.isoformat()}–{seg_end.isoformat()} [{label}]"
        for attempt in attempts:
            item = dict(attempt)
            item["source"] = f"{prefix} → {item.get('source', '?')}"
            all_attempts.append(item)
        if table:
            combined.extend(list(table))
            if source:
                sources.append(f"{source} ({label})")

    # Doppelte Perioden vermeiden, falls ein Server Schuljahresgrenzen überlappend meldet.
    unique = []
    seen = set()
    for p in combined:
        raw = getattr(p, "_data", {}) or {}
        key = (
            raw.get("id"), raw.get("date"), raw.get("startTime"), raw.get("endTime"),
            tuple(x.get("id") for x in (raw.get("su") or []) if isinstance(x, dict)),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    source_text = ", ".join(dict.fromkeys(sources)) if sources else None
    return unique, source_text, all_attempts, segments


def fetch_timetable_days(user, start=None, end=None, days_ahead=5):
    if not user.get("untis_username"):
        return []

    if start is None:
        start = dt.date.today()
    if end is None:
        end = start + dt.timedelta(days=days_ahead)

    try:
        session_ = untis_login(user)
    except Exception as e:
        print(f"WebUntis-Login fehlgeschlagen ({user['username']}): {e}")
        return []

    try:
        table, source, attempts, segments = _fetch_timetable_across_schoolyears(
            session_, user, start, end
        )
        if table:
            print(
                f"WebUntis ({user['username']}): {len(table)} Einträge über {source}; "
                f"Zeitraum {start}..{end}; Segmente="
                f"{[(s['start'], s['end'], s['schoolyear']) for s in segments]}"
            )
            try:
                return _periods_to_result(table)
            except Exception as e:
                print(f"WebUntis-Perioden konnten nicht umgewandelt werden ({user['username']}): {e}")
                return []

        login_result = getattr(session_, "login_result", {}) or {}
        safe_info = {
            "personType": login_result.get("personType"),
            "personId": login_result.get("personId"),
            "klasseId": login_result.get("klasseId"),
        }
        print(
            f"WebUntis liefert keinen Stundenplan ({user['username']}) für {start}..{end}. "
            f"Login-Info={safe_info}; Segmente={segments}; Versuche={attempts}"
        )
        return []
    finally:
        try:
            session_.logout(suppress_errors=True)
        except TypeError:
            try:
                session_.logout()
            except Exception:
                pass
        except Exception:
            pass

def fetch_exams(user, days_ahead=90):
    if not user.get("untis_username"):
        return []
    start = dt.date.today()
    end = start + dt.timedelta(days=days_ahead)
    try:
        session_ = untis_login(user)
        segments, _ = _split_range_by_schoolyear(session_, start, end)
        exams = []
        for seg in segments:
            try:
                exams.extend(list(session_.exams(start=seg["start"], end=seg["end"])))
            except Exception as e:
                print(
                    f"Klausuren-Abruf Teilzeitraum {seg['start']}..{seg['end']} "
                    f"fehlgeschlagen ({user['username']}): {e}"
                )
        session_.logout(suppress_errors=True)
    except Exception as e:
        print(f"Klausuren-Abruf fehlgeschlagen ({user['username']}): {e}")
        return []

    result = []
    seen = set()
    for e in exams:
        key = (getattr(e, "id", None), e.start)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "name": getattr(e, "name", None) or getattr(e, "subject", "Klausur"),
            "date": e.start.date().isoformat(),
            "time": e.start.strftime("%H:%M"),
        })
    return result


def entry_key(entry):
    return f"{entry['date']}_{entry['start']}_{entry['subject']}"


def diff_timetable(old_entries, new_entries):
    """Vergleicht zwei Stundenplan-Stände. Erst exakter Abgleich über
    (Datum, Startzeit, Fach); übrig gebliebene Einträge werden zusätzlich
    pro (Datum, Fach) in Startzeit-Reihenfolge gepaart, damit eine
    verschobene Stunde als 'geändert' statt 'entfernt+neu' erkannt wird."""
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
            webpush(subscription_info=subscription_info, data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims={"sub": VAPID_CLAIMS_EMAIL})
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



def _tomorrow_pack_items(user):
    """Ermittelt Materialien für den nächsten Schultag aus Fächern + eigenen Materiallisten."""
    uid = user["id"]
    today = dt.date.today()
    conn = get_db()
    material_rows = conn.execute(
        "SELECT fach, item FROM materials WHERE user_id = ? ORDER BY fach, item", (uid,)
    ).fetchall()
    conn.close()
    by_subject = {}
    for row in material_rows:
        by_subject.setdefault((row["fach"] or "").strip().lower(), []).append(row["item"])

    for offset in range(1, 6):
        day = today + dt.timedelta(days=offset)
        lessons = fetch_timetable_days(user, start=day, end=day)
        lessons = [x for x in lessons if x.get("code") != "cancelled"]
        if not lessons:
            continue
        subjects = []
        items = []
        for lesson in lessons:
            subject = (lesson.get("subject") or "").strip()
            if subject and subject not in subjects:
                subjects.append(subject)
            low = subject.lower()
            for item in by_subject.get(low, []):
                if item not in items:
                    items.append(item)
            if any(k in low for k in ("sport", "pe", "bewegung")) and "Sportsachen" not in items:
                items.append("Sportsachen")
            if "kunst" in low and "Kunstmaterial" not in items:
                items.append("Kunstmaterial")
            if "musik" in low and "Musikmaterial / Instrument" not in items:
                items.append("Musikmaterial / Instrument")
        return day, subjects, items
    return None, [], []


def _upcoming_manual_exams(uid):
    conn = get_db()
    rows = conn.execute(
        "SELECT fach, text, faellig FROM tasks WHERE user_id = ? AND typ = 'pruefung' "
        "AND erledigt = 0 AND faellig IS NOT NULL ORDER BY faellig", (uid,)
    ).fetchall()
    conn.close()
    return [{"name": f"{r['fach']}: {r['text']}", "date": r["faellig"], "fach": r["fach"]} for r in rows]

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

        if get_setting(uid, "notify_packlist", "true") == "true" and dt.datetime.now(TZ).hour >= 17:
            try:
                pack_day, pack_subjects, pack_items = _tomorrow_pack_items(user)
                if pack_day and (pack_items or pack_subjects):
                    extra = pack_items[:5] if pack_items else pack_subjects[:5]
                    body += f" Für {pack_day.strftime('%A')}: " + ", ".join(extra) + "."
            except Exception as e:
                print(f"Packlisten-Erinnerung fehlgeschlagen ({user['username']}): {e}")

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
            "AND faellig IS NOT NULL AND erledigt = 0", (uid,)
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
    untis_server = data.get("untis_server", UNTIS_SERVER).strip() or UNTIS_SERVER
    untis_school = data.get("untis_school", UNTIS_SCHOOL).strip() or UNTIS_SCHOOL

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
        ok, err = try_untis_login(untis_username, untis_password, untis_server, untis_school)
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
    set_setting(user_id, "untis_server", untis_server)
    set_setting(user_id, "untis_school", untis_school)
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


# ==================== Seiten & Admin ====================


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
    for t in ("tasks", "settings", "subscriptions", "notifications", "timetable_snapshot", "grades", "absences", "materials", "lesson_notes", "study_sessions"):
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
    """Unterstützt entweder ?days=N (nächste N Tage ab heute, Standard)
    oder ?start=YYYY-MM-DD&end=YYYY-MM-DD (fester Zeitraum, z.B. für die
    Wochenansicht - kann auch bereits vergangene Tage der Woche zeigen)."""
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    days = request.args.get("days", type=int)

    kwargs = {}
    if start_str and end_str:
        kwargs["start"] = dt.date.fromisoformat(start_str)
        kwargs["end"] = dt.date.fromisoformat(end_str)
    elif days:
        kwargs["days_ahead"] = days

    return jsonify(fetch_timetable_days(current_user(), **kwargs))


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



@app.route("/api/untis", methods=["GET", "POST", "DELETE"])
@login_required
def api_untis():
    uid = session["user_id"]
    user = current_user()

    if request.method == "GET":
        return jsonify({
            "connected": bool(user.get("untis_username")),
            "username": user.get("untis_username") or "",
            "server": get_setting(uid, "untis_server", UNTIS_SERVER),
            "school": get_setting(uid, "untis_school", UNTIS_SCHOOL),
            "student_firstname": get_setting(uid, "untis_student_firstname", ""),
            "student_surname": get_setting(uid, "untis_student_surname", ""),
            "student_id": get_setting(uid, "untis_student_id", ""),
        })

    if request.method == "DELETE":
        conn = get_db()
        conn.execute("UPDATE users SET untis_username = '', untis_password_enc = '' WHERE id = ?", (uid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    data = request.get_json() or {}
    untis_username = data.get("username", "").strip()
    untis_password = data.get("password", "")
    untis_server = data.get("server", UNTIS_SERVER).strip() or UNTIS_SERVER
    untis_school = data.get("school", UNTIS_SCHOOL).strip() or UNTIS_SCHOOL
    student_firstname = data.get("student_firstname", "").strip()
    student_surname = data.get("student_surname", "").strip()

    if not untis_username:
        return jsonify({"ok": False, "error": "Bitte WebUntis-Benutzername ausfüllen."}), 400

    # Beim Ändern von Schülername/Server/Schule muss das Passwort nicht erneut
    # eingegeben werden, wenn bereits ein WebUntis-Konto verbunden ist.
    if not untis_password and user.get("untis_password_enc"):
        try:
            untis_password = decrypt_untis_password(user["untis_password_enc"])
        except Exception:
            untis_password = ""
    if not untis_password:
        return jsonify({"ok": False, "error": "Bitte WebUntis-Passwort ausfüllen."}), 400

    ok, err = try_untis_login(untis_username, untis_password, untis_server, untis_school)
    if not ok:
        return jsonify({"ok": False, "error": f"WebUntis-Verbindung fehlgeschlagen: {err}"}), 400

    conn = get_db()
    taken = conn.execute(
        "SELECT 1 FROM users WHERE lower(untis_username) = lower(?) AND id != ? AND untis_username != ''",
        (untis_username, uid),
    ).fetchone()
    if taken:
        conn.close()
        return jsonify({"ok": False, "error": "Dieser WebUntis-Zugang ist bereits mit einem anderen App-Account verbunden."}), 400

    enc_pw = fernet.encrypt(untis_password.encode()).decode() if fernet else untis_password
    conn.execute(
        "UPDATE users SET untis_username = ?, untis_password_enc = ? WHERE id = ?",
        (untis_username, enc_pw, uid),
    )
    conn.commit()
    conn.close()
    old_first = (get_setting(uid, "untis_student_firstname", "") or "").strip()
    old_surname = (get_setting(uid, "untis_student_surname", "") or "").strip()
    set_setting(uid, "untis_server", untis_server)
    set_setting(uid, "untis_school", untis_school)
    set_setting(uid, "untis_student_firstname", student_firstname)
    set_setting(uid, "untis_student_surname", student_surname)
    if old_first != student_firstname or old_surname != student_surname:
        set_setting(uid, "untis_student_id", "")
    return jsonify({"ok": True})


@app.route("/api/untis/test", methods=["POST"])
@login_required
def api_untis_test():
    """Diagnose: Login + Schülerplan, automatisch an Schuljahresgrenzen getrennt."""
    user = current_user()
    if not user.get("untis_username"):
        return jsonify({"ok": False, "error": "Noch kein WebUntis-Konto verbunden."}), 400

    start = dt.date.today()
    end = start + dt.timedelta(days=14)

    try:
        s = untis_login(user)
    except Exception as e:
        return jsonify({"ok": False, "login_ok": False, "error": f"Login fehlgeschlagen: {e}"}), 400

    try:
        login_result = getattr(s, "login_result", {}) or {}
        info = {
            "personType": login_result.get("personType"),
            "personId": login_result.get("personId"),
            "klasseId": login_result.get("klasseId"),
        }
        table, source, attempts, segments = _fetch_timetable_across_schoolyears(
            s, user, start, end
        )
        segment_json = [
            {
                "start": seg["start"].isoformat(),
                "end": seg["end"].isoformat(),
                "schoolyear": seg["schoolyear"],
                "schoolyear_id": seg["schoolyear_id"],
            }
            for seg in segments
        ]

        if table:
            return jsonify({
                "ok": True,
                "login_ok": True,
                "timetable_ok": True,
                "count": len(table),
                "source": source,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "login": info,
                "attempts": attempts,
                "schoolyear_segments": segment_json,
            })

        return jsonify({
            "ok": False,
            "login_ok": True,
            "timetable_ok": False,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "login": info,
            "attempts": attempts,
            "schoolyear_segments": segment_json,
            "error": "Login funktioniert, aber WebUntis hat auch nach Trennung an den Schuljahresgrenzen keine Stunden geliefert.",
        }), 400
    finally:
        try:
            s.logout(suppress_errors=True)
        except TypeError:
            try:
                s.logout()
            except Exception:
                pass
        except Exception:
            pass

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
    conn.execute("UPDATE notifications SET gelesen = 1 WHERE id = ? AND user_id = ?", (note_id, session["user_id"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})



# ==================== Schulassistent / Organisation ====================

@app.route("/api/absences", methods=["GET", "POST"])
@login_required
def api_absences():
    uid = session["user_id"]
    conn = get_db()
    if request.method == "POST":
        data = request.get_json() or {}
        start_date = data.get("start_date") or dt.date.today().isoformat()
        end_date = data.get("end_date") or start_date
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        conn.execute(
            "INSERT INTO absences (user_id, start_date, end_date, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, start_date, end_date, data.get("note", ""), dt.datetime.now(TZ).isoformat()),
        )
        conn.commit()
    rows = conn.execute(
        "SELECT * FROM absences WHERE user_id = ? ORDER BY start_date DESC, id DESC", (uid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/absences/<int:item_id>", methods=["PATCH", "DELETE"])
@login_required
def api_absence_detail(item_id):
    uid = session["user_id"]
    conn = get_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM absences WHERE id = ? AND user_id = ?", (item_id, uid))
    else:
        data = request.get_json() or {}
        if "caught_up" in data:
            conn.execute("UPDATE absences SET caught_up = ? WHERE id = ? AND user_id = ?", (int(bool(data["caught_up"])), item_id, uid))
    conn.commit(); conn.close()
    return jsonify({"ok": True})


@app.route("/api/materials", methods=["GET", "POST"])
@login_required
def api_materials():
    uid = session["user_id"]
    conn = get_db()
    if request.method == "POST":
        data = request.get_json() or {}
        fach = (data.get("fach") or "").strip()
        item = (data.get("item") or "").strip()
        if not fach or not item:
            conn.close(); return jsonify({"ok": False, "error": "Fach und Material fehlen."}), 400
        exists = conn.execute("SELECT 1 FROM materials WHERE user_id=? AND lower(fach)=lower(?) AND lower(item)=lower(?)", (uid, fach, item)).fetchone()
        if not exists:
            conn.execute("INSERT INTO materials (user_id, fach, item, created_at) VALUES (?, ?, ?, ?)", (uid, fach, item, dt.datetime.now(TZ).isoformat()))
            conn.commit()
    rows = conn.execute("SELECT * FROM materials WHERE user_id=? ORDER BY fach, item", (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/materials/<int:item_id>", methods=["DELETE"])
@login_required
def api_material_delete(item_id):
    conn = get_db(); conn.execute("DELETE FROM materials WHERE id=? AND user_id=?", (item_id, session["user_id"])); conn.commit(); conn.close()
    return jsonify({"ok": True})


@app.route("/api/lesson-notes", methods=["GET", "POST"])
@login_required
def api_lesson_notes():
    uid = session["user_id"]
    conn = get_db()
    if request.method == "POST":
        data = request.get_json() or {}
        fach = (data.get("fach") or "").strip(); text = (data.get("text") or "").strip()
        if not fach or not text:
            conn.close(); return jsonify({"ok": False, "error": "Fach und Notiz fehlen."}), 400
        conn.execute(
            "INSERT INTO lesson_notes (user_id, date, fach, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, data.get("date") or dt.date.today().isoformat(), fach, text, dt.datetime.now(TZ).isoformat()),
        ); conn.commit()
    rows = conn.execute("SELECT * FROM lesson_notes WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 300", (uid,)).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/lesson-notes/<int:item_id>", methods=["DELETE"])
@login_required
def api_lesson_note_delete(item_id):
    conn = get_db(); conn.execute("DELETE FROM lesson_notes WHERE id=? AND user_id=?", (item_id, session["user_id"])); conn.commit(); conn.close()
    return jsonify({"ok": True})


@app.route("/api/study-sessions", methods=["GET", "POST"])
@login_required
def api_study_sessions():
    uid = session["user_id"]
    conn = get_db()
    if request.method == "POST":
        data = request.get_json() or {}
        conn.execute(
            "INSERT INTO study_sessions (user_id, fach, title, date, minutes, done, exam_date, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (uid, (data.get("fach") or "Lernen").strip(), (data.get("title") or "Lerneinheit").strip(), data.get("date") or dt.date.today().isoformat(), max(5, min(180, int(data.get("minutes", 25)))), data.get("exam_date"), dt.datetime.now(TZ).isoformat()),
        ); conn.commit()
    rows = conn.execute("SELECT * FROM study_sessions WHERE user_id=? ORDER BY done, date, id", (uid,)).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/study-sessions/<int:item_id>", methods=["PATCH", "DELETE"])
@login_required
def api_study_session_detail(item_id):
    uid = session["user_id"]
    conn = get_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM study_sessions WHERE id=? AND user_id=?", (item_id, uid))
    else:
        data = request.get_json() or {}
        if "done" in data:
            conn.execute("UPDATE study_sessions SET done=? WHERE id=? AND user_id=?", (int(bool(data["done"])), item_id, uid))
    conn.commit(); conn.close(); return jsonify({"ok": True})


@app.route("/api/study-plan/generate", methods=["POST"])
@login_required
def api_study_plan_generate():
    uid = session["user_id"]
    data = request.get_json() or {}
    fach = (data.get("fach") or "Lernen").strip()
    title = (data.get("title") or f"Vorbereitung {fach}").strip()
    exam_date = dt.date.fromisoformat(data["exam_date"])
    today = dt.date.today()
    last_day = exam_date - dt.timedelta(days=1)
    candidates = []
    d = today
    while d <= last_day:
        if d.weekday() < 6:  # Sonntag möglichst frei lassen
            candidates.append(d)
        d += dt.timedelta(days=1)
    if not candidates:
        candidates = [today]
    candidates = candidates[-min(7, len(candidates)):]
    minutes = max(15, min(90, int(data.get("minutes", 30))))
    conn = get_db()
    conn.execute("DELETE FROM study_sessions WHERE user_id=? AND exam_date=? AND lower(fach)=lower(?) AND done=0", (uid, exam_date.isoformat(), fach))
    for i, day in enumerate(candidates, 1):
        session_title = title if len(candidates) == 1 else f"{title} · Teil {i}/{len(candidates)}"
        conn.execute(
            "INSERT INTO study_sessions (user_id, fach, title, date, minutes, done, exam_date, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (uid, fach, session_title, day.isoformat(), minutes, exam_date.isoformat(), dt.datetime.now(TZ).isoformat()),
        )
    conn.commit(); conn.close()
    return jsonify({"ok": True, "count": len(candidates)})


@app.route("/api/widget")
@login_required
def api_widget():
    """Kompakte Datenbasis für ein späteres Apple-Widget / Homescreen-Widget."""
    user = current_user(); now = dt.datetime.now(TZ); today = now.date()
    lessons = fetch_timetable_days(user, start=today, end=today + dt.timedelta(days=3))
    today_lessons = [x for x in lessons if x.get("date") == today.isoformat() and x.get("code") != "cancelled"]
    now_hm = now.strftime("%H:%M")
    current = next((x for x in today_lessons if x.get("start", "") <= now_hm < x.get("end", "")), None)
    nxt = next((x for x in today_lessons if x.get("start", "") > now_hm), None)
    conn = get_db()
    tasks = conn.execute("SELECT * FROM tasks WHERE user_id=? AND erledigt=0 ORDER BY faellig IS NULL, faellig LIMIT 3", (session["user_id"],)).fetchall(); conn.close()
    exams = fetch_exams(user)
    return jsonify({
        "updated_at": now.isoformat(), "current": current, "next": nxt,
        "tasks": [dict(r) for r in tasks], "next_exam": exams[0] if exams else None,
    })

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
            (uid, data["fach"], float(data["note"]), float(data.get("gewichtung", 1)),
             data.get("art", ""), data.get("beschreibung", ""),
             data.get("datum") or dt.date.today().isoformat(), dt.datetime.now(TZ).isoformat()),
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


init_db()
setup_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
