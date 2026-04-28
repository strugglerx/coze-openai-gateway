# coze-openai-gateway

[English README](./README_EN.md) 

把 **Coze Plus 智能体**（`POST /v3/chat`）包装成 **OpenAI 兼容** 的 `/v1/chat/completions` + `/v1/models`，让任何 OpenAI 客户端只需改 `base_url` 就能接上 Coze；通过 `model` 字段切换不同机器人（客服/写作/代码…）。

```
OpenAI Client  ──►  coze-openai-gateway  ──►  Coze /v3/chat  ──►  bot (工作流/插件/提示词)
      ▲                                                            │
      └────────────────── SSE / JSON ──────────────────────────────┘
```

---

## 特性

- **OpenAI 兼容**：`POST /v1/chat/completions`、`GET /v1/models`，可直接对接 `openai` SDK、LangChain、Dify、Cursor 自定义模型等。
- **流式 & 非流式**：均支持。内部始终向上游 `stream=true` 发起，按客户端请求决定**透传 SSE**或**聚合成整段 JSON**（回避 Coze `/v3/chat` 非流式的异步轮询）。
- **两种模式**：
  - `mapped`（默认）—— 服务端用 `BOT_CONFIG` 把 `model` 名映射到 `bot_id`，统一 PAT 兜底，适合给团队/下游应用稳定接入。
  - `passthrough` —— 零配置启动；客户端 `Authorization` 即 PAT、`model` 即 `bot_id`，代理只做协议翻译，像 OpenAI→Coze 的"透明转接口"。
- **可选 CORS**：可通过环境变量开启浏览器跨域（默认关闭，避免误暴露）。
- **探活脚本**：`test/smoke.sh` 用 `curl` + `jq` 打 `/health`、`/v1/models`、`/v1/chat/completions`，无 venv。
- **统一响应协议**（可选，[docs/统一响应协议.md](./docs/统一响应协议.md)）：`X_AGENT_PROTOCOL=1` 时，在标准结果上增加 **`x_agent`**（非流式）与流式 **`object: "agent.event"`**。**默认关闭**，因 **Cherry Studio** 等客户端会对**每条** SSE 校验为 `chat.completion.chunk` 或 `error`，注入 `agent.event` 会触发 `AI_TypeValidationError`。自研前端需要时再开环境变量，或单次请求头 `X-Coze-X-Agent: 1`；若全局已开可用 `X-Coze-X-Agent: 0` 关闭。`X_AGENT_MAX_STREAM_EVENTS` 限制流式扩展条数。

---

## 支持的上游

任何实现了 Coze Plus **Chat V3** 协议（`POST /v3/chat`，SSE 事件含 `conversation.message.delta`、`conversation.chat.completed` 等）的后端，包括：

- `https://api.coze.cn`（字节官方国内版）
- Coze Plus 企业私有化部署
- 自建的 Chat V3 兼容网关

---

## 架构


| 文件            | 行数   | 职责                                                                                                    |
| ------------- | ---- | ----------------------------------------------------------------------------------------------------- |
| `app.py`      | ~120 | FastAPI 路由（`/v1/models`、`/v1/chat/completions`、`/health`）+ Bearer 回落 + 错误包装                           |
| `config.py`   | ~70  | `.env` → `Settings` dataclass，模块级单例                                                                   |
| `mapping.py`  | ~150 | 纯函数：`model→bot_id`、OpenAI `messages` → Coze `additional_messages`、OpenAI `chat.completion[.chunk]` 工厂 |
| `upstream.py` | ~250 | `httpx` 调 `/v3/chat`（始终 SSE） + 事件解析 + 聚合/透传 + 可选 `agent.event`                                           |
| `x_agent_map.py` | — | Coze SSE → 统一协议 `x_agent` / `agent.event` 映射                                                                 |


其余：`requirements.txt` / `.env.example` / `Makefile` / `test/smoke.sh`。

---

## 快速开始

### A. passthrough 模式 — **零配置**，开箱即用

客户端把自己的 PAT 作为 `Authorization`、`bot_id` 作为 `model` 直接传，服务端不预配任何机器人。上游默认打 `https://api.coze.cn`。

