# Render Free + remote MCP + Cloudflare R2

## 中文快速部署

1. 把这次改动放进你自己的 GitHub fork。
2. 在 Cloudflare R2 创建一个私有、Standard 存储桶，并生成仅限这个桶的
   **Object Read & Write** S3 密钥。保留 endpoint、Access Key ID 和 Secret Access Key。
3. 在 Render 选择 **New → Blueprint**，连接 fork 和包含本次改动的分支。
   `render.yaml` 已明确选择 **Free**，没有持久磁盘或定时任务。
4. 在 Render 填入 `R2_ENDPOINT_URL`、`R2_ACCESS_KEY_ID`、
   `R2_SECRET_ACCESS_KEY`、`R2_BUCKET`。密钥只填环境变量，不提交 GitHub。
5. 部署成功后，网页是 `https://服务名.onrender.com/`，远程 MCP 地址是
   `https://服务名.onrender.com/mcp`。ChatGPT 使用后者。
6. 留下一个标记后重启 Render，再查询标记，做真实云端的最后验收。

默认未开启访问认证，拿到地址的人可以读写这个共同世界；可选令牌认证的
适用范围见下文。当前没有 OAuth 登录服务。

网页可见时每 4 秒刷新；隐藏、关闭后停止轮询。Render 从最后一次收到请求
算起约 15 分钟才休眠，MCP 客户端的工具发现请求也可能叫醒它。
本项目没有自我敲门、防休眠或定时同步。R2 同步发生在启动和数据变更时，
**同步成功后才返回动作结果**；失败会显示 503，不要直接重复刚才的动作。

下方包含完整配置表、错误恢复说明与测试命令。

Based on upstream `yuyixuanfu/nowhere` commit
`42a5dd4465b11187c9e1bbd96b721decff2dbe1f`.

## What runs where

One **Render Free Web Service**, one Python process, one shared personal world:

| Path | Purpose |
| --- | --- |
| `/` | Original web observer |
| `/mcp` | FastMCP Streamable HTTP; all 28 upstream tools |
| `/state`, `/message`, `/marks`, etc. | All existing HTTP API routes |
| `/static/` | Original assets and visibility-aware polling |
| `/health` | Render health check; does not read/write R2 |

R2 stores one private compressed snapshot of **every file and directory under
`NOWHERE_HOME`**. This includes active state, journeys and logs, postcards and
replies, notebook/journal data, marks, footprints, sightings, souvenirs, buried
items, travelers, and future file formats. Optional generated postcard images
are redirected into `NOWHERE_HOME/postcard_images` and served at their original
`/static/postcards/` URLs.

The original local `nowhere`, `nowhere --web` and stdio entry points still work
without installing or configuring R2. Only `nowhere.remote` requires R2.

## 1. Prepare R2

1. Create a **private R2 bucket**, using Standard storage. No public bucket URL
   or public-read access is needed.
2. Create R2 S3 credentials with **Object Read & Write**, scoped to that bucket.
3. Keep the S3 endpoint, Access Key ID and Secret Access Key for Render's
   environment settings. A general Cloudflare API token is not an S3 key pair.

