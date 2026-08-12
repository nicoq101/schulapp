# Schulapp – Einrichtung

Deine Web-App mit Dashboard, Aufgaben, Stundenplan und echten
Push-Benachrichtigungen. Läuft kostenlos in der Cloud (Render.com) und
wird auf dem iPad/Handy wie eine echte App installiert.

## 1. Bei GitHub hochladen

Render zieht den Code direkt aus einem GitHub-Repository.

1. Gehe auf https://github.com und logg dich ein (Konto erstellen, falls nötig).
2. Oben rechts "+" → "New repository". Name z.B. `schulapp`. Auf "Private" stellen
   (dein WebUntis-Passwort soll ja nicht öffentlich einsehbar sein –
   auch wenn wir es gleich NICHT in den Code schreiben, sondern separat).
3. Lade diesen kompletten Ordner in das neue Repository hoch:
   Auf der Repo-Seite "uploading an existing file" anklicken → alle
   Dateien aus diesem `schulapp`-Ordner reinziehen → "Commit changes".

## 2. Bei Render.com deployen (kostenlos)

1. Gehe auf https://render.com und logg dich mit deinem GitHub-Konto ein.
2. "New +" → "Web Service".
3. Dein `schulapp`-Repository auswählen.
4. Einstellungen:
   - **Name**: z.B. `meine-schulapp`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: Free
5. Unten bei "Environment Variables" folgende Werte eintragen (Klick auf "Add Environment Variable" für jede Zeile):

   | Key | Value |
   |---|---|
   | `UNTIS_SCHOOL` | `csgb` |
   | `UNTIS_SERVER` | `csgb.webuntis.com` |
   | `UNTIS_USERNAME` | dein WebUntis-Benutzername |
   | `UNTIS_PASSWORD` | dein WebUntis-Passwort |
   | `VAPID_PUBLIC_KEY` | `BJnr4p_BSiYAmPIku_io5zj_9H64Qm-V_DwmFnXTQ7VG0kHvTaaLIEQeSbVDLKD5KpBDQAzT17tYA9WzbGizRRQ` |
   | `VAPID_PRIVATE_KEY` | `IzCuSHFzxcE0bTmYGI4DibAenOwvKq7jeLdE5BbnSTk` |
   | `VAPID_CLAIMS_EMAIL` | `mailto:deine-email@example.com` |

   Die beiden VAPID-Schlüssel sind bereits fertig für dich generiert (siehe Tabelle) –
   die brauchst du nur zum Verschlüsseln der Push-Benachrichtigungen, das hat nichts mit
   deiner echten E-Mail-Adresse zu tun (die `VAPID_CLAIMS_EMAIL` muss nur irgendeine
   gültige Adresse sein, wird nie angezeigt).

6. "Create Web Service" klicken. Render baut die App jetzt (dauert 2-5 Minuten).
7. Am Ende bekommst du eine Adresse wie `https://meine-schulapp.onrender.com` – das ist deine App!

**Wichtiger Hinweis zum kostenlosen Plan:** Render "schläft" nach 15 Minuten
ohne Anfrage ein (Free-Tier-Limit) und der erste Aufruf danach dauert
dann ~30 Sekunden zum Aufwecken. Für die geplanten Erinnerungen
(17:30/19:00/21:30 etc.) ist das ein Problem, wenn die App gerade schläft.

**Lösung (kostenlos):** Richte auf https://cron-job.org (kostenlos, kein
Geld nötig) einen "Cronjob" ein, der alle 10 Minuten deine App-Adresse
aufruft (z.B. `https://meine-schulapp.onrender.com/api/settings`) – das
hält die App durchgehend wach, damit die Erinnerungen pünktlich kommen.

## 3. App aufs iPad installieren

1. Öffne deine App-Adresse (`https://meine-schulapp.onrender.com`) in **Safari**
   auf dem iPad (wichtig: Safari, nicht Chrome – nur Safari kann auf iOS
   Apps zum Homescreen hinzufügen).
2. Tippe auf das Teilen-Symbol (Quadrat mit Pfeil nach oben).
3. "Zum Home-Bildschirm" auswählen.
4. Name bestätigen, "Hinzufügen" tippen.
5. Ab jetzt hast du ein eigenes App-Icon auf dem Homescreen – öffnest du
   es darüber, läuft die App im Vollbild, wie eine echte App.
6. In der App unter "Einstellungen" → "Push aktivieren" antippen und die
   Berechtigung erlauben – danach kommen Benachrichtigungen auch wirklich an.

**Wichtig:** Push-Benachrichtigungen funktionieren auf dem iPad nur, wenn
die App **über das Homescreen-Icon** geöffnet wurde (nicht direkt im
Safari-Tab) – das ist eine Apple-Vorgabe, kein Fehler.

## 4. Änderungen später vornehmen

Willst du später etwas am Code ändern? Einfach die geänderte Datei bei
GitHub hochladen (auf der Repo-Seite die Datei anklicken → Stift-Symbol
"Edit" → Änderung → "Commit changes"). Render erkennt das automatisch
und baut die App innerhalb weniger Minuten neu.

## Was drin ist

- **Dashboard**: heutiger Plan als Zeitleiste, nächste Stunde, offene
  Aufgaben, nächste Klausur
- **Aufgaben**: Hausaufgaben & Klausuren mit Fälligkeit, automatisch nach
  Dringlichkeit sortiert (heute/morgen/diese Woche/später)
- **Plan**: Wochenübersicht + Klausuren-Liste mit Countdown
- **Einstellungen**: Dark/Light/System-Modus, Benachrichtigungen einzeln
  an/aus, Erinnerungszeiten frei einstellbar, Name & Klasse
- **Push-Benachrichtigungen** bei: Stundenplanänderungen (konkret
  beschrieben, z.B. "Mathe wurde von 10:15 auf 11:05 verschoben"),
  Lern-Erinnerungen zu deinen Wunschzeiten, Klausur-Countdown (7/3/1 Tag vorher)

## Bewusst nicht enthalten

- **Native App-Store-App**: bräuchte einen Mac mit Xcode und einen Apple
  Developer Account (99$/Jahr) – technisch nicht im Chat umsetzbar. Die
  PWA fühlt sich auf dem iPad aber sehr ähnlich an.
- **Pomodoro-Timer / Statistiken**: bewusst weggelassen, um die App schlank
  zu halten – lässt sich bei Bedarf leicht ergänzen.
