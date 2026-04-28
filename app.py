"""
OpenAI-compatible proxy for Coze Plus `/v3/chat`.

Routes:
  GET  /v1/models
  POST /v1/chat/completions   (OpenAI stream=true|false)
  GET  /health

Domain logic lives in `config.py`, `mapping.py`, `upstream.py`;
this file is route wiring only.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from config import settings
from mapping import (
    build_coze_payload,
    completion_response,
    effective_model,
    openai_messages_to_additional,
    resolve_bot_id,
)
from proxy_log import configure, get_logger, new_request_id, request_context
from upstream import collect_stream, stream_to_openai_sse

logger = get_logger("http")


def _last_user_preview(messages: list[Any], max_len: int = 100) -> str:
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            s = c.replace("\n", " ").strip()
            return (s[:max_len] + "…") if len(s) > max_len else s
    return ""


def _snippet(text: str, max_len: int = 160) -> str:
    s = (text or "").replace("\n", " ").strip()
    return (s[:max_len] + "…") if len(s) > max_len else s


def _effective_x_agent(request: Request) -> bool:
    """Cherry Studio 等客户端只接受每条 SSE 为 chat.completion.chunk 或 error；见 _env 默认与头覆盖。"""
    h = (
        request.headers.get("X-Coze-X-Agent")
        or request.headers.get("x-coze-x-agent")
        or ""
    ).strip().lower()
    if h in ("0", "false", "no", "off", "n"):
        return False
    if h in ("1", "true", "yes", "on", "y"):
        return True
    return settings.x_agent_protocol


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure(settings)
    logger.info(
        "coze_proxy mode=%s upstream=%s log_level=%s sse_debug=%s x_agent=%s cors=%s",
        settings.mode,
        settings.upstream_url or "(unconfigured)",
        settings.log_level,
        settings.log_sse_events,
        settings.x_agent_protocol,
        settings.cors_enabled,
    )
    grouped: dict[str, list[str]] = {}
    for m in settings.ordered_models:
        grouped.setdefault(settings.group_of(m), []).append(m)
    logger.info(
        "bot_routing bot_id=%s default_model=%s groups=%s",
        settings.bot_id or "-",
        settings.default_model or "-",
        grouped or "-",
    )
    yield


app = FastAPI(
    title="coze-openai-gateway",
    version="0.4.0",
    lifespan=_lifespan,
)

if settings.cors_enabled:
    allow_origins = list(settings.cors_allow_origins)
    allow_origin_regex = settings.cors_allow_origin_regex
    # 兼容“* + credentials”场景：用正则匹配并回显具体 Origin，避免浏览器拒绝。
    if "*" in allow_origins and settings.cors_allow_credentials and not allow_origin_regex:
        allow_origins = []
        allow_origin_regex = ".*"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_methods=list(settings.cors_allow_methods),
        allow_headers=list(settings.cors_allow_headers),
        expose_headers=list(settings.cors_expose_headers),
        allow_credentials=settings.cors_allow_credentials,
        max_age=settings.cors_max_age,
    )


def _error(message: str, status: int, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def _resolve_bearer(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Client Authorization → upstream bearer.

    passthrough 模式：必须由客户端传（不回落到 COZE_API_KEY，保持"纯代理"语义）。
    mapped 模式：客户端优先，否则回落到 COZE_API_KEY。
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            return None, token
    if settings.is_passthrough:
        return "Missing Authorization: Bearer <coze_pat> (passthrough mode)", None
    if settings.coze_api_key_fallback:
        return None, settings.coze_api_key_fallback
    return "Missing Authorization: Bearer and COZE_API_KEY fallback", None


@app.get("/health")
async def health():
    return {
        "ok": True,
        "mode": settings.mode,
        "upstream_configured": bool(settings.coze_api_base),
        "x_agent_protocol": settings.x_agent_protocol,
    }


@app.get("/v1/models")
async def list_models():
    # `id` 用 `{group}-{model}` 形式：Cherry Studio 等按破折号前缀识别家族；
    # 同时 `owned_by` 也给出分组名，方便其它按 `owned_by` 分组的客户端。
    data = [
        {
            "id": settings.qualified_id(mid),
            "object": "model",
            "created": 0,
            "owned_by": settings.group_of(mid),
        }
        for mid in settings.registered_models()
    ]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    rid = new_request_id()
    t0 = time.monotonic()
    client_ip = (request.client.host if request.client else "-") or "-"
    logger.info("rid=%s recv POST /v1/chat/completions from=%s", rid, client_ip)

    if not settings.coze_api_base:
        return _error("COZE_API_BASE is not configured", 500)

    try:
        body = await request.json()
    except Exception as e:
        logger.warning("rid=%s bad json body: %s", rid, e)
        body = {}

    err, model = effective_model(body, settings)
    if err or not model:
        logger.warning("rid=%s chat reject: %s", rid, err or "invalid model")
        return _error(err or "invalid model", 400)

    bot_id = resolve_bot_id(model, settings)
    if not bot_id:
        if settings.is_passthrough:
            return _error(
                "In passthrough mode `model` must be a Coze bot_id (non-empty)", 400
            )
        return _error("No bot_id: set BOT_ID or BOT_CONFIG for this model", 500)

    err, bearer = _resolve_bearer(request)
    if err or not bearer:
        logger.warning("rid=%s chat reject: %s", rid, err or "unauthorized")
        return _error(err or "unauthorized", 401)

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _error("messages required", 400)

    additional = openai_messages_to_additional(messages)
    user = body.get("user")
    user_id = user if isinstance(user, str) and user.strip() else settings.coze_user_id
    conv = body.get("conversation_id")
    conversation_id = conv if isinstance(conv, str) and conv.strip() else None
    stream = bool(body.get("stream", False))

    preview = _last_user_preview(messages)
    logger.info(
        "rid=%s chat model=%s stream=%s bot_id=%s user_id=%s conv=%s msgs=%s last_user=%r",
        rid,
        model,
        stream,
        bot_id,
        user_id,
        conversation_id or "-",
        len(messages),
        preview,
    )

    payload = build_coze_payload(bot_id, True, additional, user_id, conversation_id)
    url = settings.upstream_url
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    use_x_agent = _effective_x_agent(request)

    if stream:

        async def logged_sse() -> AsyncIterator[str]:
            with request_context(rid):
                n_chunks = 0
                out_bytes = 0
                try:
                    async for line in stream_to_openai_sse(
                        url, payload, headers, model, enable_x_agent=use_x_agent
                    ):
                        n_chunks += 1
                        out_bytes += len(line.encode("utf-8", errors="replace"))
                        yield line
                finally:
                    logger.info(
                        "rid=%s stream_done sse_lines=%s bytes=%s elapsed_ms=%.0f",
                        rid,
                        n_chunks,
                        out_bytes,
                        (time.monotonic() - t0) * 1000,
                    )

        return StreamingResponse(logged_sse(), media_type="text/event-stream")

    with request_context(rid):
        content, usage, upstream_err, x_agent = await collect_stream(
            url, payload, headers, enable_x_agent=use_x_agent
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        if upstream_err is not None:
            logger.warning(
                "rid=%s chat_err elapsed_ms=%.0f upstream=%s",
                rid,
                elapsed_ms,
                upstream_err[:300],
            )
            return _error(upstream_err, 502, kind="upstream_error")
        logger.info(
            "rid=%s chat_ok chars=%s usage=%s elapsed_ms=%.0f reply=%r",
            rid,
            len(content),
            usage,
            elapsed_ms,
            _snippet(content),
        )
        if x_agent is not None:
            x_agent["meta"]["latency_ms"] = int(elapsed_ms)
        return JSONResponse(completion_response(model, content, usage, x_agent))
