# WOS Bot - Refactoring Plan

## Status: COMPLETED

---

## Implementation Strategy

### Parallel Agents

The implementation should ideally be carried out with **parallel agents** to speed up the work:

- **Process independent files in parallel:** For example, in Step 2 (DB Layer), multiple cogs can be migrated simultaneously, as long as they have no mutual imports.
- **Separate research and implementation:** One agent inspects the current state, another applies the changes.
- **Step 4 (Logging)** is particularly well suited for parallelization: each file can be migrated independently.
- **Step 5 (Menu structure)** can partially run in parallel with Step 4, since different files are affected.

### Mandatory: Verification after every step

**CRITICAL:** After completing each step, a verification MUST take place before the next step is started.

#### Verification checklist per step

**Automatic checks (after EVERY step):**

1. **Syntax check:** `python -m py_compile` for every changed file
2. **Import check:** `python -c "from cogs import <module>"` for every affected module
3. **Bot start check:** Bot must start without errors (`python main.py` - check that all cogs load)

**Step-specific checks:**

| Step | Additional verification |
|------|---------------------------|
| 1. Secrets | Bot starts WITH `.env` AND with fallback defaults (without `.env`) |
| 2. DB Layer | Test each migrated cog individually: run commands, check console for SQLite errors |
| 3. Utils | All imports resolvable, all relocated classes/functions reachable from all consumers |
| 4. Logging | Console output present and correctly formatted, no orphaned `print()` calls |
| 5. Menu structure | Click through EVERY menu path: Main -> Sub -> Back -> Main. No dead ends |
| 6. Cog splits | All commands, buttons, modals and pagination of the split cog work |

**Workflow:**

```
Step N implementation (ideally with parallel agents)
         |
         v
Step N verification (automatic + manual checks)
         |
    [PASS] ──→ Continue to Step N+1
         |
    [FAIL] ──→ Fix the error, verify again
```

**IMPORTANT:**
- No step may be started while the previous one is not fully verified
- If a verification fails: analyze and fix the cause, do NOT start the next step
- After the fix: repeat verification of the entire step (not just the fix)
- The bot must remain fully functional after EVERY step (no big-bang rewrite)

---

## Starting point

- **20 Python files**, ~17,271 lines of code
- **8 SQLite databases**, 135x `sqlite3.connect()` scattered around
- **268x `print()`** instead of structured logging
- **4x identical** `_create_monitored_task()` copies
- **6x separate** admin check implementations
- **Hardcoded secrets** in config.py and backup_operations.py
- **Menu structure** with dead ends, inconsistent styles and missing back buttons

---

## Dependencies / Order

```
Step 1 (Secrets/.env)
   ↓
Step 2 (Shared DB Layer)  ───→  Step 3 (Utility Module)
                                     ↓
                                Step 4 (Python Logging)
                                     ↓
                                Step 5 (Menu structure)
                                     ↓
                                Step 6 (Cog splits)
```

---

## Step 1: Secrets in `.env`

**Goal:** Remove all hardcoded secrets from the source code.
**Risk:** LOW
**Status:** [x] DONE

### New files

| File | Purpose |
|-------|-------|
| `.env` | Contains all secrets as environment variables |
| `.env.example` | Template with placeholders (will be committed) |
| `.gitignore` | Protects `.env`, `bot_token.txt`, `db/`, `__pycache__/`, `log/` |

### `.env` contents

```
BOT_TOKEN=<value from bot_token.txt>
WOS_ENCRYPT_KEY=tB87#kPtkxqOS2
WOS_TEST_PLAYER_ID=244886619
WOSLAND_API_KEY=serioyun_gift_api_key_2024
WOSLAND_BACKUP_API_KEY=serioyun_backup_api_key_2024
WOSLAND_BACKUP_API_URL=https://wosland.com/apidc/backup_api/backup_api.php
```

### Changes

