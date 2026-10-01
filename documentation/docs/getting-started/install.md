# Install and run

This page is for bot operators. Users of an existing bot can continue to [Your first system](first-system.md).

## 1. Create an isolated environment

From a Plurapack source checkout:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## 2. Configure required values

Never write the token into source code or commit it to Git.

### Linux and macOS

```bash
export STOAT_BOT_TOKEN='your-private-token'
export FLUXER_BOT_TOKEN='your-private-token'
export PLURAPACK_PREFIX='p;'
export PLURAPACK_DATABASE='/private/path/plurapack.sqlite3'
```

### Windows PowerShell

```powershell
$env:STOAT_BOT_TOKEN = 'your-private-token'
$env:FLUXER_BOT_TOKEN = 'your-private-token'
$env:PLURAPACK_PREFIX = 'p;'
$env:PLURAPACK_DATABASE = 'C:\private\plurapack.sqlite3'
```

At least one of `STOAT_BOT_TOKEN` and `FLUXER_BOT_TOKEN` is required. Set both
to connect to Stoat and Fluxer at the same time. The prefix defaults to `p;`,
and the database defaults to `plurapack.sqlite3` in the current directory.

## 3. Start Plurapack

```bash
plurapack
```

From a source checkout, `python main.py` is an equivalent cross-platform entry point.

For production, start the bot and web dashboard together under the unified
supervisor. It stops both components if either one fails and preserves the web
server's `PORT`, `PLURAPACK_WEB_PORT`, and `PLURAPACK_WEB_HOST` configuration:

```bash
python -m plurapack
```

## 4. Keep it safe

- Run the process as a dedicated, unprivileged account when possible.
- Restrict read access to the token, database, exports, and voice files.
- Back up the SQLite file while preserving filesystem permissions.
- Test permissions in a private channel before enabling the bot broadly.

!!! warning
    SQLite does not encrypt data at rest. Use full-disk encryption if that is part of your threat model.