```bash
make install
make run-pt                               # 等同 MODE=passthrough make run（零配置）

# 换个终端（不用管 make 里跑啥，任意 OpenAI 客户端）
curl -s http://localhost:38419/v1/chat/completions \
  -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "7628929501656973364",
    "messages": [{"role":"user","content":"ping"}]
  }'
```

也可以写进 `.env` 以便开机自启：

```env
MODE=passthrough
# COZE_API_BASE 默认 https://api.coze.cn；私有化部署才需改
# COZE_API_BASE=https://your-coze.example.com
```

### B. mapped 模式 — 服务端预配 bot 路由

```bash
make install
make env                    # cp .env.example .env
vi .env                     # 填 COZE_API_BASE / COZE_API_KEY / BOT_ID
make run                    # 普通模式（改代码需手动重启）
make run-dev                # 开发模式：--reload，改 .py 自动重启
# 自定义端口：make run PORT=8080
```

不走 Make 也行：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 38419
```

---

## 环境变量

**程序怎么读到这些值**

启动时 `config.py` 会执行一次 `load_dotenv(仓库根/.env)`：把该文件里 `键=值` 写入**当前进程**的环境。随后所有 `MODE`、`COZE_API_BASE` 等一律用 `os.getenv(...)` 读取。因此配置来源可以是下面任意一种或组合，而不是「只能写 `.env`」。

- `**.env`（仓库根，已被 `.gitignore`）**：适合本地/服务器落盘，不在命令里暴露密钥。没有 `.env` 时，只要环境里已有同名变量，程序照样能跑（例如 `make run-pt` 在 Makefile 里写死了 `MODE=passthrough`，不依赖文件）。
- **已存在的环境变量优先**：`python-dotenv` 默认**不覆盖**启动前已在进程里出现的变量。因此 `COZE_API_BASE=https://x.com make run`、在 shell 里 `export ...`、systemd 的 `Environment=`、Docker 的 `-e` / `environment:` 所设的键，会压过同名的 `.env` 行。需要临时改一项时用前缀变量最方便。
- `**PORT` 在 Makefile 里**：`make run PORT=8080` 只把 `8080` 传给**uvicorn 的 `--port`**；`config.py` 不读 `PORT`，和表格里其它用 `os.getenv` 的项不是同一条链。用 Docker 时见根目录 `Dockerfile`：`uvicorn` 用环境变量 `PORT`（默认 `38419`）。


