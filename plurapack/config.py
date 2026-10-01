"""Resolve Plurapack's filesystem-backed persistent storage.

Importing this module has no filesystem side effects.  Call
``resolve_database_path`` during application startup to create and validate the
directory that will contain SQLite.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


DEFAULT_DATABASE = "plurapack.sqlite3"


class StorageConfigurationError(RuntimeError):
    """Raised when configured persistent storage cannot be prepared."""


def _prepare_directory(directory: Path, description: str) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise NotADirectoryError(f"{directory} is not a directory")
        if not os.access(directory, os.W_OK | os.X_OK):
            raise PermissionError(f"{directory} is not writable")
    except OSError as error:
        raise StorageConfigurationError(
            f"Cannot use {description} '{directory}': {error}"
        ) from error


def resolve_database_path(
    environ: Mapping[str, str] | None = None, *, create: bool = True
) -> str:
    """Return the configured SQLite path, optionally preparing its directory.

    An explicit ``PLURAPACK_DATABASE`` wins over ``PLURAPACK_DATA_DIR``.  With
    neither variable, the historical current-directory default is retained.
    """
    environment = os.environ if environ is None else environ
    configured_database = environment.get("PLURAPACK_DATABASE")
    data_dir = environment.get("PLURAPACK_DATA_DIR")

    if configured_database:
        database = Path(configured_database)
        database_value = configured_database
        directory = database.parent
        description = "database parent directory"
    elif data_dir:
        directory = Path(data_dir)
        database = directory / DEFAULT_DATABASE
        database_value = str(database)
        description = "PLURAPACK_DATA_DIR"
    else:
        database = Path(DEFAULT_DATABASE)
        database_value = DEFAULT_DATABASE
        directory = database.parent
        description = "database parent directory"

    if create and database_value != ":memory:":
        _prepare_directory(directory, description)
        if database.exists() and not database.is_file():
            raise StorageConfigurationError(
                f"Cannot use database path '{database}': it is not a file"
            )
    return database_value
