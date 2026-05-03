# WOS Bot - Refactoring Plan

## Status: ABGESCHLOSSEN

---

## Umsetzungsstrategie

### Parallele Agents

Die Umsetzung soll idealerweise mit **parallelen Agents** erfolgen, um die Arbeit zu beschleunigen:

- **Unabhaengige Dateien parallel bearbeiten:** Z.B. bei Step 2 (DB Layer) koennen mehrere Cogs gleichzeitig migriert werden, solange sie keine gegenseitigen Imports haben.
- **Recherche und Umsetzung trennen:** Ein Agent prueft den Ist-Zustand, ein anderer setzt die Aenderungen um.
- **Step 4 (Logging)** eignet sich besonders gut fuer Parallelisierung: Jede Datei kann unabhaengig migriert werden.
- **Step 5 (Menustruktur)** kann teilweise parallel zu Step 4 laufen, da unterschiedliche Dateien betroffen sind.

### Pflicht: Verifizierung nach jedem Step

**KRITISCH:** Nach Abschluss jedes Steps MUSS eine Verifizierung stattfinden, bevor der naechste Step begonnen wird.

#### Verifizierungs-Checkliste pro Step

**Automatische Pruefungen (nach JEDEM Step):**

1. **Syntax-Check:** `python -m py_compile` fuer jede geaenderte Datei
2. **Import-Check:** `python -c "from cogs import <module>"` fuer jedes betroffene Modul
3. **Bot-Start-Check:** Bot muss fehlerfrei starten (`python main.py` - pruefen ob alle Cogs laden)

**Step-spezifische Pruefungen:**

| Step | Zusaetzliche Verifizierung |
|------|---------------------------|
| 1. Secrets | Bot startet MIT `.env` UND mit Fallback-Defaults (ohne `.env`) |
| 2. DB Layer | Jedes migrierte Cog einzeln testen: Commands ausfuehren, Console auf SQLite-Fehler pruefen |
| 3. Utils | Alle Imports aufloesbar, alle verschobenen Klassen/Funktionen von allen Consumern erreichbar |
| 4. Logging | Console-Output vorhanden und korrekt formatiert, keine verwaisten `print()` Calls |
| 5. Menustruktur | JEDEN Menupfad durchklicken: Main -> Sub -> Back -> Main. Keine Dead-Ends |
| 6. Cog-Splits | Alle Commands, Buttons, Modals und Pagination des gesplitteten Cogs funktionieren |

**Ablauf:**

```
Step N Umsetzung (idealerweise mit parallelen Agents)
         |
         v
Step N Verifizierung (automatische + manuelle Checks)
         |
    [PASS] ──→ Weiter zu Step N+1
         |
    [FAIL] ──→ Fehler beheben, erneut verifizieren
```

**WICHTIG:**
- Kein Step darf begonnen werden, solange der vorherige nicht vollstaendig verifiziert ist
- Bei einem Fehler in der Verifizierung: Ursache analysieren und fixen, NICHT den naechsten Step starten
- Nach dem Fix: Verifizierung des gesamten Steps wiederholen (nicht nur den Fix)
- Der Bot muss nach JEDEM Step vollstaendig funktionsfaehig bleiben (kein Big-Bang-Rewrite)

---

## Ausgangslage

- **20 Python-Dateien**, ~17.271 Zeilen Code
- **8 SQLite-Datenbanken**, 135x `sqlite3.connect()` verstreut
- **268x `print()`** statt strukturiertes Logging
- **4x identische** `_create_monitored_task()` Kopien
- **6x separate** Admin-Check Implementierungen
- **Hardcoded Secrets** in config.py und backup_operations.py
- **Menüstruktur** mit Dead-Ends, inkonsistenten Styles und fehlenden Back-Buttons

---

## Abhängigkeiten / Reihenfolge

```
Step 1 (Secrets/.env)
   ↓
Step 2 (Shared DB Layer)  ───→  Step 3 (Utility Module)
                                     ↓
                                Step 4 (Python Logging)
                                     ↓
                                Step 5 (Menustruktur)
                                     ↓
                                Step 6 (Cog-Splits)
```

---

## Step 1: Secrets in `.env`

**Ziel:** Alle hardcoded Secrets aus dem Quellcode entfernen.
**Risiko:** NIEDRIG
**Status:** [x] ERLEDIGT

### Neue Dateien

| Datei | Zweck |
|-------|-------|
| `.env` | Enthalt alle Secrets als Environment-Variablen |
| `.env.example` | Template mit Platzhaltern (wird committed) |
| `.gitignore` | Schutzt `.env`, `bot_token.txt`, `db/`, `__pycache__/`, `log/` |

### `.env` Inhalt

```
BOT_TOKEN=<Wert aus bot_token.txt>
WOS_ENCRYPT_KEY=tB87#kPtkxqOS2
WOS_TEST_PLAYER_ID=244886619
WOSLAND_API_KEY=serioyun_gift_api_key_2024
WOSLAND_BACKUP_API_KEY=serioyun_backup_api_key_2024
WOSLAND_BACKUP_API_URL=https://wosland.com/apidc/backup_api/backup_api.php
```

