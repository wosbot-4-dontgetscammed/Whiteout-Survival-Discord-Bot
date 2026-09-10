# Fork Changelog

This file tracks changes that diverge this fork
([wosbot-4-dontgetscammed/Whiteout-Survival-Discord-Bot](https://github.com/wosbot-4-dontgetscammed/Whiteout-Survival-Discord-Bot))
from upstream
([Reloisback/Whiteout-Survival-Discord-Bot](https://github.com/Reloisback/Whiteout-Survival-Discord-Bot)).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions refer to fork milestones, not upstream releases.

## [Unreleased]

### 2026-09 — WoS Atlas as replacement player-data source
The game's own player endpoint stayed dead, so profile data now comes from the
[WoS Atlas](https://wosatlas.com) community index (free account required).

**Added**
- `cogs/wosatlas_api.py` — authenticated client for `api.wosatlas.com/v1`. Chief-ID
  lookup returns nickname, furnace level, state, power, alliance tag and coordinates.
  Serialised requests with a 1.1 s minimum interval (their `robots.txt` asks for
  `Crawl-delay: 1`), a 30-minute cache and automatic re-login on session expiry.
- `cogs/wosatlas_sync.py` — `/atlas_sync [alliance]` (admin) refreshes nickname,
  furnace and state for a roster; `/atlas_lookup <fid>` shows a single live record.
- `WOSATLAS_EMAIL` / `WOSATLAS_PASSWORD` / `WOSATLAS_USER_AGENT` settings.
- `cogs/giftcode_report.py` — `/giftcode_report [alliance] [code]` summarises redemptions
  per alliance and code (redeemed / failed / pending / never attempted);
  `/giftcode_missing <alliance> <code>` names the members still without a code;
  a daily digest per alliance channel (`/giftcode_digest` toggles it) — the existing
  summaries were event-driven only, so quiet days produced no report at all.
- `cogs/api_queue.py` — one shared, token-bucket request queue per upstream host
  (`wosatlas` 0.9/s, `centurygame` 3/s). Every caller in the process goes through it, and
  a rate-limit response pauses the whole queue instead of only the unlucky caller.
- Rate-limit handling for the Atlas API: 429 is retried using the server's `Retry-After`
  / `X-RateLimit-Reset`, and the client pauses before its quota (120 requests/60s) runs
  out. A throttled lookup is never mistaken for "player not found".
- `is_placeholder_name()` guard — Atlas reports unnamed accounts as `Lord<fid>`, which
  must never overwrite a real stored nickname.

**Changed**
- Alliance control (`cogs/control.py`) picks its data source per run: the game's API
  when alive, WoS Atlas otherwise. Furnace / nickname / state-transfer notifications
  work again through the existing embeds.
- Gift-code state repair (`cogs/gift_api.py`) asks WoS Atlas for a member's real `kid`
  after a `40020 USER INFO ERROR` before falling back to kingdom probing.

**Fixed**
- **Kingdom oracle false positives.** `resolve_kingdom()` treated *any* response that was
  not `40020` as proof that a kingdom was correct — including unreadable bodies, where a
  missing `err_code` defaulted to `0`. A wrong kingdom could therefore be written as fact,
  breaking every later redemption for that member and causing endless repair loops (one
  member produced 596 identical repair warnings). Only the error codes upstream returns
  *after* accepting `fid+kid` now count as confirmation; anything else is inconclusive.
- Atlas kingdom changes are confirmed against the game before being stored
  (`wosatlas_api.confirm_kid()`).
- `claim_giftcode_rewards_wos` crashed with `'int' object has no attribute 'upper'` when
  upstream returned a non-string `msg`, aborting that member's redemption.
- The community gift-code sync ran against an empty URL when `GIFTCODE_API_URL` /
  `GIFTCODE_API_KEY` were unset, producing ~100 errors a day with empty messages for a
  feature that was never configured. It now disables itself with one clear log line, and
  the remaining connection errors report the exception type and URL.
- Repeated `40020` warnings for a member already probed this run are logged at debug.
- A transient probe failure (429 / network error) no longer counts as "the player-info
  endpoint is gone", which used to switch every alliance to the fallback source for 6h.
- Transient WoS Atlas failures are no longer cached as "player has no record".
- The dead player endpoint is no longer hammered once per alliance interval. A circuit
  breaker in `cogs/wos_api.py` answers `404` without a request while the endpoint is
  down, re-probing every 6 hours, and the outage notice is posted once per channel
  instead of every cycle (previously ~6600 pointless cycles were logged).


### 2026-07 — CenturyGame API change adaptation
CenturyGame removed the `/api/player` and `/api/captcha` endpoints (~2026-07-21) and made the kingdom id (`kid`) required for gift-code redemption. Changes to keep the fork working:
- **Gift redemption** rewritten to the new single signed call (`fid`+`cdk`+`kid`+`time`), dropping the dead player-info pre-check and the captcha loop (`cogs/gift_api.py`).
- **Specific failure reasons** everywhere instead of a bare `ERROR` (e.g. `CDK_NOT_FOUND`, `USER_INFO_ERROR`, `NO_KID`, `ERROR_<code>_<MSG>`); completed the signed-v2 error-code map and retry `40019` (per-FID throttle), never conflating it with `40020`.
- **Kingdom auto-detect** via a side-effect-free gift-code oracle (`wos_api.resolve_kingdom`); used by add-member and the ID channel.
- **Per-alliance regions** (`cogs/regions.py`): `alliance_regions` table, `/region_add|remove|default|list` commands, a **Manage Regions** button in the member menu, and auto-seeding of defaults from existing members. The default pre-fills add-member's region field.
- **Inactive-member handling** (`cogs/gift_operations.py`): members unresolvable for several cycles (upstream-up gated) are flagged inactive and skipped, with `/inactive_members` to review/reactivate; stale kingdoms are auto-corrected when detectable.
- **Screenshot member add** (`cogs/screenshot_add.py`, `cogs/screenshot_ocr.py`, `tools/ocr_vision.swift`): `/add_screenshot` reads nickname/furnace/kingdom from a profile screenshot via on-device Apple Vision OCR (macOS, offline, no API key).
- **Resilient scraper** (`cogs/gift_scraper.py`): validates candidate codes against several resolving players so a dead validator can't discard valid codes; disabled the OAuth-gated Reddit source.
- **Local backups** (`cogs/backup_operations.py`): encrypted-or-plain backups written to `backups/` (kept 14); the defunct upload API is optional.
- **Graceful degradation** in `control.py` (probe once, skip furnace/nickname sync while the API is down, clearer status message) and `w.py` (falls back to stored records).

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
