# coze-openai-gateway

[中文 README](./README.md)

**coze-openai-gateway** wraps **Coze Plus agents** (`POST /v3/chat`, Chat V3) behind an **OpenAI-compatible** surface:

- `POST /v1/chat/completions`
- `GET /v1/models`
- `GET /health`

Point any OpenAI client at this service by changing `base_url`, and route different bots via the `model` field.

```
OpenAI Client  ──►  coze-openai-gateway  ──►  Coze /v3/chat  ──►  bot (workflow/plugins/prompts)
      ▲                                                            │
      └────────────────── SSE / JSON ──────────────────────────────┘
```

---

## Features

- **OpenAI wire compatibility** for common integrations (OpenAI SDK, LangChain, Dify, Cursor custom models, etc.).
- **Streaming & non-streaming**: supported. The proxy **always** calls upstream with `stream=true`, then either **streams SSE through** or **aggregates** into a non-streaming JSON response (avoids Coze non-stream async polling complexity).
- **Two modes**:
  - **`mapped` (default)**: server-side routing via `BOT_CONFIG` / `BOT_ID` / `DEFAULT_MODEL`, with optional `COZE_API_KEY` fallback when clients omit `Authorization`.
  - **`passthrough`**: “pure proxy” mode: client sends `Authorization: Bearer <PAT>` and sets `model` to the **Coze `bot_id`**. Minimal/no server-side bot configuration.
- **Smoke testing**: `test/smoke.sh` uses `curl` + `jq` (no Python venv required), if present in your checkout.
- **Unified agent response extension** (optional, see `docs/统一响应协议.md` if present): with `X_AGENT_PROTOCOL=1`, adds **`x_agent`** and streaming **`object: "agent.event"`** lines. **Off by default** because clients like **Cherry Studio** validate every SSE `data` line as OpenAI `chat.completion.chunk` (with `choices`) or `error`—`agent.event` lines cause `AI_TypeValidationError`. Enable only for custom UIs, or per-request: header **`X-Coze-X-Agent: 1`** to turn on, **`0`** to force off. `X_AGENT_MAX_STREAM_EVENTS` caps stream extension lines.

---

## Supported upstreams

Any deployment implementing Coze Plus **Chat V3** (`POST /v3/chat`, SSE events such as `conversation.message.delta`, `conversation.chat.completed`, etc.), including:

- `https://api.coze.cn` (ByteDance-hosted China endpoint)
- Private Coze Plus / compatible gateways
- Self-hosted Chat V3-compatible relays

---

## Architecture

| File | Approx lines | Responsibility |
|---|---:|---|
| `app.py` | ~120–240 | FastAPI routes (`/v1/models`, `/v1/chat/completions`, `/health`), bearer resolution, error shaping |
| `config.py` | ~100–200 | `.env` → `Settings` singleton, `BOT_CONFIG` parsing (flat map or grouped array + optional file) |
| `mapping.py` | ~150–200 | Pure transforms: model routing, OpenAI messages → Coze `additional_messages`, OpenAI response factories |
| `upstream.py` | ~170–280 | `httpx` upstream calls + SSE parsing + aggregate/pass-through + optional `agent.event` |
| `x_agent_map.py` | — | Coze SSE → unified `x_agent` / `agent.event` mapping |

Also: `requirements.txt`, `.env.example`, `Makefile`, `Dockerfile`.

---

## Quickstart

### A) `passthrough` mode — zero server-side bot config

The client supplies the PAT and uses `model` as the Coze `bot_id`. Upstream defaults to `https://api.coze.cn`.

```bash
make install
make run-pt

# In another terminal
curl -s http://localhost:38419/v1/chat/completions \
  -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "7628929501656973364",
    "messages": [{"role":"user","content":"ping"}]
  }'
```

Optional `.env` for convenience:

```env
MODE=passthrough
# COZE_API_BASE defaults to https://api.coze.cn unless you override it
# COZE_API_BASE=https://your-coze.example.com
```

Makefile equivalents:

```bash
make run-pt-dev   # passthrough + --reload
```

### B) `mapped` mode — server-side bot routing

```bash
make install
make env          # cp .env.example -> .env if missing
vi .env           # set COZE_API_BASE / COZE_API_KEY / BOT_ID / BOT_CONFIG ...
make run          # production-ish (no auto-reload)
make run-dev      # dev (--reload)
# make run PORT=8080
```