### Anderungen

| Datei | Zeilen | Anderung |
|-------|--------|----------|
| `main.py` | oben + 249-257 | `load_dotenv()` hinzufugen, Token via `os.getenv('BOT_TOKEN')` statt `bot_token.txt` lesen |
| `cogs/config.py` | 5, 20, 24 | `WOS_ENCRYPT_KEY = os.getenv("WOS_ENCRYPT_KEY", "tB87#kPtkxqOS2")` etc. mit Fallback-Defaults |
| `cogs/backup_operations.py` | 21-22 | `self.api_url = os.getenv(...)`, `self.api_key = os.getenv(...)` |

### Loschen (nach Bestatigung)

- `bot_token.txt`

### Verifizierung

```bash
# 1. Syntax-Check
python -m py_compile main.py
python -m py_compile cogs/config.py
python -m py_compile cogs/backup_operations.py

# 2. Import-Check
python -c "from cogs.config import WOS_ENCRYPT_KEY, WOSLAND_API_KEY; print('OK')"

# 3. Bot-Start-Check (mit .env)
python main.py  # Muss fehlerfrei starten, alle Cogs laden

# 4. Funktionstest
# - Gift Code Einloesung testen (beweist WOS_ENCRYPT_KEY geladen)
# - Backup testen (beweist WOSLAND_BACKUP_API_KEY geladen)

# 5. Fallback-Test (ohne .env)
# - .env temporaer umbenennen, Bot starten -> Defaults muessen greifen
```

### Parallelisierung

Keine sinnvoll — nur 3 Dateien betroffen, Aenderungen sind minimal.

---

## Step 2: Shared DB Layer

**Ziel:** 135x `sqlite3.connect()` durch zentralen Connection Manager ersetzen.
**Risiko:** MITTEL
**Status:** [x] ERLEDIGT

### Neue Datei

**`cogs/database.py`** (~80-100 Zeilen)

```python
class DatabaseManager:
    """Zentraler SQLite Connection Manager (Singleton).

    Verwendung:
        db = DatabaseManager.instance()
        conn = db.get("settings")  # -> Connection zu db/settings.sqlite
    """
```

**Registry der 8 Datenbanken:**

```
"alliance"   -> db/alliance.sqlite
"giftcode"   -> db/giftcode.sqlite
"settings"   -> db/settings.sqlite
"users"      -> db/users.sqlite
"changes"    -> db/changes.sqlite
"beartime"   -> db/beartime.sqlite
"backup"     -> db/backup.sqlite
"id_channel" -> db/id_channel.sqlite
```

**Features:**
- Alle Connections mit WAL-Mode, `timeout=30`
- Thread-Safe via `threading.Lock`
- `get(name)` gibt gecachte Connection zuruck (erstellt bei erstem Zugriff)
- `close_all()` fur Shutdown
- `close()` auf managed Connections ist No-Op (verhindert versehentliches Schliessen)

### Anderungen in main.py

| Zeilen | Anderung |
|--------|----------|
| nach 259 | `db = DatabaseManager.instance()` instanziieren |
| 263-276 | `databases` Dict und Connection-Loop durch `db.get()` ersetzen |
| 279-343 | `create_tables` nutzt `db.get("changes")`, `db.get("settings")` etc. |
| 346-348 | Connection-Close entfernen (Manager ubernimmt Lifecycle) |
| nach bot init | `bot.db = db` setzen |
| main() Ende | `db.close_all()` nach `bot.start()` |

### Cog-Migration (Reihenfolge nach Risiko, niedrigstes zuerst)

Fur jedes Cog:
1. `from .database import DatabaseManager` importieren
2. `sqlite3.connect('db/X.sqlite')` -> `DatabaseManager.instance().get("X")`
3. Doppelte WAL-Pragma Calls entfernen
4. `cog_unload` Connection-Close entfernen (Manager besitzt Lifecycle)

| # | Datei | Aktuelle connect() Calls | Besonderheiten |
|---|-------|--------------------------|----------------|
| 1 | `wel.py` | 6 inline `with` | Einfachstes Cog |
| 2 | `w.py` | 4 Connections | Klein, einfach |
| 3 | `changes.py` | 20 Connections | Viele inline `with` Statements |
| 4 | `logsystem.py` | 2 in `__init__` | Nutzt `check_same_thread=False` -> entfernen |
| 5 | `control.py` | 5 Connections | Hat `db_lock`, pruefen ob noch noetig |
| 6 | `backup_operations.py` | 8 Connections | Mix aus persistent und inline |
| 7 | `bot_operations.py` | 2 in `__init__` | Nutzt `check_same_thread=False` -> entfernen, `__del__` -> entfernen |
| 8 | `id_channel.py` | 4+ Connections | Inline `with` Blocks |
| 9 | `gift_scraper.py` | 2 in `__init__` | Eigene settings_conn |
| 10 | `gift_operationsapi.py` | 3 in `__init__` | Fallt zuruck auf eigene Connection |
| 11 | `alliance_member_operations.py` | 4 Connections | Moderate Komplexitat |
| 12 | `alliance.py` | 4 in `__init__` | `__init__` bekommt aktuell `conn` Parameter -> entfernen |
| 13 | `gift_operations.py` | 3 persistent + inline | Groesste Datei, vorsichtig migrieren |
| 14 | `bear_trap.py` | 2 Connections | Zweitgroesste Datei |

