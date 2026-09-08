"""Whole-home R2 snapshots; no scheduler, watcher, or keep-alive.

The remote entry point owns the request transaction. Existing storage modules
continue to read and write local files. One conditional object PUT commits an
entire snapshot, including deletions, without partially updating remote files.
"""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import threading
from typing import Callable


pending_jobs: ContextVar[list[Callable] | None] = ContextVar("nowhere_jobs", default=None)


def run_background_job(job: Callable) -> None:
    """Local mode keeps threads; remote requests await these jobs before commit."""
    jobs = pending_jobs.get()
    if jobs is None:
        threading.Thread(target=job, daemon=True).start()
    else:
        jobs.append(job)


class PersistenceError(RuntimeError):
    pass


class R2Persistence:
    def __init__(self, home: Path, client, bucket: str, key: str,
                 max_bytes: int = 128 * 1024 * 1024):
        self.home = home.resolve()
        self.client = client
        self.bucket = bucket
        self.key = key
        self.max_bytes = max_bytes
        self.etag: str | None = None
        self.fingerprint: dict | None = None
        self.ready = False

    @classmethod
    def from_env(cls) -> "R2Persistence":
        names = ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise PersistenceError("Missing R2 configuration: " + ", ".join(missing))
        import boto3
        from botocore.config import Config

        endpoint = os.environ["R2_ENDPOINT_URL"]
        if not endpoint.startswith("https://"):
            raise PersistenceError("R2_ENDPOINT_URL must use HTTPS")
        client = boto3.client(
            "s3", endpoint_url=endpoint, region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(connect_timeout=5, read_timeout=30,
                          retries={"mode": "standard", "total_max_attempts": 3},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required",
                          s3={"addressing_style": "path"}),
        )
        home = Path(os.environ.get("NOWHERE_HOME", str(Path.home() / ".nowhere")))
        return cls(home, client, os.environ["R2_BUCKET"],
                   os.environ.get("R2_SNAPSHOT_KEY", "nowhere/snapshot.tar.gz"),
                   int(os.environ.get("R2_MAX_SNAPSHOT_BYTES", 128 * 1024 * 1024)))

    def _inventory(self) -> dict:
        inventory = {}
        total = 0
        for path in sorted(self.home.rglob("*")):
            name = path.relative_to(self.home).as_posix()
            if path.is_symlink():
                raise PersistenceError("Symlinks are not supported in NOWHERE_HOME")
            if path.is_dir():
                inventory[name] = None
            elif path.is_file():
                total += path.stat().st_size
                if total > self.max_bytes:
                    raise PersistenceError("NOWHERE_HOME exceeds R2_MAX_SNAPSHOT_BYTES")
                with path.open("rb") as stream:
                    inventory[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                raise PersistenceError("Unsupported file in NOWHERE_HOME")
            if len(inventory) > 10000:
                raise PersistenceError("Too many snapshot entries (maximum 10000)")
        return inventory

    def restore(self) -> None:
        """Restore before importing storage modules or accepting requests.

        Only NoSuchKey is an empty first installation. Bad credentials, network
        errors, corrupt archives and missing buckets must fail startup closed.
        """
        from botocore.exceptions import ClientError

        self.ready = False
        self.home.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "NoSuchKey":
                raise PersistenceError("R2 restore failed; local data was not replaced") from exc
            self.home.mkdir(parents=True, exist_ok=True)
            # The first sync also imports a pre-populated local home, if any.
            self.fingerprint = None
            self.etag = None
            self.ready = True
            return

        with tempfile.TemporaryDirectory(prefix="nowhere-restore-", dir=self.home.parent) as tmp:
            staging = Path(tmp) / "home"
            staging.mkdir()
            archive = Path(tmp) / "snapshot.tar.gz"
            size = 0
            try:
                with archive.open("wb") as out:
                    for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
                        size += len(chunk)
                        if size > self.max_bytes + 10 * 1024 * 1024:
                            raise PersistenceError("R2 archive exceeds size limit")
                        out.write(chunk)
            finally:
                response["Body"].close()
            with tarfile.open(archive, "r:gz") as tar:
                total = 0
                seen = set()
                for member in tar:
                    name = PurePosixPath(member.name)
                    if (name.is_absolute() or ".." in name.parts or not name.parts
                            or "\\" in member.name or str(name) in seen
                            or not (member.isfile() or member.isdir())):
                        raise PersistenceError("Unsafe or duplicate R2 snapshot entry")
                    seen.add(str(name))
                    total += member.size
                    if total > self.max_bytes or len(seen) > 10000:
                        raise PersistenceError("R2 snapshot exceeds extraction limits")
                    dest = staging.joinpath(*name.parts)
                    if member.isdir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out)
            # Validation/extraction finishes before touching an existing home.
            old = Path(tmp) / "old-home"
            if self.home.exists():
                self.home.rename(old)
            try:
                staging.rename(self.home)
            except BaseException:
                if old.exists():
                    old.rename(self.home)
                raise
        self.etag = response["ETag"]
        self.fingerprint = self._inventory()
        self.ready = True

    def sync(self) -> bool:
        """Upload only changed data. A stale deployment cannot overwrite a newer one."""
        if not self.ready:
            raise PersistenceError("Restore must complete before sync")
        inventory = self._inventory()
        if inventory == self.fingerprint:
            return False
        with tempfile.TemporaryFile() as archive:
            with tarfile.open(fileobj=archive, mode="w:gz", dereference=True) as tar:
                for name in inventory:
                    tar.add(self.home / name, arcname=name, recursive=False)
            length = archive.tell()
            archive.seek(0)
            condition = {"IfMatch": self.etag} if self.etag else {"IfNoneMatch": "*"}
            try:
                response = self.client.put_object(
                    Bucket=self.bucket, Key=self.key, Body=archive,
                    ContentLength=length, ContentType="application/gzip", **condition,
                )
            except Exception as exc:
                raise PersistenceError(
                    "R2 save failed or another instance changed the snapshot; "
                    "do not repeat the action. Check storage and restart if there is a conflict."
                ) from exc
        self.etag = response["ETag"]
        self.fingerprint = inventory
        return True