| File | Lines | Change |
|-------|--------|----------|
| `main.py` | top + 249-257 | Add `load_dotenv()`, read token via `os.getenv('BOT_TOKEN')` instead of `bot_token.txt` |
| `cogs/config.py` | 5, 20, 24 | `WOS_ENCRYPT_KEY = os.getenv("WOS_ENCRYPT_KEY", "tB87#kPtkxqOS2")` etc. with fallback defaults |
| `cogs/backup_operations.py` | 21-22 | `self.api_url = os.getenv(...)`, `self.api_key = os.getenv(...)` |

### Delete (after confirmation)

- `bot_token.txt`

### Verification

```bash
# 1. Syntax check
python -m py_compile main.py
python -m py_compile cogs/config.py
python -m py_compile cogs/backup_operations.py

# 2. Import check
python -c "from cogs.config import WOS_ENCRYPT_KEY, WOSLAND_API_KEY; print('OK')"

# 3. Bot start check (with .env)
python main.py  # Must start without errors, all cogs must load

# 4. Functional test
# - Test gift code redemption (proves WOS_ENCRYPT_KEY is loaded)
# - Test backup (proves WOSLAND_BACKUP_API_KEY is loaded)

# 5. Fallback test (without .env)
# - Temporarily rename .env, start bot -> defaults must take effect
```

### Parallelization

None makes sense — only 3 files are affected, changes are minimal.

---

## Step 2: Shared DB Layer

**Goal:** Replace 135x `sqlite3.connect()` with a centralized connection manager.
**Risk:** MEDIUM
**Status:** [x] DONE

### New file

**`cogs/database.py`** (~80-100 lines)

```python
class DatabaseManager:
    """Zentraler SQLite Connection Manager (Singleton).

    Verwendung:
        db = DatabaseManager.instance()
        conn = db.get("settings")  # -> Connection zu db/settings.sqlite
    """
```

**Registry of the 8 databases:**

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
- All connections with WAL mode, `timeout=30`
- Thread-safe via `threading.Lock`
- `get(name)` returns a cached connection (created on first access)
- `close_all()` for shutdown
- `close()` on managed connections is a no-op (prevents accidental closing)

### Changes in main.py

| Lines | Change |
|--------|----------|
| after 259 | Instantiate `db = DatabaseManager.instance()` |
| 263-276 | Replace `databases` dict and connection loop with `db.get()` |
| 279-343 | `create_tables` uses `db.get("changes")`, `db.get("settings")` etc. |
| 346-348 | Remove connection close (manager handles lifecycle) |
| after bot init | Set `bot.db = db` |
| end of main() | `db.close_all()` after `bot.start()` |

### Cog migration (order by risk, lowest first)

For each cog:
1. Import `from .database import DatabaseManager`
2. `sqlite3.connect('db/X.sqlite')` -> `DatabaseManager.instance().get("X")`
3. Remove duplicate WAL pragma calls
4. Remove `cog_unload` connection close (manager owns lifecycle)

| # | File | Current connect() calls | Notes |
|---|-------|--------------------------|----------------|
| 1 | `wel.py` | 6 inline `with` | Simplest cog |
| 2 | `w.py` | 4 connections | Small, simple |
| 3 | `changes.py` | 20 connections | Many inline `with` statements |
| 4 | `logsystem.py` | 2 in `__init__` | Uses `check_same_thread=False` -> remove |
| 5 | `control.py` | 5 connections | Has `db_lock`, check whether still needed |
| 6 | `backup_operations.py` | 8 connections | Mix of persistent and inline |
| 7 | `bot_operations.py` | 2 in `__init__` | Uses `check_same_thread=False` -> remove, `__del__` -> remove |
| 8 | `id_channel.py` | 4+ connections | Inline `with` blocks |
| 9 | `gift_scraper.py` | 2 in `__init__` | Own settings_conn |
| 10 | `gift_operationsapi.py` | 3 in `__init__` | Falls back to its own connection |
| 11 | `alliance_member_operations.py` | 4 connections | Moderate complexity |
| 12 | `alliance.py` | 4 in `__init__` | `__init__` currently receives `conn` parameter -> remove |
| 13 | `gift_operations.py` | 3 persistent + inline | Largest file, migrate carefully |
| 14 | `bear_trap.py` | 2 connections | Second largest file |