| 变量                | 说明                                                                                                                                                                           | 默认                                                 |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `MODE`            | `mapped` / `passthrough`（见"两种模式"）                                                                                                                                            | `mapped`                                           |
| `COZE_API_BASE`   | 上游根 URL，含 `https`、不含尾斜杠                                                                                                                                                      | `mapped` 必填；`passthrough` 默认 `https://api.coze.cn` |
| `COZE_CHAT_PATH`  | Chat V3 路径                                                                                                                                                                   | `/v3/chat`                                         |
| `COZE_USER_ID`    | 上游必填 `user_id`（多租户数据隔离）                                                                                                                                                      | `openai-proxy`                                     |
| `COZE_API_KEY`    | Coze PAT，客户端未传 `Authorization` 时作为 fallback（仅 `mapped`）                                                                                                                      | —（建议填）                                             |
| `BOT_ID`          | 未命中 `BOT_CONFIG` 时的默认 bot（仅 `mapped`）                                                                                                                                        | —（强烈建议填）                                           |
| `DEFAULT_MODEL`   | 客户端未传 `model` 时使用的逻辑名（仅 `mapped`）                                                                                                                                            | `coze`                                             |
| `BOT_CONFIG`      | 路由表 JSON（仅 `mapped`）。支持两种形态：① 扁平 map `{"客服":"7628..."}`；② 分组数组 `[{"group":"住建局","models":{"客服":"7628..."}}]`（`/v1/models` 的 `id` 会输出 `{group}-{model}`，`owned_by` 为 `group`） | `{}`                                               |
| `BOT_CONFIG_FILE` | 指向 JSON 文件（相对仓库根或绝对路径），优先级高于 `BOT_CONFIG` 环境变量；适合多行维护                                                                                                                        | —                                                  |
| `PORT`            | 监听端口（传给 Makefile）                                                                                                                                                            | `38419`                                            |
| `LOG_LEVEL`       | `coze_proxy.*` 日志级别（如 `DEBUG`、`INFO`）                                                                                                                                        | `INFO`                                             |
| `COZE_LOG_SSE`    | `true` 时 DEBUG 打印每条上游 SSE 的 `event` 名（量较大）                                                                                                                                   | （关）                                                |
| `X_AGENT_PROTOCOL` | 是否返回 `x_agent` / 流式 `agent.event`；**Cherry 等需保持关或默认**                                                                                                        | 关（`0`）                                              |
| `X_AGENT_MAX_STREAM_EVENTS` | 流式时最多输出多少条 `agent.event`                                                                                                                                       | `40`                                               |
| `CORS_ENABLED` | 是否启用 CORS 中间件（浏览器跨域） | 关（`0`） |
| `CORS_ALLOW_ORIGINS` | 允许来源（逗号分隔或 JSON 数组） | `*` |
| `CORS_ALLOW_ORIGIN_REGEX` | 允许来源的正则（可选） | — |
| `CORS_ALLOW_METHODS` | 允许方法（逗号分隔或 JSON 数组） | `*` |
| `CORS_ALLOW_HEADERS` | 允许请求头（逗号分隔或 JSON 数组） | `*` |
| `CORS_EXPOSE_HEADERS` | 暴露给浏览器的响应头（逗号分隔或 JSON 数组） | — |
| `CORS_ALLOW_CREDENTIALS` | 是否允许携带凭据（Cookie/Authorization） | 关（`0`） |
| `CORS_MAX_AGE` | 预检请求缓存秒数 | `600` |


### 最小可跑的 `.env`

```env
COZE_API_BASE=https://api.coze.cn
COZE_API_KEY=pat_xxxxxxxxxxxxxxxxxx
COZE_USER_ID=openai-proxy

BOT_ID=7628929501656973364
DEFAULT_MODEL=coze
BOT_CONFIG={}

PORT=38419
```

---

## `model` 路由规则

客户端 `"model": "X"` → 代理实际用哪个 `bot_id`？

**passthrough 模式**

```
1. 请求体必须带 model，且去空白后非空 ⇒ bot_id = model
   否则 400 Bad Request
2. Authorization: Bearer <pat> 必须由客户端传，否则 401
```

**mapped 模式**

```
1. 取有效 model:
   - 请求体有 model    ⇒ X = 去空白(model)
   - 否则有 DEFAULT_MODEL ⇒ X = DEFAULT_MODEL
   - 都没有            ⇒ 400 Bad Request

2. 解析 bot_id:
   - X 是 BOT_CONFIG 的键 ⇒ bot_id = BOT_CONFIG[X]
   - 否则若 X 形如 `{group}-{name}` 且能剥前缀命中 ⇒ bot_id = BOT_CONFIG[name]
   - 否则                 ⇒ bot_id = BOT_ID
   - BOT_ID 还为空        ⇒ 500 (请检查 .env)

3. Authorization: Bearer <pat> 客户端优先；没有则用 .env 的 COZE_API_KEY
```

### `BOT_CONFIG` 例子

**单机器人**（最常见）：

```env
BOT_ID=7628929501656973364
DEFAULT_MODEL=coze
BOT_CONFIG={}
```

任何 `model` 都走 `BOT_ID`。

**多机器人**（shell 里用 **单引号** 包住 JSON，避免双引号被吃）：

```env
BOT_ID=7628929501656973364
DEFAULT_MODEL=kefu
BOT_CONFIG='{"kefu":"7628929501656973364","writer":"7345111111111111","coder":"7345222222222222"}'
```


