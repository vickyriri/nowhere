import io
from pathlib import Path
import tarfile

import boto3
from moto import mock_aws
import pytest

from nowhere.persistence import PersistenceError, R2Persistence


@pytest.fixture
def storage(tmp_path):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1",
                              aws_access_key_id="testing", aws_secret_access_key="testing")
        client.create_bucket(Bucket="nowhere-test")
        yield R2Persistence(tmp_path / "home", client, "nowhere-test", "private/snapshot.tar.gz")


def test_whole_home_roundtrip_deletions_and_noop(storage, tmp_path):
    storage.restore()
    names = ["journey.json", "journeys/index.json", "journeys/上海.json", "postcards.json",
             "notebook.json", "journal.json", "marks.json", "footprints.json", "sightings.json",
             "souvenirs.json", "buried.json", "travelers.json", "future/new-format.bin"]
    for name in names:
        path = storage.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\xff" + name.encode())
    assert storage.sync()
    assert not storage.sync()
    restored = R2Persistence(tmp_path / "fresh", storage.client, storage.bucket, storage.key)
    restored.restore()
    for name in names:
        assert (restored.home / name).read_bytes() == (storage.home / name).read_bytes()
    (restored.home / "postcards.json").unlink()
    assert restored.sync()
    storage.restore()
    assert not (storage.home / "postcards.json").exists()


def test_failed_write_keeps_previous_snapshot_and_can_retry(storage):
    storage.restore()
    path = storage.home / "journey.json"
    path.write_text("old")
    storage.sync()
    etag = storage.etag
    path.write_text("new")
    put = storage.client.put_object
    storage.client.put_object = lambda **kwargs: (_ for _ in ()).throw(OSError("offline"))
    with pytest.raises(PersistenceError):
        storage.sync()
    assert storage.etag == etag
    storage.client.put_object = put
    assert storage.sync()
    storage.restore()
    assert path.read_text() == "new"


def test_stale_instance_cannot_overwrite(storage, tmp_path):
    storage.restore()
    (storage.home / "state").write_text("first")
    storage.sync()
    stale = R2Persistence(tmp_path / "stale", storage.client, storage.bucket, storage.key)
    stale.restore()
    (storage.home / "state").write_text("latest")
    storage.sync()
    (stale.home / "state").write_text("stale")
    with pytest.raises(PersistenceError):
        stale.sync()
    stale.restore()
    assert (stale.home / "state").read_text() == "latest"


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/escape", "file"),
                                      ("symlink", "symlink")])
def test_unsafe_restore_preserves_local_home(storage, name, kind):
    storage.home.mkdir()
    (storage.home / "safe").write_text("keep")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "../escape"
        tar.addfile(member)
    storage.client.put_object(Bucket=storage.bucket, Key=storage.key, Body=buf.getvalue())
    with pytest.raises(PersistenceError):
        storage.restore()
    assert (storage.home / "safe").read_text() == "keep"


def test_missing_bucket_and_corrupt_archive_fail_closed(storage):
    storage.bucket = "missing-bucket"
    with pytest.raises(PersistenceError):
        storage.restore()
    storage.bucket = "nowhere-test"
    storage.client.put_object(Bucket=storage.bucket, Key=storage.key, Body=b"not an archive")
    with pytest.raises(tarfile.ReadError):
        storage.restore()
    assert not storage.ready


def test_config_must_not_silently_disable_r2(monkeypatch):
    for name in ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PersistenceError, match="Missing R2"):
        R2Persistence.from_env()
