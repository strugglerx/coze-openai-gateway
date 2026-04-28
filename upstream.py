"""Upstream client: POST /v3/chat (SSE) aggregation + pass-through."""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator, Optional

import httpx

from config import settings
from mapping import (
    chunk_content,
    chunk_done,
    extract_answer_text,
    extract_delta_text,
    new_chat_id,
)
from proxy_log import get_logger, get_request_id, req_prefix
from x_agent_map import (
    build_agent_event_envelope,
    build_x_agent_root,
    fold_events_for_x_agent,
    first_conversation_id_from_traces,
    map_coze_sse_to_inner_event,
    new_evt_id,
)

logger = get_logger("upstream")

TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _log_reply_shape(text: str) -> None:
    """若整段回复是 JSON 字符串（不少工作流会输出 {\"text\":...}），打一行说明便于排障。"""
    t = text.strip()
    if len(t) < 2 or not t.startswith("{"):
        return
    try:
        j = json.loads(t)
    except json.JSONDecodeError:
        logger.info(
            "%sassistant_content starts with '{' but is not valid JSON (bot raw string?)",
            req_prefix(),
        )
        return
    if isinstance(j, dict) and "text" in j:
        logger.info(
            "%sassistant_content is JSON object (bot/workflow output); "
            "keys=%s text_field_len=%s",
            req_prefix(),
            list(j.keys())[:12],
            len(j["text"]) if isinstance(j.get("text"), str) else "?",
        )
    else:
        logger.info(
            "%sassistant_content is JSON object; keys=%s",
            req_prefix(),
            list(j.keys())[:12] if isinstance(j, dict) else type(j).__name__,
        )


async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, dict]]:
    """Yield (event_name, data_obj) pairs per SSE record.

    SSE record boundary = blank line. Coze emits `event:<name>` and `data:<json>`
    on separate lines, unlike OpenAI which embeds the event inside the JSON.
    """
    event = ""
    async for raw_line in response.aiter_lines():
        line = (raw_line or "").rstrip("\r\n")
        if line == "":
            event = ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("[DONE]", '"[DONE]"'):
            return
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield event, obj


async def _read_error(r: httpx.Response) -> str:
    body = (await r.aread()).decode("utf-8", errors="replace")
    try:
        j = json.loads(body)
        return j.get("msg") or j.get("message") or body[:300]
    except Exception:
        return body[:300]


async def collect_stream(
    url: str, payload: dict, headers: dict, *, enable_x_agent: bool
) -> tuple[str, Optional[dict], Optional[str], Optional[dict]]:
    """Consume the upstream SSE and return (text, usage, error_message, x_agent|None)."""
    parts: list[str] = []
    usage: Optional[dict] = None
    coze_traces: list[tuple[str, dict]] = []
    bot_id = payload.get("bot_id", "?")
    logger.info("%supstream_req POST %s bot_id=%s", req_prefix(), url, bot_id)
    n_events = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as r:
            logger.info("%supstream_resp status=%s", req_prefix(), r.status_code)
            if r.status_code >= 400:
                err_body = await _read_error(r)
                logger.warning(
                    "%supstream_http status=%s err=%s",
                    req_prefix(),
                    r.status_code,
                    err_body[:200],
                )
                return "", None, err_body, None
            async for ev, obj in _iter_sse_events(r):
                n_events += 1
                if enable_x_agent:
                    coze_traces.append((ev, obj))
                if settings.log_sse_events:
                    logger.debug("%ssse event=%s", req_prefix(), ev)
                if ev == "conversation.message.delta":
                    t = extract_delta_text(obj)
                    if t:
                        parts.append(t)
                elif ev == "conversation.message.completed":
                    # Fallback: some deployments emit only `completed` (no deltas).
                    if not parts:
                        t = extract_answer_text(obj)
                        if t:
                            parts.append(t)
                elif ev == "conversation.chat.completed":
                    rri = obj.get("run_record_item")
                    if isinstance(rri, dict) and isinstance(rri.get("usage"), dict):
                        usage = rri["usage"]
                    if not enable_x_agent:
                        break
                elif ev == "conversation.error":
                    err = obj.get("error")
                    msg = (
                        err.get("message")
                        if isinstance(err, dict)
                        else str(err or obj.get("msg") or "stream error")
                    )
                    logger.warning("%supstream_sse_error %s", req_prefix(), msg[:300])
                    return "".join(parts), usage, msg, None
                elif ev == "conversation.stream.done":
                    break
    text = "".join(parts)
    _log_reply_shape(text)
    logger.info(
        "%scollect_stream ok sse_events=%s chars=%s has_usage=%s",
        req_prefix(),
        n_events,
        len(text),
        usage is not None,
    )
    x_agent: Optional[dict] = None
    if enable_x_agent and coze_traces:
        rid = get_request_id()
        trace_id = f"trace_{rid}" if rid else f"trace_{uuid.uuid4().hex[:8]}"
        conv = first_conversation_id_from_traces(coze_traces)
        evs = fold_events_for_x_agent(coze_traces)
        x_agent = build_x_agent_root(
            trace_id=trace_id,
            conversation_id=conv,
            run_id=None,
            events=evs,
            status="completed",
            status_detail={"code": "coze.chat.completed", "label": "对话完成"},
            meta_extra={"coze_trace_events": len(coze_traces)},
        )
    return text, usage, None, x_agent


