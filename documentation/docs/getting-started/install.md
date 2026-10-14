# Install and run

This page is for bot operators. Users of an existing bot can continue to [Your first system](first-system.md).

## 1. Create an isolated environment

Plurapack installs only the chat platform libraries you use: Stoat, Fluxer, or
both. From a Plurapack source checkout, the interactive installer asks which you
host, offers to create the virtual environment, and installs the right libraries:

```bash
python install.py
```

To skip the questions, name the platform (`stoat`, `fluxer`, or `both`). Add
`--dev` for the test tools, `--venv` to create `.venv`, or `--dry-run` to see what
it would do without changing anything:

```bash
python install.py --platform stoat
python install.py --platform both --venv
```

### Installing by hand

Create the environment and install the extra that matches your platform:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[stoat]'          # Stoat only
python -m pip install -e '.[fluxer]'         # Fluxer only
python -m pip install -e '.[stoat,fluxer]'   # both (or '.[all]')
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

A plain `python -m pip install -e .` installs Plurapack and its dashboard without
either chat library. If you later start a platform whose library is missing,
Plurapack stops with a message naming the extra to install. `'.[dev]'` adds the
test tools and both libraries.

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

At least one of `STOAT_BOT_TOKEN` and `FLUXER_BOT_TOKEN` is required, and the
matching library must be installed (step 1). Set both to connect to Stoat and
Fluxer at the same time; if you installed only one platform, set only its token. The prefix defaults to `p;`,
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

## Railway: choosing platforms

Railway builds the included `Dockerfile`, which installs both the Stoat and
Fluxer libraries by default. To install only one, add a service variable named
`PLURAPACK_PLATFORMS` set to `stoat` or `fluxer` (the default is `stoat,fluxer`),
then redeploy so the image rebuilds. For a local Docker build, pass it as
`docker build --build-arg PLURAPACK_PLATFORMS=stoat .`

## Optional image uploads

By default you paste an image link into the dashboard. To let people upload a picture
from the picker instead, set these service variables and redeploy:

| Variable | Meaning |
| --- | --- |
| `PLURAPACK_IMAGE_HOST_KEY` | Your API key for the image host. Uploads are off until this is set. |
| `PLURAPACK_IMAGE_HOST_URL` | Upload endpoint. Defaults to `https://freeimage.host/api/1/upload`. Must be `https`. |
| `PLURAPACK_IMAGE_MAX_BYTES` | Largest accepted file. Defaults to 8 MiB. |

Plurapack still stores only the returned link. Only PNG, JPEG, GIF, and WebP files are
accepted, judged by their contents. The host can delete images or go offline, and its
terms apply to what you upload, so keep the original files. Pasting any other link still works. People can also attach a picture to `p;avatar` or `p;banner` and leave out the URL.

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
