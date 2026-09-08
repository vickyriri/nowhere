import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import boto3
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from moto.server import ThreadedMotoServer
import httpx


UPSTREAM_TOOLS = set("""open_door continue_journey walk listen look_around ask mark
marks where_am_i souvenir give_souvenir bury deliver postcards walk_to journeys_list
atlas wait look say quotes talk journal notebook walk_alone guess reveal drift""".split())


@contextmanager
def remote_process(tmp_path, endpoint, token="", abrupt=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, NOWHERE_HOME=str(tmp_path / "home"), PORT=str(port),
               TEST_S3_ENDPOINT=endpoint, NOWHERE_ACCESS_TOKEN=token)
    # Only the object-storage transport is substituted. Run the real production
    # main(), HTTP listener, FastMCP, observer and storage in a separate process.
    script = '''
import os
from pathlib import Path
import boto3
from nowhere.persistence import R2Persistence
from nowhere.remote import main
def store(cls):
    client = boto3.client("s3", endpoint_url=os.environ["TEST_S3_ENDPOINT"],
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    return cls(Path(os.environ["NOWHERE_HOME"]), client, "nowhere-test", "snapshot.tar.gz")
R2Persistence.from_env = classmethod(store)
main()
'''
    log_path = tmp_path / "server.log"
    with log_path.open("a") as log:
        process = subprocess.Popen([sys.executable, "-c", script], env=env,
                                   stdout=log, stderr=log)
        url = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(timeout=1, trust_env=False) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError(log_path.read_text())
                    try:
                        if client.get(url + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise AssertionError("Startup timed out: " + log_path.read_text())
            yield url
        finally:
            if abrupt:
                process.kill()
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_real_http_mcp_and_process_restart(tmp_path):
    backend = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    backend.start()
    host, port = backend.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                      aws_access_key_id="testing", aws_secret_access_key="testing")
    s3.create_bucket(Bucket="nowhere-test")
    home = tmp_path / "home"
    home.mkdir()
    (home / "journey.json").write_text(json.dumps({"pos": [31.23, 121.47],
        "place_name": "上海", "landed_at": "2026-09-08T10:00:00+00:00"}))
    (home / "future").mkdir()
    (home / "future" / "unknown.bin").write_bytes(b"\x00\xffdurable")
    try:
        with remote_process(tmp_path, endpoint, abrupt=True) as url, httpx.Client(base_url=url, timeout=15, trust_env=False) as http:
            assert http.get("/").status_code == 200
            assert "createVisiblePoller" in http.get("/static/observer-polling.js").text
            assert http.get("/state").json()["pos"] == [31.23, 121.47]
            headers = {"Accept": "application/json, text/event-stream"}
            assert http.get("/mcp", headers=headers).status_code == 405
            init = http.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                "method": "initialize", "params": {"protocolVersion": "2024-11-05",
                "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
            assert init.status_code == 200, init.text
            assert init.json()["result"]["serverInfo"]["name"] == "nowhere"
            listing = http.post("/mcp", headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            assert listing.status_code == 200, listing.text
            raw_tools = listing.json()["result"]["tools"]
            assert {tool["name"] for tool in raw_tools} == UPSTREAM_TOOLS

            async def exercise_mcp():
                transport = StreamableHttpTransport(url + "/mcp", httpx_client_factory=
                    lambda **kwargs: httpx.AsyncClient(trust_env=False, **kwargs))
                async with Client(transport) as client:
                    tools = await client.list_tools()
                    assert {t.name for t in tools} == UPSTREAM_TOOLS
                    result = await client.call_tool("mark", {"name": "猫的落脚点", "note": "重启后还在"})
                    assert not result.is_error
            asyncio.run(exercise_mcp())
            assert http.post("/message", json={"content": "下一站见"}).status_code == 200
            assert http.post("/mark", json={"name": "HTTP API 标记"}).status_code == 200
            saved_marks = http.get("/marks").json()
            assert len(saved_marks) == 2
            previous_etag = s3.head_object(Bucket="nowhere-test", Key="snapshot.tar.gz")["ETag"]
            # Observer reads must not generate object writes or background sync.
            for path in ["/state", "/marks", "/history", "/sightings", "/postcards"]:
                assert http.get(path).status_code == 200
            assert s3.head_object(Bucket="nowhere-test", Key="snapshot.tar.gz")["ETag"] == previous_etag
            # Simulate lost write permission. No mutation is reported as saved.
            s3.put_bucket_policy(Bucket="nowhere-test", Policy=json.dumps({"Version":"2012-10-17",
                "Statement":[{"Effect":"Deny","Principal":"*","Action":"s3:PutObject",
                              "Resource":"arn:aws:s3:::nowhere-test/*"}]}))
            failed = http.post("/message", json={"content": "只执行一次"})
            assert failed.status_code == 503, failed.text
            assert http.post("/message", json={"content": "不应执行"}).status_code == 503
            s3.delete_bucket_policy(Bucket="nowhere-test")
            messages = http.get("/messages").json()
            assert [m["content"] for m in messages] == ["下一站见", "只执行一次"]

        shutil.rmtree(home)  # Render's ephemeral filesystem is completely gone.
        with remote_process(tmp_path, endpoint, token="test-secret") as url:
            with httpx.Client(base_url=url, timeout=15, trust_env=False) as http:
                assert http.get("/").status_code == 401
                assert http.get("/health").status_code == 200
                assert http.get("/", auth=("nowhere", "test-secret")).status_code == 200
                http.headers["Authorization"] = "Bearer test-secret"
                assert http.get("/state").json()["pos"] == [31.23, 121.47]
                assert http.get("/marks").json() == saved_marks
                assert len(http.get("/messages").json()) == 2
                assert http.post("/message", headers={"Origin": "https://other.example"},
                                 json={"content": "blocked"}).status_code == 403
                assert (home / "future" / "unknown.bin").read_bytes() == b"\x00\xffdurable"
    finally:
        backend.stop()