### Special: alliance.py

`Alliance.__init__(bot, conn)` currently receives `conn` explicitly from main.py. After migration:
- Remove `conn` parameter
- `__init__` fetches the connection itself via `DatabaseManager.instance().get("alliance")`
- Adapt `setup()` in main.py (no longer pass conn)

### Verification

```bash
# 1. Syntax check (after each cog migration)
python -m py_compile cogs/database.py
python -m py_compile cogs/<migrated_cog>.py
python -m py_compile main.py

# 2. Import check
python -c "from cogs.database import DatabaseManager; db = DatabaseManager.instance(); print(db.get('settings')); db.close_all(); print('OK')"

# 3. Bot start check
python main.py  # All cogs must load, no SQLite errors

# 4. Per migrated cog: run slash commands
# 5. Check console for "database is locked" errors
# 6. Verify that no connection is accidentally closed
```

### Parallelization

**Well parallelizable:** Cogs 1-3 (wel, w, changes) can be migrated simultaneously. Likewise cogs 4-6 (logsystem, control, backup) and cogs 7-9 (bot_operations, id_channel, gift_scraper). The last 5 cogs (10-14) have cross-dependencies and should be migrated sequentially.

```
Agent 1: wel.py + changes.py        |  Agent 2: w.py + logsystem.py
                    ↓ Verification
Agent 1: control.py + backup_ops    |  Agent 2: bot_operations + id_channel
                    ↓ Verification
Agent 1: gift_scraper.py            |  Agent 2: gift_operationsapi.py
                    ↓ Verification
Sequential: alliance_member_ops → alliance → gift_operations → bear_trap
                    ↓ Verification after each individual one
```

---

## Step 3: Utility Module

**Goal:** Eliminate code duplication.
**Risk:** MEDIUM
**Status:** [x] DONE

### New file

**`cogs/utils.py`** (~400-500 lines)

### What gets relocated

#### 3a. `_create_monitored_task(coro, name=None)`

| Current file | Line |
|----------------|-------|
| `gift_operations.py` | 23-29 |
| `alliance_member_operations.py` | 19-25 |
| `gift_scraper.py` | 15-21 |
| `gift_operationsapi.py` | 16-22 |

All 4 copies are byte-identical. Move to `utils.py`, replace with import in all 4 files.

#### 3b. Admin checks

**`check_admin(user_id: int) -> bool`** (checks whether the user is any kind of admin)

| Current file | Line | Semantics |
|----------------|-------|----------|
| `bear_trap.py` | 575-589 | Opens its own connection, checks `admin` table |
| `alliance_member_operations.py` | 1371-1383 | Opens its own connection, checks `admin` table |
| `gift_scraper.py` | 645-647 | Uses stored cursor |

**`check_global_admin(user_id: int) -> bool`** (checks `is_initial = 1`)

| Current file | Line | Semantics |
|----------------|-------|----------|
| `gift_operations.py` | 1256-1277 | Opens its own connection, checks `admin WHERE is_initial = 1` |
| `id_channel.py` | 370-383 | Opens its own connection, checks `admin WHERE is_initial` |

Both use `DatabaseManager.instance().get("settings")` instead of their own connections.

#### 3c. `fix_rtl(text: str) -> str`

| Current file | Line |
|----------------|-------|
| `alliance_member_operations.py` | 74 |

RTL text fix helper. Move to `utils.py`.

#### 3d. `PaginationView` class

| Current file | Lines |
|----------------|--------|
| `alliance_member_operations.py` | 27-72 |

Generic UI component, not specific to alliance member. Move to `utils.py`.

#### 3e. `AllianceSelectView` class

