# ats_fratag_formynd_slet

ATS process that deletes old fratag-formynd sager every night directly in the `[RPA].[formynd]` schema owned by ats_fratag_formynd, built on ats-process-framework. This robot owns the deletion rule (`ErAktiv = 0` and `SlutDato` more than `SLETTE_DAGE` = 30 days ago) and only deletes database rows, never the ESDH sag. All database access goes through the SQLAlchemy ORM — never raw SQL strings: models and `db_session` live in `ats_framework/helpers/models.py` (columns mirror ats_fratag_formynd's `helpers/models.py`), queries in `ats_framework/helpers/formynd_db.py` and `rpa_db.py`. Keep `CHILD_TABLES` in sync with the tables in ats_fratag_formynd's `db/20_tabeller.sql` that carry a `SagId`. Each deletion is one transaction that re-checks the rule under lock and writes a PII-free `SLETNING` row to `ProcesEventLog`. Process logic lives in `ats_framework/processes/`; configuration in `ats_framework/processes/slet_config.py` (`DRY_RUN` must stay `True` until deletion is approved for an environment). Run `uv run pytest` and `uv run ruff check . && uv run ruff format --check .` before committing. Docstrings are in Danish (Google format).

## GitHub & Git workflow

**Hard rules — never override these, even if it seems convenient**
- Commit freely on dedicated `task/<slug>` (topic) branches without being asked. On any shared/protected branch, only commit when explicitly told to — and NEVER on `main` or `dev` (see below).
- NEVER commit to `main`.
- NEVER commit to `dev`.
- ONLY create pull requests when explicitly told to.
- NEVER merge pull requests — the user does this themselves in the GitHub UI.
- NEVER delete anything from GitHub — branches, tags, releases, repos, issues, PRs, comments — UNLESS I created it myself for testing (e.g. a throwaway write-check branch), in which case cleaning up my own test artifact is allowed.
- If asked to do any of the above "NEVER" actions: do NOT do it. Say plainly that I've been instructed never to do it, then give a step-by-step guide so the user can do it themselves.

**Pushing & committing**
- Never `push` unless explicitly asked — pushing is always an opt-in action, even when commits already exist.
- Always commit on a topic branch, never on `main` or `dev` (create one off the current branch first if needed).
- End commit messages with the `Co-Authored-By: Claude` trailer.

**Credentials & tokens**
- Never paste a token into the chat. To hand me a token, write it to a file (e.g. `~/pat.txt`) in your own terminal — not via the `!` prefix — and tell me the path. I authenticate by piping the file straight into `gh` (`gh auth login --with-token < file`), so the value never enters the transcript.
- Delete the token file immediately after authenticating (`shred -u <file>`, or `rm -f`). Do NOT keep PAT files on the VM as a reusable library — the only token that lives on this machine is the one baked into `gh`'s `hosts.yml` (mode 600). When I need access the gh-auth token lacks, ask the user for a scoped token, authenticate, then delete the file.
- Prefer fine-grained PATs scoped to the specific repo + minimum permissions (avoid `admin:public_key`). Prefer HTTPS git protocol (`gh config set git_protocol https`) so the token drives git, not SSH.
- If a token value is ever exposed (printed, pasted, logged), treat it as compromised: revoke/rotate it and re-auth.
- **Write access via a temporary PAT** — the standing `gh` auth is a read(-all) token that can't push. When a push/PR needs write access, the user drops a scoped write PAT in the agreed transfer folder. The flow, every time:
  1. Back up the current read auth: `cp ~/.config/gh/hosts.yml ~/.config/gh/hosts.yml.bak`.
  2. Authenticate with the write PAT: `gh auth login --with-token < <pat-file>` then `gh auth setup-git`.
  3. Do the push / open the PR (never merge).
  4. `shred -u <pat-file>` — delete the write token immediately after use.
  5. Restore the read token: `mv ~/.config/gh/hosts.yml.bak ~/.config/gh/hosts.yml` then `gh auth setup-git`.
  - End state: the machine is back on the read-only token and is never left with write access active. No separate "read token" needs sending — the backup IS it.

**Verifying access (non-destructive)**
- To confirm write access without altering anything, use a reversible ref check: create a throwaway branch ref at the current HEAD sha, verify, then delete it. Never use a test commit/file to probe access.

**Repo hygiene**
- Before deleting or overwriting anything I didn't create, inspect it first and surface any mismatch instead of proceeding.
- Use the `gh` CLI for GitHub operations (PRs, issues, API).