### Spezial: alliance.py

`Alliance.__init__(bot, conn)` bekommt aktuell `conn` explizit aus main.py. Nach Migration:
- `conn` Parameter entfernen
- `__init__` holt sich Connection selbst via `DatabaseManager.instance().get("alliance")`
- `setup()` in main.py anpassen (kein conn mehr ubergeben)

### Verifizierung

```bash
# 1. Syntax-Check (nach jeder Cog-Migration)
python -m py_compile cogs/database.py
python -m py_compile cogs/<migriertes_cog>.py
python -m py_compile main.py

# 2. Import-Check
python -c "from cogs.database import DatabaseManager; db = DatabaseManager.instance(); print(db.get('settings')); db.close_all(); print('OK')"

# 3. Bot-Start-Check
python main.py  # Alle Cogs muessen laden, keine SQLite-Fehler

# 4. Pro migriertes Cog: Slash-Commands ausfuehren
# 5. Console auf "database is locked" Errors pruefen
# 6. Pruefen dass keine Connection versehentlich geschlossen wird
```

### Parallelisierung

**Gut parallelisierbar:** Cogs 1-3 (wel, w, changes) koennen gleichzeitig migriert werden. Ebenso Cogs 4-6 (logsystem, control, backup) und Cogs 7-9 (bot_operations, id_channel, gift_scraper). Die letzten 5 Cogs (10-14) haben Cross-Dependencies und sollten sequentiell migriert werden.

```
Agent 1: wel.py + changes.py        |  Agent 2: w.py + logsystem.py
                    ↓ Verifizierung
Agent 1: control.py + backup_ops    |  Agent 2: bot_operations + id_channel
                    ↓ Verifizierung
Agent 1: gift_scraper.py            |  Agent 2: gift_operationsapi.py
                    ↓ Verifizierung
Sequentiell: alliance_member_ops → alliance → gift_operations → bear_trap
                    ↓ Verifizierung nach jedem einzelnen
```

---

## Step 3: Utility Module

**Ziel:** Code-Duplikation eliminieren.
**Risiko:** MITTEL
**Status:** [x] ERLEDIGT

### Neue Datei

**`cogs/utils.py`** (~400-500 Zeilen)

### Was wird verschoben

#### 3a. `_create_monitored_task(coro, name=None)`

| Aktuelle Datei | Zeile |
|----------------|-------|
| `gift_operations.py` | 23-29 |
| `alliance_member_operations.py` | 19-25 |
| `gift_scraper.py` | 15-21 |
| `gift_operationsapi.py` | 16-22 |

Alle 4 Kopien byte-identisch. Verschieben nach `utils.py`, in allen 4 Dateien durch Import ersetzen.

#### 3b. Admin-Checks

**`check_admin(user_id: int) -> bool`** (pruft ob User irgendein Admin ist)

| Aktuelle Datei | Zeile | Semantik |
|----------------|-------|----------|
| `bear_trap.py` | 575-589 | Offnet eigene Connection, pruft `admin` Tabelle |
| `alliance_member_operations.py` | 1371-1383 | Offnet eigene Connection, pruft `admin` Tabelle |
| `gift_scraper.py` | 645-647 | Nutzt gespeicherten Cursor |

**`check_global_admin(user_id: int) -> bool`** (pruft `is_initial = 1`)

| Aktuelle Datei | Zeile | Semantik |
|----------------|-------|----------|
| `gift_operations.py` | 1256-1277 | Offnet eigene Connection, pruft `admin WHERE is_initial = 1` |
| `id_channel.py` | 370-383 | Offnet eigene Connection, pruft `admin WHERE is_initial` |

Beide nutzen `DatabaseManager.instance().get("settings")` statt eigener Connections.

#### 3c. `fix_rtl(text: str) -> str`

| Aktuelle Datei | Zeile |
|----------------|-------|
| `alliance_member_operations.py` | 74 |

RTL-Text-Fix Helper. Verschieben nach `utils.py`.

#### 3d. `PaginationView` Klasse

| Aktuelle Datei | Zeilen |
|----------------|--------|
| `alliance_member_operations.py` | 27-72 |

Generische UI-Komponente, nicht spezifisch fur Alliance-Member. Verschieben nach `utils.py`.

#### 3e. `AllianceSelectView` Klasse

| Aktuelle Datei | Zeilen |
|----------------|--------|
| `alliance_member_operations.py` | 1527-1844 |