| Current file | Lines |
|----------------|--------|
| `alliance_member_operations.py` | 1527-1844 |

Imported by 4 cogs:
- `gift_operations.py:13`
- `logsystem.py:5`
- `bot_operations.py:7`
- `changes.py:5`

Move to `utils.py`.

#### 3f. `PaginatedChannelView` class

| Current file | Lines |
|----------------|--------|
| `alliance.py` | 1718-1795 |

Imported by:
- `logsystem.py:6`
- `gift_operations.py:14`

Move to `utils.py`.

### Import updates

| File | Old import | New import |
|-------|-------------|-------------|
| `gift_operations.py:13` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |
| `gift_operations.py:14` | `from .alliance import PaginatedChannelView` | `from .utils import PaginatedChannelView` |
| `logsystem.py:5` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |
| `logsystem.py:6` | `from .alliance import PaginatedChannelView` | `from .utils import PaginatedChannelView` |
| `bot_operations.py:7` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |
| `changes.py:5` | `from .alliance_member_operations import AllianceSelectView` | `from .utils import AllianceSelectView` |

### Backwards compatibility

Keep re-exports in the original files:

```python
# alliance_member_operations.py (temporary)
from .utils import AllianceSelectView, PaginationView, fix_rtl

# alliance.py (temporary)
from .utils import PaginatedChannelView
```

Remove these re-exports after all imports have been updated.

### Verification

```bash
# 1. Syntax check
python -m py_compile cogs/utils.py
# + all changed files

# 2. Import check — CRITICAL: all consumers must find the relocated classes
python -c "from cogs.utils import _create_monitored_task, check_admin, check_global_admin; print('OK')"
python -c "from cogs.utils import PaginationView, AllianceSelectView, PaginatedChannelView; print('OK')"
python -c "from cogs.utils import fix_rtl; print('OK')"

# 3. Check backwards compatibility (re-exports)
python -c "from cogs.alliance_member_operations import AllianceSelectView; print('OK')"
python -c "from cogs.alliance import PaginatedChannelView; print('OK')"

# 4. Bot start check
python main.py  # All cogs must load

# 5. Functional test
# - check_admin: test a command both as admin AND as non-admin
# - PaginationView: open a paginated list, page through it
# - AllianceSelectView: test in Gift Operations, Log System, Bot Operations, Changes
# - PaginatedChannelView: test channel selection in Gift Operations and Log System
```

### Parallelization

**Partially parallelizable:**
- Agent 1: create `utils.py` + move `_create_monitored_task` (3a)
- Agent 2: prepare admin check functions (3b, depends on DB Layer)
- After verification of 3a+3b:
- Agent 1: move `PaginationView` + `fix_rtl` (3c+3d)
- Agent 2: move `AllianceSelectView` + `PaginatedChannelView` (3e+3f)

---

## Step 4: Python `logging`

**Goal:** Replace 268x `print()` + Colorama with structured logging.
**Risk:** LOW
**Status:** [x] DONE

### New file

**`cogs/log_config.py`** (~40-50 lines)

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

### Migration pattern

```python
# BEFORE:
print(f"[SCRAPER] New code found: {code}")
print(Fore.RED + f"Error: {e}" + Style.RESET_ALL)
traceback.print_exc()

# AFTER:
logger = get_logger("gift_scraper")
logger.info(f"New code found: {code}")
logger.error(f"Error: {e}")
logger.exception(f"Error: {e}")  # incl. traceback
```

### Migration per file (order by risk)