Without Make:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 38419
```

---

## Environment variables

**How settings are read**

On import, `config.py` runs `load_dotenv` against **repo-root** `.env`, then every setting is read with `os.getenv(...)`. You can use `.env`, pure process env, or a mix.

- **`.env`**: optional file for local/servers. If it’s missing, values can still come from the environment (e.g. `make run-pt` sets `MODE=passthrough` in the Makefile and does not require `.env`).
- **Pre-set env wins over `.file` (default dotenv behavior)**: variables already present in the process environment when Python starts are **not** replaced by the same key from `.env`. So `COZE_API_BASE=https://x.com make run`, `export ...`, systemd `Environment=`, and Docker `-e` / `environment:` override `.env` for those keys. Use a leading `VAR=value` when you need a one-off override.
- **Makefile `PORT=...`**: only goes to **uvicorn’s `--port`**. The Python `Settings` object in `config.py` does not read `PORT` (it’s a separate concern from the `os.getenv(...)`-backed table below). The root `Dockerfile` runs `uvicorn ... --port ${PORT:-38419}` in the container.

| Variable | Meaning | Default |
|---|---|---|
| `MODE` | `mapped` / `passthrough` | `mapped` |
| `COZE_API_BASE` | Upstream origin URL (`https...`, no trailing `/`) | required in `mapped`; defaults to `https://api.coze.cn` in `passthrough` |
| `COZE_CHAT_PATH` | Chat V3 path | `/v3/chat` |
| `COZE_USER_ID` | Upstream-required `user_id` | `openai-proxy` |
| `COZE_API_KEY` | PAT fallback when client omits `Authorization` (**`mapped` only**) | unset |
| `BOT_ID` | Default `bot_id` when `model` misses `BOT_CONFIG` (**`mapped` only**) | unset |
| `DEFAULT_MODEL` | Default logical `model` when request omits `model` (**`mapped` only**) | `coze` |
| `BOT_CONFIG` | Routing table JSON (**`mapped` only**). Two supported shapes: (1) flat map `{"kefu":"7628..."}`; (2) grouped array `[{"group":"support","models":{"kefu":"7628..."}}]`. For grouped configs, `/v1/models` returns `id = "{group}-{model}"` and `owned_by = "{group}"`. | `{}` |
| `BOT_CONFIG_FILE` | Path to JSON file (repo-relative or absolute), overrides `BOT_CONFIG` env string when present | unset |
| `PORT` | Listen port (Makefile + Docker) | `38419` |
| `LOG_LEVEL` | `coze_proxy.*` log level | `INFO` |
| `COZE_LOG_SSE` | `true` enables verbose per-SSE-event debug logging | off |
| `X_AGENT_PROTOCOL` | Enable `x_agent` / streaming `agent.event` (strict OpenAI clients need **off**) | off (`0`) |
| `X_AGENT_MAX_STREAM_EVENTS` | Max `agent.event` lines per streaming response | `40` |

### Minimal `.env` (mapped)

```env
COZE_API_BASE=https://api.coze.cn
COZE_API_KEY=pat_xxxxxxxxxxxxxxxxxx
COZE_USER_ID=openai-proxy

BOT_ID=7628929501656973364
DEFAULT_MODEL=coze
BOT_CONFIG={}

PORT=38419
```

### Important: `.env` formatting pitfalls

`python-dotenv` **does not** support unquoted multi-line values. If you inline JSON in `.env`, keep `BOT_CONFIG` on **one line**, or use **`BOT_CONFIG_FILE`** for multi-line JSON files.

---

## `model` routing

### `passthrough`

```
1) model is required (non-empty after trim) => bot_id = model
   else 400
2) Authorization: Bearer <PAT> is required (no COZE_API_KEY fallback)
   else 401
```

### `mapped`

```
1) effective model X:
   - request.model if present (trimmed)
   - else DEFAULT_MODEL
   - else 400

2) resolve bot_id:
   - if X is a key in BOT_CONFIG => BOT_CONFIG[X]
   - else if X looks like "{group}-{name}" and stripping a known group prefix yields a key => BOT_CONFIG[name]
   - else BOT_ID
   - if BOT_ID is empty => 500

3) bearer token:
   - client Authorization first
   - else COZE_API_KEY (mapped only)
```

### `/v1/models` listing behavior

