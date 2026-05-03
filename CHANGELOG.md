# Fork Changelog

This file tracks changes that diverge this fork
([wosbot-4-dontgetscammed/Whiteout-Survival-Discord-Bot](https://github.com/wosbot-4-dontgetscammed/Whiteout-Survival-Discord-Bot))
from upstream
([Reloisback/Whiteout-Survival-Discord-Bot](https://github.com/Reloisback/Whiteout-Survival-Discord-Bot)).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions refer to fork milestones, not upstream releases.

## [Unreleased]

### Added
- `requirements.txt` so users can install dependencies with a single `pip install -r requirements.txt`.
- `CHANGELOG.md` (this file) tracking fork-specific changes.
- Beginner setup guide (12 sections) at the top of `README.md` covering Python install on Windows/macOS/Linux, Discord bot creation, virtual environments, `.env` configuration, first start, 24/7 operation, updates, and troubleshooting.
- `.env.example` template documenting all environment variables read by `main.py` and the cogs.
- `.gitignore` excluding secrets (`.env`, `bot_token.txt`), runtime data (`db/`, `log/`, `giftcode_logs/`), Python artifacts, and the `upstream/` working copy.
- New cog modules from in-progress refactoring: `cogs/config.py`, `cogs/database.py`, `cogs/utils.py`, `cogs/log_config.py`, `cogs/wos_api.py`, `cogs/gift_api.py`, `cogs/gift_scraper.py`, `cogs/gift_distribution.py`, `cogs/gift_ui.py`, `cogs/gift_views.py`, `cogs/bear_trap_modals.py`, `cogs/bear_trap_views.py`.
- `REFACTORING_PLAN.md` documenting the 6-step refactoring path: secret handling, centralized DB layer, shared utilities, logging, menu structure, cog splits.

### Changed
- Modified upstream cogs to support the refactoring above: `alliance.py`, `alliance_member_operations.py`, `backup_operations.py`, `bear_trap.py`, `bear_trap_editor.py`, `bot_operations.py`, `changes.py`, `control.py`, `gift_operations.py`, `gift_operationsapi.py`, `id_channel.py`, `logsystem.py`, `olddb.py`, `other_features.py`, `support_operations.py`, `w.py`, `wel.py`.
- Replaced hardcoded secrets in `main.py` and cogs with `os.getenv()` reads against a `.env` file loaded via `python-dotenv`.

### Preserved from upstream
- Original `LICENSE` (Custom Usage License by Reloisback) — unchanged.
- Original `README.md` content — appended below the new fork setup section.
- All upstream assets (`pictures/`, `V1oldbot/`, `V2Old/`, `autoupdateinfo.txt`).

---

## Baseline

Forked from upstream commit
[`6a3f2e7`](https://github.com/Reloisback/Whiteout-Survival-Discord-Bot/commit/6a3f2e7)
("Update README.md") on 2026-05-03.