| # | File | print() calls | Notes |
|---|-------|---------------|----------------|
| 1 | `main.py` | ~25 | Heavy Colorama use (Fore.GREEN, Fore.RED etc.) |
| 2 | `olddb.py` | ~5 | Small, simple |
| 3 | `w.py` | ~2 | Minimal |
| 4 | `wel.py` | ~3 | Minimal |
| 5 | `logsystem.py` | ~3 | Small |
| 6 | `backup_operations.py` | ~10 | Mix of print and file logging |
| 7 | `changes.py` | ~5 | Moderate |
| 8 | `id_channel.py` | ~5 | Has its own file logging (`id_channel_log.txt`) |
| 9 | `gift_operationsapi.py` | ~5 | API logging |
| 10 | `gift_scraper.py` | ~10 | [SCRAPER] prefix pattern |
| 11 | `control.py` | ~15 | Heavy Colorama use |
| 12 | `alliance_member_operations.py` | ~10 | Moderate |
| 13 | `bear_trap.py` | ~20 | Many traceback.print_exc() |
| 14 | `gift_operations.py` | ~30 | Most prints, most complex file |

### Replace manual file logging

| Current | Replace with |
|---------|---------------|
| `log/giftlog.txt` (gift_operations.py) | Keep as a dedicated gift log, but via `logging.FileHandler` |
| `log/backuplog.txt` (backup_operations.py) | Via logger with FileHandler |
| `id_channel_log.txt` (id_channel.py) | Via logger with FileHandler |

### Remove Colorama

After full migration, remove from the following files:
- `main.py` (import and usage)
- `control.py` (import and usage)

### Verification

```bash
# 1. Syntax check (after each file)
python -m py_compile cogs/log_config.py
python -m py_compile cogs/<migrated_file>.py

# 2. Import check
python -c "from cogs.log_config import get_logger; logger = get_logger('test'); logger.info('test'); print('OK')"

# 3. Bot start check
python main.py  # Console output must appear formatted

# 4. Search for orphaned print()
grep -rn "print(" cogs/*.py main.py --include="*.py" | grep -v "venv" | grep -v "__pycache__"
# Result: 0 remaining print() calls (except in external libs)

# 5. Check log file
ls -la log/bot.log  # Must exist and be written to
```

### Parallelization

**Very well parallelizable:** each file can be migrated independently.

```
Agent 1: main.py + olddb.py + w.py + wel.py
Agent 2: logsystem.py + backup_operations.py + changes.py + id_channel.py
Agent 3: gift_operationsapi.py + gift_scraper.py + control.py
                    ↓ Verification
Agent 1: alliance_member_operations.py + bear_trap.py
Agent 2: gift_operations.py
                    ↓ Verification
```

---

## Step 5: Fix menu structure

**Goal:** Consistent, error-free navigation without dead ends.
**Risk:** LOW-MEDIUM
**Status:** [x] DONE

### 5a. Fix dead ends (missing back buttons)

| Menu | Problem | Solution | File | Class |
|------|---------|--------|-------|--------|
| Bear Trap | No main menu, no back | + `main_menu` button + `back_other_features` button | `bear_trap.py` | `BearTrapView` (line 1748) |
| ID Channel | No main menu, no back | + `main_menu` button + `back_other_features` button | `id_channel.py` | `IDChannelView` (line 423) |
| Gift Operations | No main menu | + `main_menu` button | `gift_operations.py` | `GiftView` (line 2481) |
| Gift Scraper | Has main menu via Alliance cog, inconsistent | Consistent `main_menu` handler | `gift_scraper.py` | `ScraperView` (line 784) |

**Navigation scheme for ALL submenus:**
```
Every submenu gets:
Last row:
  ├── ◀️ Back (ButtonStyle.secondary) -> back to parent menu
  └── 🏠 Main Menu (ButtonStyle.secondary) -> back to /settings
```