Importiert von 4 Cogs:
- `gift_operations.py:13`
- `logsystem.py:5`
- `bot_operations.py:7`
- `changes.py:5`

Verschieben nach `utils.py`.

#### 3f. `PaginatedChannelView` Klasse

| Aktuelle Datei | Zeilen |
|----------------|--------|
| `alliance.py` | 1718-1795 |

Importiert von:
- `logsystem.py:6`
- `gift_operations.py:14`

Verschieben nach `utils.py`.

### Import-Updates

| Datei | Alter Import | Neuer Import |
|-------|-------------|-------------|
| `gift_operations.py:13` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |
| `gift_operations.py:14` | `from .alliance import PaginatedChannelView` | `from .utils import PaginatedChannelView` |
| `logsystem.py:5` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |
| `logsystem.py:6` | `from .alliance import PaginatedChannelView` | `from .utils import PaginatedChannelView` |
| `bot_operations.py:7` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |
| `changes.py:5` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |

### Ruckwartskompatibilitat

In den Original-Dateien Re-Exports beibehalten:

```python
# alliance_member_operations.py (temporar)
from .utils import AllianceSelectView, PaginationView, fix_rtl

# alliance.py (temporar)
from .utils import PaginatedChannelView
```

Diese Re-Exports nach Aktualisierung aller Imports entfernen.

### Verifizierung

```bash
# 1. Syntax-Check
python -m py_compile cogs/utils.py
# + alle geaenderten Dateien

# 2. Import-Check — KRITISCH: Alle Consumer muessen die verschobenen Klassen finden
python -c "from cogs.utils import _create_monitored_task, check_admin, check_global_admin; print('OK')"
python -c "from cogs.utils import PaginationView, AllianceSelectView, PaginatedChannelView; print('OK')"
python -c "from cogs.utils import fix_rtl; print('OK')"

# 3. Rueckwaertskompatibilitaet pruefen (Re-Exports)
python -c "from cogs.alliance_member_operations import AllianceSelectView; print('OK')"
python -c "from cogs.alliance import PaginatedChannelView; print('OK')"

# 4. Bot-Start-Check
python main.py  # Alle Cogs muessen laden

# 5. Funktionstest
# - check_admin: Als Admin UND als Nicht-Admin einen Command testen
# - PaginationView: Paginierte Liste oeffnen, blaettern
# - AllianceSelectView: In Gift Operations, Log System, Bot Operations, Changes testen
# - PaginatedChannelView: Channel-Auswahl in Gift Operations und Log System testen
```

### Parallelisierung

**Teilweise parallelisierbar:**
- Agent 1: `utils.py` erstellen + `_create_monitored_task` verschieben (3a)
- Agent 2: Admin-Check Funktionen vorbereiten (3b, haengt von DB Layer ab)
- Nach Verifizierung von 3a+3b:
- Agent 1: `PaginationView` + `fix_rtl` verschieben (3c+3d)
- Agent 2: `AllianceSelectView` + `PaginatedChannelView` verschieben (3e+3f)

---

## Step 4: Python `logging`

**Ziel:** 268x `print()` + Colorama durch strukturiertes Logging ersetzen.
**Risiko:** NIEDRIG
**Status:** [x] ERLEDIGT

### Neue Datei

**`cogs/log_config.py`** (~40-50 Zeilen)

```python
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    """Logging-Konfiguration: Console + rotierende Datei."""
    # Root Logger
    # StreamHandler mit farbigem Formatter
    # RotatingFileHandler -> log/bot.log (5MB, 3 Backups)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"wosbot.{name}")
```

### Migrationsmuster

```python
# VORHER:
print(f"[SCRAPER] New code found: {code}")
print(Fore.RED + f"Error: {e}" + Style.RESET_ALL)
traceback.print_exc()

# NACHHER:
logger = get_logger("gift_scraper")
logger.info(f"New code found: {code}")
logger.error(f"Error: {e}")
logger.exception(f"Error: {e}")  # inkl. Traceback
```

### Migration pro Datei (Reihenfolge nach Risiko)

| # | Datei | print() Calls | Besonderheiten |
|---|-------|---------------|----------------|
| 1 | `main.py` | ~25 | Schwere Colorama-Nutzung (Fore.GREEN, Fore.RED etc.) |
| 2 | `olddb.py` | ~5 | Klein, einfach |
| 3 | `w.py` | ~2 | Minimal |
| 4 | `wel.py` | ~3 | Minimal |
| 5 | `logsystem.py` | ~3 | Klein |
| 6 | `backup_operations.py` | ~10 | Mix aus print und Datei-Logging |
| 7 | `changes.py` | ~5 | Moderat |
| 8 | `id_channel.py` | ~5 | Hat eigenes File-Logging (`id_channel_log.txt`) |
| 9 | `gift_operationsapi.py` | ~5 | API-Logging |
| 10 | `gift_scraper.py` | ~10 | [SCRAPER] Prefix Pattern |
| 11 | `control.py` | ~15 | Schwere Colorama-Nutzung |
| 12 | `alliance_member_operations.py` | ~10 | Moderat |
| 13 | `bear_trap.py` | ~20 | Viele traceback.print_exc() |
| 14 | `gift_operations.py` | ~30 | Meiste Prints, komplexeste Datei |