- **`mapped`**: returns `DEFAULT_MODEL` plus all logical model keys (deduped, stable order). With grouped `BOT_CONFIG`, each entry uses `id="{group}-{model}"` and `owned_by="{group}"` (some clients cluster by `id` prefix).
- **`passthrough`**: returns an empty list (there is nothing server-side to enumerate; `model` is the raw `bot_id`).

---

## API

### `GET /v1/models`

Example shape (grouped):

```json
{
  "object": "list",
  "data": [
    { "id": "support-kefu",   "object": "model", "created": 0, "owned_by": "support" },
    { "id": "support-writer", "object": "model", "created": 0, "owned_by": "support" }
  ]
}
```

### `POST /v1/chat/completions`

Supported fields:

| Field | Meaning |
|---|---|
| `model` | logical model name / qualified `{group}-{name}` / passthrough `bot_id` |
| `messages` | OpenAI-compatible; `system` is folded into the first `user` (Coze V3 doesn’t natively consume `system` as a first-class role) |
| `stream` | `true` SSE, `false` full JSON (still upstream-streamed internally) |
| `user` | optional; becomes upstream `user_id` (fallback: `COZE_USER_ID`) |
| `conversation_id` | OpenAI extension; mapped to upstream `ConversationID` / `conversation_id` |

**Ignored parameters** (not forwarded; Coze behavior is controlled in the Coze console bot):

`temperature` / `top_p` / `max_tokens` / `stop` / `frequency_penalty` / `presence_penalty` / `n` / `logit_bias` / `tools` / `functions`

### `GET /health`

```json
{ "ok": true, "mode": "mapped", "upstream_configured": true }
```

---

## Client examples

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:38419/v1",
    api_key="pat_xxxxxxxxxxxxxxxxxx",
)

resp = client.chat.completions.create(
    model="kefu",
    messages=[
        {"role": "system", "content": "Answer in Chinese."},
        {"role": "user", "content": "Hello"},
    ],
)
print(resp.choices[0].message.content)
```

### Continue a conversation (`conversation_id`)

```python
client.chat.completions.create(
    model="kefu",
    extra_body={"conversation_id": "7631949064133591083"},
    messages=[{"role": "user", "content": "Continue the previous topic"}],
)
```

### curl

```bash
curl -s http://localhost:38419/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxx" \
  -d '{
    "model": "kefu",
    "messages": [{"role": "user", "content": "ping"}],
    "stream": false
  }'
```

---

## Docker

The repo ships a root `Dockerfile`. Default container port is **`38419`**, overridable via `PORT`.

```bash
docker build -t coze-openai-gateway .
docker run --rm -p 38419:38419 --env-file .env coze-openai-gateway
docker run --rm -p 8080:8080 --env-file .env -e PORT=8080 coze-openai-gateway
```

`docker-compose.yml` pins the published/listen **port in the file** (default `38419:38419`; it does not follow `.env`’s `PORT`). You still need a repo-root `.env` for other settings. `make compose` / `make compose-down`; `make image-tar` (default `coze-openai-gateway.image.tar`); `docker load -i <file>` to import the tarball.

---

## Troubleshooting

| Symptom | What to check |
|---|---|
| `401 Missing Authorization: Bearer <coze_pat> (passthrough mode)` | passthrough requires client `Authorization` (no `.env` fallback) |
| `401 Missing Authorization: Bearer and COZE_API_KEY fallback` | mapped: missing client token and missing `COZE_API_KEY` |
| `400 Missing model: in passthrough mode ...` | passthrough requires `model` (it is the `bot_id`) |
| `400 Missing model and DEFAULT_MODEL not set` | mapped: missing `model` and missing `DEFAULT_MODEL` |
| `500 No bot_id: set BOT_ID or BOT_CONFIG for this model` | mapped: `BOT_ID` empty and `model` didn’t resolve |
| `502 The token you entered is incorrect...` | invalid/expired PAT |
| `200` but empty assistant `content` | bot not published to token’s workspace, or upstream didn’t emit an `answer`-type completion; inspect proxy logs |
| Cherry Studio `AI_TypeValidationError` / `invalid_union` (`choices` or `error`) | Do not enable `X_AGENT_PROTOCOL` for strict OpenAI clients, or set `0`; if enabled globally, send `X-Coze-X-Agent: 0` on that request |

---

## Security

- Never commit `.env` / PATs.
- Treat leaked PATs as compromised: rotate/revoke immediately.
- Prefer server-side deployment with minimal logging of sensitive payloads.

---

## License

This project is licensed under the [**MIT**](./LICENSE) license.