**Hierarchy:**
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
│   └── Backup System        -> Back = Other Features (already has Main Menu)
└── Gift Scraper             -> Back = Main Menu
```

### 5b. Standardize button styles

**Mandatory style scheme:**

| Action | Emoji | ButtonStyle | Example |
|--------|-------|-------------|----------|
| Create/Add | ➕ | `.success` | Add Alliance, Add Admin, Create Gift Code |
| Delete/Remove | 🗑️ | `.danger` | Delete Alliance, Remove Admin, Delete Gift Code |
| Edit | ✏️ | `.primary` | Edit Alliance |
| View/List | 📋 | `.primary` | View Alliances, List Gift Codes, View Admins |
| Execute/Start | ▶️ | `.success` | Use Gift Code, Run Scraper, Create Backup |
| Settings/Config | ⚙️ | `.secondary` | Auto Gift Settings, Toggle Sources |
| Navigate back | 🏠 / ◀️ | `.secondary` | Main Menu, Back |
| Search | 🔍 | `.primary` | Check Alliance, Search FID |

**Affected views with their corrections:**

| File | Class | Button | Current | Target |
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
| `bot_operations.py` | Bot Operations | View Admin Permissions (label says "Delete") | `.danger` | Fix label or split button |

### 5c. Prefix custom IDs (avoid conflicts)

**Problem:** `main_menu` is used in 5+ views with identical custom_id.

**Solution:** Each view gets prefixed IDs:

| Old custom_id | New custom_id | View |
|----------------|-----------------|------|
| `main_menu` | `alliance_main_menu` | Alliance Operations View |
| `main_menu` | `member_main_menu` | Member Operations View |
| `main_menu` | `bot_main_menu` | Bot Operations View |
| `main_menu` | `gift_main_menu` | Gift Operations View |
| `main_menu` | `other_main_menu` | Other Features View |
| `main_menu` | `backup_main_menu` | Backup View |
| `main_menu` | `bear_trap_main_menu` | Bear Trap View (NEW) |
| `main_menu` | `id_channel_main_menu` | ID Channel View (NEW) |
| `main_menu` | `scraper_main_menu` | Scraper View (already correct) |

**Pagination IDs:**

| Old custom_id | New custom_id |
|----------------|-----------------|
| `next` / `previous` | `{context}_next` / `{context}_previous` |
| `next_nick` / `previous_nick` | `history_nick_next` / `history_nick_previous` |

**Handler adjustment:** All `on_interaction()` listeners that check `custom_id == "main_menu"` must check the new prefixed ID. Alternatively: match with `.startswith()` or `.endswith("_main_menu")`.

### 5d. Missing error feedback

| File | Line | Problem | Solution |
|-------|-------|---------|--------|
| `alliance.py` | 1422 | Silent `pass` on interaction errors | Send ephemeral error embed |
| `bear_trap.py` | various | Traceback is printed, user sees nothing | Send ephemeral "An error occurred" |
| `gift_operations.py` | various | Some errors are swallowed | Consistent error embed pattern |

**Standard error response pattern:**

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

### 5e. Standardize embed colors

| Type | Color | Usage |
|-----|-------|-----------|
| Menu/Navigation | `discord.Color.blue()` | All submenu embeds |
| Success | `discord.Color.green()` | Successful actions |
| Error | `discord.Color.red()` | Errors and unauthorized |
| Warning | `discord.Color.orange()` | Warnings, partial success |
| Info/Progress | `discord.Color.blue()` | Ongoing processes |
| Scraper/Features | `discord.Color.teal()` | Can stay as an accent |

### Verification

```bash
# 1. Syntax check
python -m py_compile cogs/bear_trap.py
python -m py_compile cogs/id_channel.py
python -m py_compile cogs/gift_operations.py
python -m py_compile cogs/gift_scraper.py
python -m py_compile cogs/alliance.py
python -m py_compile cogs/backup_operations.py
python -m py_compile cogs/bot_operations.py
python -m py_compile cogs/other_features.py

# 2. Bot start check
python main.py  # All cogs must load