### Manuelles File-Logging ersetzen

| Aktuell | Ersetzen durch |
|---------|---------------|
| `log/giftlog.txt` (gift_operations.py) | Beibehalten als dediziertes Gift-Log, aber via `logging.FileHandler` |
| `log/backuplog.txt` (backup_operations.py) | Via Logger mit FileHandler |
| `id_channel_log.txt` (id_channel.py) | Via Logger mit FileHandler |

### Colorama entfernen

Nach vollstandiger Migration aus folgenden Dateien entfernen:
- `main.py` (import und Verwendung)
- `control.py` (import und Verwendung)

### Verifizierung

```bash
# 1. Syntax-Check (nach jeder Datei)
python -m py_compile cogs/log_config.py
python -m py_compile cogs/<migrierte_datei>.py

# 2. Import-Check
python -c "from cogs.log_config import get_logger; logger = get_logger('test'); logger.info('test'); print('OK')"

# 3. Bot-Start-Check
python main.py  # Console-Output muss formatiert erscheinen

# 4. Verwaiste print() suchen
grep -rn "print(" cogs/*.py main.py --include="*.py" | grep -v "venv" | grep -v "__pycache__"
# Ergebnis: 0 verbleibende print() Calls (ausser in externen Libs)

# 5. Log-Datei pruefen
ls -la log/bot.log  # Muss existieren und beschrieben werden
```

### Parallelisierung

**Sehr gut parallelisierbar:** Jede Datei ist unabhaengig migrierbar.

```
Agent 1: main.py + olddb.py + w.py + wel.py
Agent 2: logsystem.py + backup_operations.py + changes.py + id_channel.py
Agent 3: gift_operationsapi.py + gift_scraper.py + control.py
                    ↓ Verifizierung
Agent 1: alliance_member_operations.py + bear_trap.py
Agent 2: gift_operations.py
                    ↓ Verifizierung
```

---

## Step 5: Menustruktur fixen

**Ziel:** Konsistente, fehlerfreie Navigation ohne Dead-Ends.
**Risiko:** NIEDRIG-MITTEL
**Status:** [x] ERLEDIGT

### 5a. Dead-Ends beheben (fehlende Zuruck-Buttons)

| Menu | Problem | Losung | Datei | Klasse |
|------|---------|--------|-------|--------|
| Bear Trap | Kein Main Menu, kein Back | + `main_menu` Button + `back_other_features` Button | `bear_trap.py` | `BearTrapView` (Zeile 1748) |
| ID Channel | Kein Main Menu, kein Back | + `main_menu` Button + `back_other_features` Button | `id_channel.py` | `IDChannelView` (Zeile 423) |
| Gift Operations | Kein Main Menu | + `main_menu` Button | `gift_operations.py` | `GiftView` (Zeile 2481) |
| Gift Scraper | Hat Main Menu via Alliance Cog, inkonsistent | Konsistenten `main_menu` Handler | `gift_scraper.py` | `ScraperView` (Zeile 784) |

**Navigationsschema fur ALLE Submenus:**
```
Jedes Submenu bekommt:
Row letzte:
  ├── ◀️ Back (ButtonStyle.secondary) -> zuruck zum Eltern-Menu
  └── 🏠 Main Menu (ButtonStyle.secondary) -> zuruck zu /settings
```

**Hierarchie:**
```
/settings (Main Menu)
├── Alliance Operations      -> Back = Main Menu
├── Member Operations        -> Back = Main Menu
├── Bot Operations           -> Back = Main Menu
├── Gift Operations          -> Back = Main Menu
├── Alliance History         -> Back = Main Menu
├── Support Operations       -> Back = Main Menu
├── Other Features           -> Back = Main Menu
│   ├── Bear Trap            -> Back = Other Features
│   ├── ID Channel           -> Back = Other Features
│   └── Backup System        -> Back = Other Features (hat schon Main Menu)
└── Gift Scraper             -> Back = Main Menu
```

### 5b. Button-Styles standardisieren

**Verbindliches Style-Schema:**

| Aktion | Emoji | ButtonStyle | Beispiel |
|--------|-------|-------------|----------|
| Erstellen/Hinzufugen | ➕ | `.success` | Add Alliance, Add Admin, Create Gift Code |
| Loschen/Entfernen | 🗑️ | `.danger` | Delete Alliance, Remove Admin, Delete Gift Code |
| Bearbeiten | ✏️ | `.primary` | Edit Alliance |
| Anzeigen/Listen | 📋 | `.primary` | View Alliances, List Gift Codes, View Admins |
| Ausfuhren/Starten | ▶️ | `.success` | Use Gift Code, Run Scraper, Create Backup |
| Einstellungen/Config | ⚙️ | `.secondary` | Auto Gift Settings, Toggle Sources |
| Navigation zuruck | 🏠 / ◀️ | `.secondary` | Main Menu, Back |
| Suchen | 🔍 | `.primary` | Check Alliance, Search FID |