async def stream_to_openai_sse(
    url: str, payload: dict, headers: dict, model: str, *, enable_x_agent: bool
) -> AsyncIterator[str]:
    """Proxy upstream SSE → OpenAI `chat.completion.chunk` SSE，可选夹带 `agent.event`。"""
    chat_id = new_chat_id()
    created = int(time.time())
    finished = False
    delta_seen = False
    bot_id = payload.get("bot_id", "?")
    max_agent = settings.x_agent_max_stream_events
    ag_emit = 0
    ag_step = 0
    logger.info("%supstream_req POST %s bot_id=%s (stream)", req_prefix(), url, bot_id)
    delta_chars = 0

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as r:
            logger.info("%supstream_resp status=%s (stream)", req_prefix(), r.status_code)
            if r.status_code >= 400:
                err = {
                    "error": {
                        "message": await _read_error(r),
                        "type": "upstream_error",
                        "code": r.status_code,
                    }
                }
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for ev, obj in _iter_sse_events(r):
                if settings.log_sse_events:
                    logger.debug("%ssse event=%s", req_prefix(), ev)
                if enable_x_agent and ag_emit < max_agent:
                    inner = map_coze_sse_to_inner_event(ev, obj, ag_step)
                    if inner is not None:
                        yield f"data: {json.dumps(build_agent_event_envelope(inner, created, new_evt_id()), ensure_ascii=False)}\n\n"
                        ag_step += 1
                        ag_emit += 1

                if ev == "conversation.message.delta":
                    t = extract_delta_text(obj)
                    if t:
                        delta_seen = True
                        delta_chars += len(t)
                        yield f"data: {json.dumps(chunk_content(chat_id, created, model, t), ensure_ascii=False)}\n\n"

                elif ev == "conversation.message.completed" and not delta_seen:
                    t = extract_answer_text(obj)
                    if t:
                        yield f"data: {json.dumps(chunk_content(chat_id, created, model, t), ensure_ascii=False)}\n\n"

                elif ev == "conversation.chat.completed":
                    rri = obj.get("run_record_item")
                    usage = (
                        rri["usage"]
                        if isinstance(rri, dict) and isinstance(rri.get("usage"), dict)
                        else None
                    )
                    yield f"data: {json.dumps(chunk_done(chat_id, created, model, usage), ensure_ascii=False)}\n\n"
                    finished = True
                    # 标准 OpenAI 模式（无 agent.event）：不再消费上游尾部事件（如追问建议），尽早发 [DONE]。
                    if not enable_x_agent:
                        break

                elif ev == "conversation.error":
                    err = obj.get("error")
                    msg = (
                        err.get("message")
                        if isinstance(err, dict)
                        else str(err or obj.get("msg") or "stream error")
                    )
                    logger.warning("%sstream upstream_sse_error %s", req_prefix(), msg[:300])
                    yield f"data: {json.dumps({'error': {'message': msg, 'type': 'upstream_error'}}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                elif ev == "conversation.stream.done":
                    if not finished:
                        yield f"data: {json.dumps(chunk_done(chat_id, created, model, None), ensure_ascii=False)}\n\n"
                    break

    logger.info(
        "%sstream_to_openai_sse finished model=%s delta_chars=%s",
        req_prefix(),
        model,
        delta_chars,
    )
    yield "data: [DONE]\n\n"