# 3. Custom ID duplicate check
grep -rn "custom_id=" cogs/*.py | grep -v "venv" | sort -t'"' -k2 | uniq -d -f1
# Result: no duplicate custom_ids remaining

# 4. Navigation path test (MANUAL in Discord - MANDATORY)
# Path 1: /settings → Alliance Operations → Back (🏠) → Main Menu
# Path 2: /settings → Member Operations → Back (🏠) → Main Menu
# Path 3: /settings → Bot Operations → Back (🏠) → Main Menu
# Path 4: /settings → Gift Operations → Back (🏠) → Main Menu
# Path 5: /settings → Alliance History → Back (🏠) → Main Menu
# Path 6: /settings → Other Features → Bear Trap → Back (◀️) → Other Features → Back (🏠) → Main Menu
# Path 7: /settings → Other Features → ID Channel → Back (◀️) → Other Features → Back (🏠) → Main Menu
# Path 8: /settings → Other Features → Backup System → Back (◀️) → Other Features → Back (🏠) → Main Menu
# Path 9: /settings → Gift Scraper → Back (🏠) → Main Menu
# Path 10: Click every button in every submenu → no error, no dead end
```

### Parallelization

**Well parallelizable:** the affected views are in different files.

```
Agent 1: Dead-end fixes (bear_trap.py, id_channel.py)
Agent 2: Button styles + emojis (gift_operations.py, backup_operations.py, bot_operations.py)
Agent 3: Custom ID prefixing (alliance.py, other views)
                    ↓ Verification
Agent 1: Error feedback pattern (utils.py + all cogs)
Agent 2: Standardize embed colors
                    ↓ Verification
```

---

## Step 6: Split large cogs

**Goal:** Keep files under ~500-600 lines.
**Risk:** HIGH
**Status:** [x] DONE

**Important:** Split files are NOT separate cogs but modules imported by the parent cog. `load_cogs` in main.py does NOT need to be changed.

### 6A: `gift_operations.py` (2871 lines -> 4 files)

| New file | Contents | Lines from original | ~Size |
|-----------|--------|--------------------|---------|
| `gift_operations.py` | Cog class, __init__, encode_data, solve_captcha, get_stove_info_wos, claim_giftcode_rewards_wos, retry_missing_codes | ~1-500 | ~500 |
| `gift_views.py` | GiftView, RetryFailedView, CreateGiftCodeModal, DeleteGiftCodeModal | ~2266-2871 | ~600 |
| `gift_distribution.py` | use_giftcode_for_alliance, auto-gift logic, setup_giftcode_auto | ~1700-2265 | ~600 |
| `gift_channel.py` | setup_gift_channel, delete_gift_channel, check_channels_loop | ~600-800 | ~200 |

**Import chain:**
- `gift_operations.py` imports `gift_views.py` for view classes
- `gift_views.py` receives cog reference via `__init__(self, cog)` (existing pattern)
- `gift_distribution.py` imports from `gift_operations.py` for API methods
- Avoid circular imports via `TYPE_CHECKING`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .gift_operations import GiftOperations
```

### 6B: `bear_trap.py` (2569 lines -> 4 files)

| New file | Contents | Lines from original | ~Size |
|-----------|--------|--------------------|---------|
| `bear_trap.py` | Cog class, DB setup, notification loop, check_admin removed (-> utils) | 12-625 | ~625 |
| `bear_trap_views.py` | BearTrapView (main menu), ChannelSelectView, ChannelSelectMenu, ImportEmbedModal | 1748-2569 | ~850 |
| `bear_trap_modals.py` | RepeatOptionView, RepeatIntervalModal, TextInputModal, TimeSelectModal, NotificationTypeView, CustomTimesModal, MentionTypeView, MentionSelectMenu, MessageTypeView | 626-1747 | ~1100 |
| `bear_trap_embed.py` | EmbedEditorView (standalone, large, self-contained) | 864-1156 | ~300 |

All view/modal classes receive `cog` as a constructor parameter (existing pattern).

### 6C: `alliance_member_operations.py` (1844 -> ~1310 after Step 3)

After Step 3 the file shrinks by ~534 lines (PaginationView: ~45, _create_monitored_task: ~7, fix_rtl: ~2, AllianceSelectView: ~317, re-exports: ~163).

~1310 lines is acceptable. Optionally split further:

| New file | Contents | ~Size |
|-----------|--------|---------|
| `alliance_member_operations.py` | Cog class (member add/remove/list/search) | ~900 |
| `alliance_member_views.py` | Remaining view classes specific to member ops | ~400 |

### 6D: `alliance.py` (1795 -> ~1718 after Step 3)

After Step 3 (PaginatedChannelView relocated), optionally split:

| New file | Contents | ~Size |
|-----------|--------|---------|
| `alliance.py` | Cog class (CRUD, settings, on_interaction) | ~1200 |
| `alliance_views.py` | Alliance-specific view classes | ~500 |

### Risk avoidance during splits

1. **Split one file at a time**
2. **Move view/modal classes first** (they are standalone, only reference `self.cog`)
3. **Keep backwards-compatible re-exports** in the original file
4. **Avoid circular imports** via `TYPE_CHECKING`
5. **After each split:** test all commands of the affected cog

### Verification (per split individually!)

```bash
# 1. Syntax check (ALL new + changed files)
python -m py_compile cogs/gift_operations.py
python -m py_compile cogs/gift_views.py
python -m py_compile cogs/gift_distribution.py
python -m py_compile cogs/gift_channel.py
# (analogous for bear_trap splits)

# 2. Import check — no circular imports
python -c "from cogs.gift_views import GiftView; print('OK')"
python -c "from cogs.gift_distribution import *; print('OK')"
python -c "from cogs.bear_trap_views import BearTrapView; print('OK')"
python -c "from cogs.bear_trap_modals import TimeSelectModal; print('OK')"

# 3. Bot start check
python main.py  # All cogs must load, no ImportErrors

# 4. Functional test per split cog (MANUAL in Discord - MANDATORY)
# gift_operations: Create, List, Delete Gift Code, Use for Alliance, Auto Gift, Gift Channel
# bear_trap: Set Time (Discord + Web), Remove, View, Toggle Notifications
# alliance_member: Add, Remove, View, Transfer Member
# alliance: Add, Edit, Delete, View, Check Alliance

# 5. Verify that no functionality was lost
# Before: note number of slash commands + buttons
# After: same number present
```

### Parallelization

**Limited parallelizability:** splits must be verified one after another, since they could affect each other. Within a split, however, new files can be created in parallel.

```
Agent 1: create gift_views.py        |  Agent 2: create gift_distribution.py
                    ↓ Verification of gift_operations split
Agent 1: create bear_trap_views.py   |  Agent 2: bear_trap_modals.py + bear_trap_embed.py
                    ↓ Verification of bear_trap split
Sequential: alliance_member_operations → alliance (optional)
                    ↓ Verification
```

---

## Summary

| Step | New files | Changed files | Risk | Effort |
|------|-------------|-------------------|--------|---------|
| 1. Secrets/.env | 3 | 3 | Low | Small |
| 2. DB Layer | 1 | 15 (all cogs + main) | Medium | Large |
| 3. Utils | 1 | 10 | Medium | Medium |
| 4. Logging | 1 | 15 (all .py) | Low | Medium |
| 5. Menu structure | 0 | 8 (view classes) | Low-Medium | Medium |
| 6. Cog splits | 8 | 4 | High | Large |
| **Total** | **14 new** | **all changed** | | |

### New files after completion

```
cogs/
├── database.py          (Step 2 - DB Manager)
├── utils.py             (Step 3 - Shared Utilities)
├── log_config.py        (Step 4 - Logging Setup)
├── gift_views.py        (Step 6A - Gift UI)
├── gift_distribution.py (Step 6A - Gift Distribution)
├── gift_channel.py      (Step 6A - Gift Channel)
├── bear_trap_views.py   (Step 6B - Bear Trap UI)
├── bear_trap_modals.py  (Step 6B - Bear Trap Modals)
├── bear_trap_embed.py   (Step 6B - Embed Editor)
├── ... (existing files, smaller)
.env                     (Step 1 - Secrets)
.env.example             (Step 1 - Template)
.gitignore               (Step 1 - Protection)
```