**Betroffene Views mit ihren Korrekturen:**

| Datei | Klasse | Button | Aktuell | Soll |
|-------|--------|--------|---------|------|
| `gift_operations.py` | `GiftView` | Create Gift Code | 🎫 `.green` | ➕ `.success` |
| `gift_operations.py` | `GiftView` | List Gift Codes | 📋 `.blurple` | 📋 `.primary` |
| `gift_operations.py` | `GiftView` | Auto Gift Settings | ⚙️ `.grey` | ⚙️ `.secondary` |
| `gift_operations.py` | `GiftView` | Delete Gift Code | ❌ `.danger` | 🗑️ `.danger` |
| `gift_operations.py` | `GiftView` | Gift Code Channel | 📢 `.primary` | ⚙️ `.secondary` |
| `gift_operations.py` | `GiftView` | Delete Gift Channel | 🗑️ `.danger` | 🗑️ `.danger` (OK) |
| `gift_operations.py` | `GiftView` | Use Gift Alliance | 🎯 `.primary` | ▶️ `.success` |
| `backup_operations.py` | `BackupView` | Create Backup | 💾 `.primary` | ▶️ `.success` |
| `backup_operations.py` | `BackupView` | Create/Change Password | 🔐 `.primary` | ⚙️ `.secondary` |
| `bot_operations.py` | Bot Operations | View Admin Permissions (Label sagt "Delete") | `.danger` | Label korrigieren oder Button aufteilen |

### 5c. Custom-IDs prefixen (Konflikt-Vermeidung)

**Problem:** `main_menu` wird in 5+ Views mit identischem custom_id verwendet.

**Losung:** Jede View bekommt geprefixte IDs:

| Alter custom_id | Neuer custom_id | View |
|----------------|-----------------|------|
| `main_menu` | `alliance_main_menu` | Alliance Operations View |
| `main_menu` | `member_main_menu` | Member Operations View |
| `main_menu` | `bot_main_menu` | Bot Operations View |
| `main_menu` | `gift_main_menu` | Gift Operations View |
| `main_menu` | `other_main_menu` | Other Features View |
| `main_menu` | `backup_main_menu` | Backup View |
| `main_menu` | `bear_trap_main_menu` | Bear Trap View (NEU) |
| `main_menu` | `id_channel_main_menu` | ID Channel View (NEU) |
| `main_menu` | `scraper_main_menu` | Scraper View (existiert bereits korrekt) |

**Pagination IDs:**

| Alter custom_id | Neuer custom_id |
|----------------|-----------------|
| `next` / `previous` | `{context}_next` / `{context}_previous` |
| `next_nick` / `previous_nick` | `history_nick_next` / `history_nick_previous` |

**Handler-Anpassung:** Alle `on_interaction()` Listener die auf `custom_id == "main_menu"` prufen mussen auf den neuen prefixed ID prufen. Alternativ: mit `.startswith()` oder `.endswith("_main_menu")` matchen.

### 5d. Fehlendes Error-Feedback

| Datei | Zeile | Problem | Losung |
|-------|-------|---------|--------|
| `alliance.py` | 1422 | Stummes `pass` bei Interaction-Fehlern | Ephemeral Error-Embed senden |
| `bear_trap.py` | diverse | Traceback wird geprinted, User sieht nichts | Ephemeral "Ein Fehler ist aufgetreten" senden |
| `gift_operations.py` | diverse | Manche Fehler werden verschluckt | Konsistentes Error-Embed Pattern |

**Standard Error-Response Pattern:**

```python
# In utils.py:
async def send_error(interaction, message="Ein Fehler ist aufgetreten."):
    embed = discord.Embed(
        title="Error",
        description=message,
        color=discord.Color.red()
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:
        pass
```

### 5e. Embed-Farben standardisieren

| Typ | Farbe | Verwendung |
|-----|-------|-----------|
| Menu/Navigation | `discord.Color.blue()` | Alle Submenu-Embeds |
| Erfolg | `discord.Color.green()` | Erfolgreiche Aktionen |
| Fehler | `discord.Color.red()` | Fehler und Unauthorized |
| Warnung | `discord.Color.orange()` | Warnungen, teilweiser Erfolg |
| Info/Fortschritt | `discord.Color.blue()` | Laufende Prozesse |
| Scraper/Features | `discord.Color.teal()` | Kann bleiben als Akzent |

### Verifizierung