[Cloudflare's S3 setup](https://developers.cloudflare.com/r2/get-started/s3/)
and [boto3 example](https://developers.cloudflare.com/r2/examples/aws/boto3/).

## 2. Create the Render service

Use a GitHub fork containing these changes. In Render choose **New → Blueprint**
and select the fork and the branch containing `render.yaml`. The Blueprint
explicitly uses `plan: free` and declares no persistent disk or cron service.

Alternatively create a Web Service manually:

| Setting | Value |
| --- | --- |
| Runtime | Python |
| Instance | **Free** |
| Python version | `3.12.13` |
| Build command | `pip install -e '.[remote]' -c requirements-remote.txt` |
| Start command | `python -m nowhere.remote` |
| Health check | `/health` |

The entry point listens on **`0.0.0.0:$PORT`**, defaults to port 8000 outside
Render, and runs exactly one worker. Do not add multiple workers, replicas,
uptime monitors, scheduled pings, cron jobs, or keep-alive services.

Set these variables in Render (never in GitHub):

| Variable | Required | Value |
| --- | --- | --- |
| `R2_ENDPOINT_URL` | Yes | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` (use the endpoint shown for your bucket/jurisdiction) |
| `R2_ACCESS_KEY_ID` | Yes | R2 S3 Access Key ID |
| `R2_SECRET_ACCESS_KEY` | Yes | R2 S3 Secret Access Key |
| `R2_BUCKET` | Yes | Private bucket name |
| `NOWHERE_HOME` | Blueprint sets it | `/tmp/nowhere`; ephemeral local working directory |
| `R2_SNAPSHOT_KEY` | No | Default `nowhere/snapshot.tar.gz`; use a different key for every separate world |
| `NOWHERE_PUBLIC_URL` | No | Public HTTPS base URL; defaults to Render's `RENDER_EXTERNAL_URL` |
| `NOWHERE_ACCESS_TOKEN` | No | Optional shared access token; see authentication below |
| `R2_MAX_SNAPSHOT_BYTES` | No | Uncompressed size cap; default `134217728` (128 MiB) |

Deploy and wait for `/health` to become ready. Missing credentials, an inaccessible
bucket, or a corrupt existing snapshot **fail startup**, rather than silently
starting an empty world and overwriting your progress.

The default install includes the small offline data shipped upstream. Do not
download optional multi-gigabyte terrain/GeoNames packages onto a Free instance.
The optional heavy map-poster dependencies are not installed by this Blueprint;
the observer keeps its original SVG postcard fallback.

## 3. Connect the remote MCP client

Use the exact public URL:

```text
https://<your-render-service>.onrender.com/mcp
```

Select **Streamable HTTP** in clients which ask for a transport. Do not use the
observer URL or the old HTTP API as the MCP endpoint. The endpoint is stateless
at the **protocol/session** level; the travel world is still persisted to R2.
No permanently open server-to-client SSE connection is needed. Existing
requests such as `initialize`, `tools/list` and `tools/call` work over HTTP.

For ChatGPT, add this URL through the custom MCP connection option available in
your account. This repository implements the MCP endpoint; it does not add an
OAuth authorization server. Account-specific availability and authentication
options must be checked in the actual ChatGPT connection UI.

### Optional authentication

The upstream observer/API is public. To preserve a simple compatible connection,
`NOWHERE_ACCESS_TOKEN` is unset by default. **In that mode anyone who can reach
the service can read and change its shared world.** Keep personal/sensitive
content out of an unauthenticated deployment.

If your MCP client supports a static Authorization header, set
`NOWHERE_ACCESS_TOKEN` and send `Authorization: Bearer <token>`. Browsers can open
the observer using HTTP Basic: any username and the token as password. This
protects the observer, API and MCP together; `/health` remains public. Cross-origin
browser writes are rejected when token protection is enabled.

If ChatGPT's connection dialog only offers OAuth or no authentication, the static
token option is **not** an OAuth substitute. Use the no-auth configuration only
if you accept its access model, or add an OAuth gateway separately. Do not paste
an R2 secret into ChatGPT's connection URL.

## Saving and sleeping

At startup the snapshot is downloaded and safely extracted **before importing
the game's storage modules or accepting requests**. An existing snapshot is
authoritative and replaces the ephemeral local directory. An empty bucket is a
new installation; a pre-populated local home can be imported on that first start.

Dynamic requests are serialized because upstream uses one global world. After
the action, the wrapper waits for any action-triggered postcard work, saves
changed in-memory state, checks the complete directory, and uploads only when
contents changed. Successful responses are released **after** the R2 write.
Plain observer reads do not cause R2 writes. File deletions are reflected by the
next snapshot, so deleted postcards do not reappear on restart.

A snapshot is replaced with one conditional object PUT. R2's `If-Match` /
`If-None-Match` support prevents an old instance from overwriting a newer
instance's save during overlapping deploys. On a conflict, stop using the old
instance and restart it to restore the latest snapshot; do not remove this
protection. Only one service should actively use a snapshot key.

If saving fails, the request returns **503**. The local action may already have
happened, so **do not repeat the action blindly**. Subsequent requests retry the
pending save before allowing another action. Fix credentials/connectivity, then
query state. A process killed before a successful commit can lose that
unacknowledged action; data already committed to R2 survives restarts. There is
no periodic retry task. Shutdown sync is only a final best effort.

The observer behaves as follows:

- Visible: immediate refresh, then the original five reads every 4 seconds.
- Hidden: interval cleared; unfinished polling reads aborted. No new polling.
- Visible again: one immediate refresh and polling resumes.
- Closed/navigated away: polling stops. Back/forward cache restoration resumes it.

Abort cannot undo a request already received by Render. A visible observer or
an MCP request (including a client's discovery/check) can keep/wake the service.
After **15 minutes without incoming traffic**, Render Free can sleep; hiding the
page does not instantly stop billable runtime. There are no application cron
jobs, self-pings, R2 polling, or anti-sleep mechanisms in this deployment.
[Render's Free service rules](https://render.com/docs/free).

The tradeoff of this small adaptation is a full compressed snapshot upload per
changed request. Large histories/images increase Render outbound traffic and R2
operations; it is not an unlimited-free-storage promise. See
[R2 pricing](https://developers.cloudflare.com/r2/pricing/). The configurable size
and entry limits fail visibly rather than silently dropping files.

## Verification

```bash
pip install -e '.[remote,dev]' -c requirements-remote.txt 'moto[s3,server]>=5,<6'
pytest -q tests
node --test tests/test_observer_polling.cjs
```

Tests use a real separate Uvicorn process, actual HTTP MCP initialize/tools/list,
the FastMCP client, and a local S3-compatible test backend. They check tool names,
observer/API routes, failed-save responses and retry without duplicate actions,
token authentication, and restoration after stopping the process and deleting
its entire working directory. Storage tests cover unknown/binary/nested files,
deletions, stale writers, corrupted/unsafe archives and missing configuration.
JavaScript tests cover visibility, cancellation, slow requests and page lifecycle.

These automated tests do **not** replace an end-to-end check against your actual
R2 bucket, Render service or ChatGPT account. After deploying:

1. Open `/`, connect `/mcp`, and ask for the tool list (28 tools for this upstream).
2. Open a door and create a named mark; record the position and name.
3. Confirm R2 contains the snapshot object. Keep observer visible to watch updates;
   switch tabs and check browser Network logs to confirm polling pauses.
4. Use Render's restart action. Reopen the observer and query marks through MCP.
   Position and the named mark should remain. Also check a postcard or notebook
   entry if one was created.

For backups, download the private snapshot from the R2 dashboard. It is a standard
`.tar.gz` archive containing the original Nowhere file formats, not a new database.
Never configure a lifecycle rule that expires this live snapshot.
