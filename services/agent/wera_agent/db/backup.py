"""Nightly backups of the Postgres database (REL-05).

On SQLite a backup was copying a file. On Postgres the data lives in the server's own directory,
so a copy of it is something WERA has to make: `pg_dump` in its custom format, which `pg_restore`
can bring back whole or table by table. One a day, the last fourteen kept, in `data/backups/`.

`pg_dump` is the server's own tool rather than a Python reimplementation, because a backup is the
one thing that must be restorable by someone who has never seen this code. The password goes in
the environment (`PGPASSWORD`), never on the command line, where `ps` would show it.

To restore into an empty database:

    createdb wera_restored
    pg_restore --dbname=wera_restored --no-owner data/backups/wera-20261112-0300.dump
"""

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import logfire
from sqlalchemy.engine import make_url

from ..settings import get_settings

KEEP_DAYS = 14
PREFIX = "wera-"
SUFFIX = ".dump"


class BackupFailed(Exception):
    """`pg_dump` couldn't run or didn't finish: why, in words."""


def backup_dir() -> Path:
    return get_settings().data_dir / "backups"


def backup(now: datetime | None = None) -> Path:
    """Dump the configured database to `data/backups/`, prune old dumps, return the new file."""
    url = make_url(get_settings().db_url)
    if url.get_backend_name() != "postgresql":
        raise BackupFailed("Backups are for the Postgres database; DATABASE_URL isn't one.")
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise BackupFailed("pg_dump isn't on PATH. It comes with Postgres (brew install postgresql@17).")

    when = (now or datetime.now()).astimezone()
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{PREFIX}{when:%Y%m%d-%H%M}{SUFFIX}"
    partial = out.with_suffix(".partial")

    env = {**os.environ}
    if url.password:
        env["PGPASSWORD"] = url.password
    command = [pg_dump, "--format=custom", "--no-owner", f"--file={partial}"]
    command += [f"--host={url.host or 'localhost'}", f"--port={url.port or 5432}"]
    if url.username:
        command.append(f"--username={url.username}")
    command.append(url.database or "wera")

    with logfire.span("database backup", database=url.database):
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600, check=False)  # noqa: S603
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise BackupFailed(f"pg_dump failed: {result.stderr.strip()[:400]}")
        # Renamed only once it is complete, so a dump cut off halfway never looks like a backup.
        partial.rename(out)
        removed = prune(when)
        logfire.info(
            "Backed up {database} to {file} ({size} bytes); removed {removed} old backup(s)",
            database=url.database,
            file=out.name,
            size=out.stat().st_size,
            removed=removed,
        )
    return out


def prune(now: datetime) -> int:
    """Delete dumps older than `KEEP_DAYS`. Only WERA's own files: anything else is left alone."""
    cutoff = now - timedelta(days=KEEP_DAYS)
    removed = 0
    for file in backup_dir().glob(f"{PREFIX}*{SUFFIX}"):
        try:
            made = datetime.strptime(file.name.removeprefix(PREFIX).removesuffix(SUFFIX), "%Y%m%d-%H%M").astimezone()
        except ValueError:
            continue
        if made < cutoff:
            file.unlink(missing_ok=True)
            removed += 1
    return removed