```bash
# 1. Syntax-Check
python -m py_compile cogs/bear_trap.py
python -m py_compile cogs/id_channel.py
python -m py_compile cogs/gift_operations.py
python -m py_compile cogs/gift_scraper.py
python -m py_compile cogs/alliance.py
python -m py_compile cogs/backup_operations.py
python -m py_compile cogs/bot_operations.py
python -m py_compile cogs/other_features.py

# 2. Bot-Start-Check
python main.py  # Alle Cogs muessen laden

# 3. Custom-ID Duplikat-Check
grep -rn "custom_id=" cogs/*.py | grep -v "venv" | sort -t'"' -k2 | uniq -d -f1
# Ergebnis: Keine doppelten custom_ids mehr

# 4. Navigationspfad-Test (MANUELL im Discord - PFLICHT)
# Pfad 1: /settings → Alliance Operations → Back (🏠) → Main Menu
# Pfad 2: /settings → Member Operations → Back (🏠) → Main Menu
# Pfad 3: /settings → Bot Operations → Back (🏠) → Main Menu
# Pfad 4: /settings → Gift Operations → Back (🏠) → Main Menu
# Pfad 5: /settings → Alliance History → Back (🏠) → Main Menu
# Pfad 6: /settings → Other Features → Bear Trap → Back (◀️) → Other Features → Back (🏠) → Main Menu
# Pfad 7: /settings → Other Features → ID Channel → Back (◀️) → Other Features → Back (🏠) → Main Menu
# Pfad 8: /settings → Other Features → Backup System → Back (◀️) → Other Features → Back (🏠) → Main Menu
# Pfad 9: /settings → Gift Scraper → Back (🏠) → Main Menu
# Pfad 10: Jeden Button in jedem Submenu klicken → Kein Error, kein Dead-End
```

### Parallelisierung

**Gut parallelisierbar:** Die betroffenen Views sind in unterschiedlichen Dateien.

```
Agent 1: Dead-End Fixes (bear_trap.py, id_channel.py)
Agent 2: Button-Styles + Emojis (gift_operations.py, backup_operations.py, bot_operations.py)
Agent 3: Custom-ID Prefixing (alliance.py, andere Views)
                    ↓ Verifizierung
Agent 1: Error-Feedback Pattern (utils.py + alle Cogs)
Agent 2: Embed-Farben standardisieren
                    ↓ Verifizierung
```

---

## Step 6: Grosse Cogs aufteilen

**Ziel:** Dateien unter ~500-600 Zeilen halten.
**Risiko:** HOCH
**Status:** [x] ERLEDIGT

**Wichtig:** Split-Dateien sind KEINE eigenen Cogs, sondern Module die vom Parent-Cog importiert werden. `load_cogs` in main.py muss NICHT geandert werden.

### 6A: `gift_operations.py` (2871 Zeilen -> 4 Dateien)

| Neue Datei | Inhalt | Zeilen aus Original | ~Grosse |
|-----------|--------|--------------------|---------|
| `gift_operations.py` | Cog-Klasse, __init__, encode_data, solve_captcha, get_stove_info_wos, claim_giftcode_rewards_wos, retry_missing_codes | ~1-500 | ~500 |
| `gift_views.py` | GiftView, RetryFailedView, CreateGiftCodeModal, DeleteGiftCodeModal | ~2266-2871 | ~600 |
| `gift_distribution.py` | use_giftcode_for_alliance, Auto-Gift-Logik, setup_giftcode_auto | ~1700-2265 | ~600 |
| `gift_channel.py` | setup_gift_channel, delete_gift_channel, check_channels_loop | ~600-800 | ~200 |

**Import-Kette:**
- `gift_operations.py` importiert `gift_views.py` fur View-Klassen
- `gift_views.py` bekommt Cog-Referenz via `__init__(self, cog)` (bestehender Pattern)
- `gift_distribution.py` importiert aus `gift_operations.py` fur API-Methoden
- Zirkulare Imports vermeiden via `TYPE_CHECKING`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .gift_operations import GiftOperations
```

### 6B: `bear_trap.py` (2569 Zeilen -> 4 Dateien)

| Neue Datei | Inhalt | Zeilen aus Original | ~Grosse |
|-----------|--------|--------------------|---------|
| `bear_trap.py` | Cog-Klasse, DB-Setup, Notification-Loop, check_admin entfernt (-> utils) | 12-625 | ~625 |
| `bear_trap_views.py` | BearTrapView (Hauptmenu), ChannelSelectView, ChannelSelectMenu, ImportEmbedModal | 1748-2569 | ~850 |
| `bear_trap_modals.py` | RepeatOptionView, RepeatIntervalModal, TextInputModal, TimeSelectModal, NotificationTypeView, CustomTimesModal, MentionTypeView, MentionSelectMenu, MessageTypeView | 626-1747 | ~1100 |
| `bear_trap_embed.py` | EmbedEditorView (eigenstandig, gross, in sich geschlossen) | 864-1156 | ~300 |

Alle View/Modal-Klassen bekommen `cog` als Constructor-Parameter (bestehender Pattern).

### 6C: `alliance_member_operations.py` (1844 -> ~1310 nach Step 3)

Nach Step 3 schrumpft die Datei um ~534 Zeilen (PaginationView: ~45, _create_monitored_task: ~7, fix_rtl: ~2, AllianceSelectView: ~317, Re-Exports: ~163).

~1310 Zeilen ist akzeptabel. Optional weiter aufteilen:

| Neue Datei | Inhalt | ~Grosse |
|-----------|--------|---------|
| `alliance_member_operations.py` | Cog-Klasse (Member Add/Remove/List/Search) | ~900 |
| `alliance_member_views.py` | Verbleibende View-Klassen spezifisch fur Member-Ops | ~400 |

### 6D: `alliance.py` (1795 -> ~1718 nach Step 3)

Nach Step 3 (PaginatedChannelView verschoben) optional aufteilen:

| Neue Datei | Inhalt | ~Grosse |
|-----------|--------|---------|
| `alliance.py` | Cog-Klasse (CRUD, Settings, on_interaction) | ~1200 |
| `alliance_views.py` | Alliance-spezifische View-Klassen | ~500 |

### Risikovermeidung bei Splits

1. **Eine Datei nach der anderen** aufteilen
2. **View/Modal-Klassen zuerst** verschieben (sind eigenstandig, referenzieren nur `self.cog`)
3. **Ruckwartskompatible Re-Exports** in der Original-Datei beibehalten
4. **Zirkulare Imports** via `TYPE_CHECKING` vermeiden
5. **Nach jedem Split:** Alle Commands des betroffenen Cogs testen

### Verifizierung (pro Split einzeln!)

```bash
# 1. Syntax-Check (ALLE neuen + geaenderten Dateien)
python -m py_compile cogs/gift_operations.py
python -m py_compile cogs/gift_views.py
python -m py_compile cogs/gift_distribution.py
python -m py_compile cogs/gift_channel.py
# (analog fuer bear_trap Splits)