| 请求 `"model"` | 实际 bot_id             | 备注                                 |
| ------------ | --------------------- | ---------------------------------- |
| `"kefu"`     | `7628929501656973364` | 命中 `BOT_CONFIG["kefu"]`            |
| `"writer"`   | `7345111111111111`    | 命中 `BOT_CONFIG["writer"]`          |
| `"gpt-4o"`   | `7628929501656973364` | 未命中 → 回落 `BOT_ID`（方便对接只认固定模型名的客户端） |
| 不传 `model`   | `7628929501656973364` | 取 `DEFAULT_MODEL=kefu` → 命中        |


`GET /v1/models`：

- `mapped` 模式：`DEFAULT_MODEL` + `BOT_CONFIG` 里所有逻辑 `model` 名（去重、保序）；若使用分组数组，则每条 `id` 为 `**{group}-{model}**`，`owned_by` 为 `**group**`（部分客户端如 Cherry 会按 `id` 前缀聚类）。
- `passthrough` 模式返回空列表（model 即 bot_id，服务端无从枚举）。

---

## API

### `GET /v1/models`

```json
{
  "object": "list",
  "data": [
    { "id": "support-kefu",   "object": "model", "created": 0, "owned_by": "support" },
    { "id": "support-writer", "object": "model", "created": 0, "owned_by": "support" },
    { "id": "support-coder",  "object": "model", "created": 0, "owned_by": "support" }
  ]
}
```

### `POST /v1/chat/completions`

请求体支持字段：


| 字段                | 含义                                                               |
| ----------------- | ---------------------------------------------------------------- |
| `model`           | 逻辑名（见上节路由规则）                                                     |
| `messages`        | OpenAI 标准；`system` 会合并到首条 `user` 前（因 Coze V3 不直接吃 system）        |
| `stream`          | `true`=SSE，`false`=完整 JSON（代理内部仍走 SSE 再聚合）                       |
| `user`            | 可选；作为上游 `user_id`。未传则用 `COZE_USER_ID`                            |
| `conversation_id` | **OpenAI 扩展字段**；会映射为上游 `ConversationID`/`conversation_id`，用于续接会话 |

**怎么传：** `user` **可选**。传了即作为 Coze 的 `user_id`，用来区分终端用户（多租户隔离）；**不传**则本条请求一律用 `.env` 里的 **`COZE_USER_ID`**。`conversation_id` **可选**：续聊时在**同一业务用户**下沿用上一轮会话 ID（见 **续接会话**）；首轮或未做多轮上下文可省略。

**被忽略的字段**（Coze 生成行为完全由控制台 Bot 配置决定，不做假装兼容）：
`temperature` / `top_p` / `max_tokens` / `stop` / `frequency_penalty` / `presence_penalty` / `n` / `logit_bias` / `tools` / `functions`

### `GET /health`

```json
{ "ok": true, "mode": "passthrough", "upstream_configured": true }
```

---

## 客户端示例

### Python (`openai` SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:38419/v1",
    api_key="pat_xxxxxxxxxxxxxxxxxx",   # Coze PAT；若代理 .env 已配 fallback，填任意非空串也行
)

resp = client.chat.completions.create(
    model="kefu",
    messages=[
        {"role": "system", "content": "用中文回答"},
        {"role": "user", "content": "你好"},
    ],
)
print(resp.choices[0].message.content)

