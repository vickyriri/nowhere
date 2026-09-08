import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

from nowhere.persistence import run_background_job
from nowhere.remote import DurableRequests


@pytest.mark.asyncio
async def test_shared_world_serializes_and_waits_for_postcard_jobs(tmp_path):
    class State:
        count = 0
        def to_dict(self):
            return {"count": self.count}
        def save(self):
            (tmp_path / "state").write_text(json.dumps(self.to_dict()))

    state = State()
    commits = []

    def sync():
        commits.append({p.name: p.read_text() for p in tmp_path.iterdir()})

    async def endpoint(scope, receive, send):
        old = state.count
        await asyncio.sleep(.01)
        state.count = old + 1
        number = state.count
        run_background_job(lambda: (tmp_path / f"card-{number}").write_text("rendered"))
        await JSONResponse({"count": number})(scope, receive, send)

    app = DurableRequests(endpoint, SimpleNamespace(sync=sync), SimpleNamespace(_state=state))

    async def request():
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            # Even response headers must wait for all of this action's writes.
            assert f"card-{state.count}" in commits[-1]
            assert json.loads(commits[-1]["state"])["count"] == state.count
        await app({"type": "http", "path": "/postcard", "method": "POST"}, receive, send)

    await asyncio.gather(request(), request())
    assert state.count == 2
    assert len(commits) == 2
    assert "card-2" not in commits[0]
    assert "card-1" in commits[1] and "card-2" in commits[1]


@pytest.mark.asyncio
async def test_local_save_failure_is_retried_before_next_action(tmp_path):
    class State:
        count = 0
        fail = True
        def to_dict(self):
            return {"count": self.count}
        def save(self):
            if self.fail:
                raise OSError("temporary disk error")
            (tmp_path / "state").write_text(str(self.count))
    state = State()
    commits = []
    async def endpoint(scope, receive, send):
        state.count += 1
        await JSONResponse({"ok": True})(scope, receive, send)
    app = DurableRequests(endpoint, SimpleNamespace(sync=lambda: commits.append(
        (tmp_path / "state").read_text())), SimpleNamespace(_state=state))
    async def request():
        messages = []
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            messages.append(message)
        await app({"type": "http", "path": "/action", "method": "POST"}, receive, send)
        return messages[0]["status"]
    assert await request() == 503
    assert await request() == 503
    assert state.count == 1
    state.fail = False
    assert await request() == 200
    assert commits == ["1", "2"]