# 2. Import-Check — Keine zirkulaeren Imports
python -c "from cogs.gift_views import GiftView; print('OK')"
python -c "from cogs.gift_distribution import *; print('OK')"
python -c "from cogs.bear_trap_views import BearTrapView; print('OK')"
python -c "from cogs.bear_trap_modals import TimeSelectModal; print('OK')"

# 3. Bot-Start-Check
python main.py  # Alle Cogs muessen laden, keine ImportErrors

# 4. Funktionstest pro gesplittetem Cog (MANUELL im Discord - PFLICHT)
# gift_operations: Create, List, Delete Gift Code, Use for Alliance, Auto Gift, Gift Channel
# bear_trap: Set Time (Discord + Web), Remove, View, Toggle Notifications
# alliance_member: Add, Remove, View, Transfer Member
# alliance: Add, Edit, Delete, View, Check Alliance

# 5. Pruefen dass keine Funktionalitaet verloren ging
# Vorher: Anzahl Slash-Commands + Buttons notieren
# Nachher: Gleiche Anzahl vorhanden
```

### Parallelisierung

**Eingeschraenkt parallelisierbar:** Splits muessen nacheinander verifiziert werden, da sie sich gegenseitig beeinflussen koennten. Innerhalb eines Splits koennen aber neue Dateien parallel erstellt werden.

```
Agent 1: gift_views.py erstellen      |  Agent 2: gift_distribution.py erstellen
                    ↓ Verifizierung gift_operations Split
Agent 1: bear_trap_views.py erstellen  |  Agent 2: bear_trap_modals.py + bear_trap_embed.py
                    ↓ Verifizierung bear_trap Split
Sequentiell: alliance_member_operations → alliance (optional)
                    ↓ Verifizierung
```

---

## Zusammenfassung

| Step | Neue Dateien | Geanderte Dateien | Risiko | Aufwand |
|------|-------------|-------------------|--------|---------|
| 1. Secrets/.env | 3 | 3 | Niedrig | Klein |
| 2. DB Layer | 1 | 15 (alle Cogs + main) | Mittel | Gross |
| 3. Utils | 1 | 10 | Mittel | Mittel |
| 4. Logging | 1 | 15 (alle .py) | Niedrig | Mittel |
| 5. Menustruktur | 0 | 8 (View-Klassen) | Niedrig-Mittel | Mittel |
| 6. Cog-Splits | 8 | 4 | Hoch | Gross |
| **Total** | **14 neue** | **alle geandert** | | |

### Neue Dateien nach Abschluss

```
cogs/
├── database.py          (Step 2 - DB Manager)
├── utils.py             (Step 3 - Shared Utilities)
├── log_config.py        (Step 4 - Logging Setup)
├── gift_views.py        (Step 6A - Gift UI)
├── gift_distribution.py (Step 6A - Gift Verteilung)
├── gift_channel.py      (Step 6A - Gift Channel)
├── bear_trap_views.py   (Step 6B - Bear Trap UI)
├── bear_trap_modals.py  (Step 6B - Bear Trap Modals)
├── bear_trap_embed.py   (Step 6B - Embed Editor)
├── ... (bestehende Dateien, verkleinert)
.env                     (Step 1 - Secrets)
.env.example             (Step 1 - Template)
.gitignore               (Step 1 - Schutz)
```