for chunk in client.chat.completions.create(
    model="kefu",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### 续接会话

```python
client.chat.completions.create(
    model="kefu",
    extra_body={"conversation_id": "7631949064133591083"},
    messages=[{"role": "user", "content": "继续上一个话题"}],
)
```

### curl

```bash
curl -s http://localhost:38419/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxx" \
  -d '{
    "model": "kefu",
    "user": "your-end-user-id",
    "messages": [{"role": "user", "content": "ping"}],
    "stream": false
  }'
```

---

## Docker

仓库根目录已提供 `Dockerfile`（默认监听 `38419`，可用环境变量 `PORT` 覆盖）。

```bash
docker build -t coze-openai-gateway .
docker run --rm -p 38419:38419 --env-file .env coze-openai-gateway
# 或换端口（容器内外都一致）
docker run --rm -p 8080:8080 --env-file .env -e PORT=8080 coze-openai-gateway
```

同目录有 `docker-compose.yml`：宿主机/容器端口**写死在本文件**（默认 `38419:38419`；勿依赖 `.env` 的 `PORT`）。需先有仓库根 `.env`（`make env` 或自建）。`make compose` / `make compose-down`。镜像离线包：`make image-tar`（默认 `coze-openai-gateway.image.tar`），对端 `docker load -i …` 导入。

离线交付可用：`make export-linux-amd64-zip`，会产出 `dist/coze-openai-gateway-linux-amd64.zip`，内含镜像 tar、`.env.example`、`.docker-compose.yml`、`start-server.sh`、`stop-server.sh`。在目标机器解压后：

```bash
./start-server.sh    # 可选参数：镜像 tar 路径
./stop-server.sh
```

---

## 故障排查


| 现象                                                                | 定位 / 处理                                                                                  |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `401 Missing Authorization: Bearer <coze_pat> (passthrough mode)` | passthrough 必须由客户端传 PAT，不走 `.env` 兜底                                                     |
| `401 Missing Authorization: Bearer and COZE_API_KEY fallback`     | mapped 模式客户端没带 `Authorization`，`.env` 也没 `COZE_API_KEY`                                  |
| `400 Missing model: in passthrough mode ...`                      | passthrough 下 `model` 必传且就是 bot_id                                                       |
| `400 Missing model and DEFAULT_MODEL not set`                     | mapped 模式请求未带 `model`，`.env` 也没配 `DEFAULT_MODEL`                                         |
| `500 No bot_id: set BOT_ID or BOT_CONFIG for this model`          | mapped 下 `BOT_ID` 空且未命中 `BOT_CONFIG`                                                     |
| `502 The token you entered is incorrect...`                       | PAT 错/过期，更新 `.env` 或客户端 Authorization                                                    |
| `200` 但 `content` 为空字符串                                           | Bot 未发布到该 PAT 所在空间，或 Bot 本身没输出 `type=answer` 的消息；看代理日志里的 `conversation.message.delta` 事件 |
| Cherry Studio `AI_TypeValidationError` / `invalid_union`（`choices` 或 `error`） | 流式里不能夹带 `agent.event`。**不要**开 `X_AGENT_PROTOCOL`，或设 `0`；若全局已开则加请求头 `X-Coze-X-Agent: 0` |
| 开了「随机建议问题」等导致**正文都出完了还要等一会** `stream` 才结束 | Coze 还会在 `conversation.chat.completed` 后继续推尾部事件。**标准模式**（关 `X_AGENT`，无 `agent.event`）下，代理在 `chat.completed` 后即结束 SSE、发 `[DONE]`，不再等建议流；需要过程/建议时请开 `X_AGENT_PROTOCOL`（或头 `X-Coze-X-Agent: 1`） |
| 504/超时                                                            | 上游生成慢；如机器人触发了长工作流，考虑将客户端 timeout 调大                                                      |


排障流程：

```bash
# 实时日志
nohup .venv/bin/uvicorn app:app --host 0.0.0.0 --port 38419 > /tmp/coze-proxy.log 2>&1 &
tail -f /tmp/coze-proxy.log

# 绕过代理，直接看上游 SSE 是否出文本
curl -sN -X POST https://api.coze.cn/v3/chat \
  -H "Authorization: Bearer pat_xxx" \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"76289...","user_id":"dbg","stream":true,"additional_messages":[{"role":"user","content":"hi","content_type":"text"}]}'
```

---

## 安全

- `.env` 已在 `.gitignore`，**勿提交 PAT 到任何仓库**。
- **PAT 一旦出现在聊天记录、截图、日志、公开仓库历史里，即视为泄漏**——到 Coze 控台立即作废并重新签发，不要指望撤回或删除消息。
- 生产部署建议：代理跑在内网，PAT 只存在服务器；或让客户端各自带 PAT、代理不配 fallback，以便最小权限追责。
- 日志脱敏：避免直接打 `messages` 原文与完整 PAT。

---

## License

本项目以 **[MIT](./LICENSE)** 许可证发布。