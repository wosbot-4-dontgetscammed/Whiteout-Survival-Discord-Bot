# Contributing

Thanks for your interest in improving this fork.

## Where to send what

This repository is a **community fork** of
[Reloisback/Whiteout-Survival-Discord-Bot](https://github.com/Reloisback/Whiteout-Survival-Discord-Bot).
Please send your change to the place that can act on it:

| Type of change | Where to open it |
|----------------|------------------|
| Bug in functionality that exists upstream | Upstream repo first; if you also want it patched here in the meantime, open it here too. |
| Improvement to the beginner setup guide, `requirements.txt`, `.env.example`, `CHANGELOG.md`, or fork-only documentation | This repo. |
| Refactoring work tracked in [`REFACTORING_PLAN.md`](REFACTORING_PLAN.md) | This repo. |
| Question about gameplay or the WOS API itself | Upstream repo or Reloisback's official channels. |

When in doubt, open it here and we will redirect.

## Pull request workflow

1. Fork this repository (yes, fork the fork).
2. Create a branch from `main` named after the change, e.g. `fix/intent-toggle-doc`, `refactor/step-2-db-layer`.
3. Make the change in the smallest reviewable units. One concern per PR.
4. Update `CHANGELOG.md` under `[Unreleased]` if your change is user-visible.
5. Run a syntax check before opening the PR:
   ```bash
   python -m compileall -q cogs main.py
   ```
   The CI pipeline (`.github/workflows/ci.yml`) does the same check on every PR.
6. Open the PR against `main` of this repo. Reference any related upstream issue in the description.

## Commit message style

- Subject line: imperative mood, no trailing period, ≤ 72 characters.
- Body: explain the *why*, not the *what* — the diff already shows what changed.
- Reference issues with `Fixes #N` / `Refs #N` where applicable.

Example:
```
Use os.getenv for BOT_TOKEN in main.py

bot_token.txt is no longer read at runtime; the value is sourced from
the .env file loaded by python-dotenv. This matches step 1 of the
refactoring plan and removes the only file that previously had to be
created with the wrong filename to avoid being committed.
```

## Code style

- Target Python 3.12.4 (matches the version pinned in the README).
- Keep new modules under `cogs/` consistent with the structure described in `REFACTORING_PLAN.md`.
- No new top-level dependencies without a corresponding update to `requirements.txt` and to the `required_packages` dict in `main.py`.

## Reporting security issues

Please **do not** open a public issue for security-sensitive bugs (token leakage, RCE, SQL injection, etc.). Instead, contact the maintainer of this fork via a private GitHub message, or — for issues that affect upstream as well — Reloisback at `usabsz@gmail.com`.

## License

By contributing you agree that your contribution is released under the
same [Custom Usage License by Reloisback](LICENSE) that covers the rest of
the project. Attribution to the original author and to all derivative-work
clauses must remain intact.
