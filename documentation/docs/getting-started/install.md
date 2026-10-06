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
export PLURAPACK_DATA_DIR='/private/path/plurapack-data'
```

### Windows PowerShell

```powershell
$env:STOAT_BOT_TOKEN = 'your-private-token'
$env:FLUXER_BOT_TOKEN = 'your-private-token'
$env:PLURAPACK_PREFIX = 'p;'
$env:PLURAPACK_DATA_DIR = 'C:\private\plurapack-data'
```

At least one of `STOAT_BOT_TOKEN` and `FLUXER_BOT_TOKEN` is required. Set both
to connect to Stoat and Fluxer at the same time. The prefix defaults to `p;`,
and the database defaults to `plurapack.sqlite3` in the current directory.
When `PLURAPACK_DATA_DIR` is set, the default database is instead
`plurapack.sqlite3` beneath that directory. An explicitly set
`PLURAPACK_DATABASE` takes precedence and can still select a custom database
file. Missing required directories are created at startup; invalid or
inaccessible configured paths stop startup rather than causing an ephemeral
fallback.

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

## Railway persistent storage

For Railway, attach a Volume to the existing Plurapack service, mount it at
`/data`, and set `PLURAPACK_DATA_DIR=/data`. Keep `python -m plurapack` as the
Start Command and keep the bot and dashboard together in the same service. A
Volume—not merely the environment variable—is required for the SQLite database
to survive redeployments and container replacement.

Plurapack does not move an old database when its configured path changes. Move
it deliberately while Plurapack is stopped if retaining an existing deployment.
The browser-audio spool is temporary and remains outside the persistent data
directory. Custom voice recordings are stored in the configured private Forgejo
repository at `custom/<UUID>.wav`; SQLite retains the voice metadata and member
selection. Configure the protected Modal endpoint and separate TTS/Forgejo
credentials as described in the [speech guide](../guides/speech.md). No local
reference directory or GPU is needed on the Plurapack host.

## 4. Keep it safe

- Run the process as a dedicated, unprivileged account when possible.
- Restrict read access to the token, database, exports, and voice files.
- Back up the SQLite file while preserving filesystem permissions.
- Test permissions in a private channel before enabling the bot broadly.

!!! warning
    SQLite does not encrypt data at rest. Use full-disk encryption if that is part of your threat model.
