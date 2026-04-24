"""Map Coze Chat V3 SSE → 统一响应协议 `x_agent` / `agent.event`（见 docs/统一响应协议.md）。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

X_AGENT_VERSION = "1.0.0"


def new_evt_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def _short(s: str, max_len: int = 400) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _dig_conversation_id(obj: dict) -> Optional[str]:
    for k in ("conversation_id", "conversationId", "ConversationID"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    data = obj.get("data")
    if isinstance(data, dict):
        for k in ("conversation_id", "conversationId"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _sanitize(obj: Any, max_depth: int = 2, max_str: int = 400) -> Any:
    if max_depth <= 0:
        return "…"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _short(obj, max_str)
    if isinstance(obj, list):
        return [_sanitize(x, max_depth - 1, max_str) for x in obj[:20]] + (
            ["…"] if len(obj) > 20 else []
        )
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= 32:
                out["_truncated_"] = True
                break
            if not isinstance(k, str):
                continue
            if k in ("content", "raw", "message") and isinstance(v, str) and len(v) > max_str:
                out[k] = _short(v, max_str)
            else:
                out[k] = _sanitize(v, max_depth - 1, max_str)
        return out
    return str(obj)[:200]


def first_conversation_id_from_traces(
    coze_traces: list[tuple[str, dict]],
) -> Optional[str]:
    for _, obj in coze_traces:
        c = _dig_conversation_id(obj)
        if c:
            return c
    return None


def map_coze_sse_to_inner_event(
    coze_event: str, obj: dict, step: int
) -> Optional[dict[str, Any]]:
    """将一条 Coze `event` + `data` 映为 `agent.event` 里 `x_agent` 内层对象；无需输出则 `None`。"""

    if not coze_event or not coze_event.strip():
        return None
    coze_event = coze_event.strip()
    sid = f"s{step}"

    if coze_event == "conversation.message.delta":
        mi = obj.get("message_item")
        if isinstance(mi, dict):
            typ = mi.get("type")
            if typ and typ != "answer":
                return {
                    "type": "status",
                    "step_id": sid,
                    "status": "running",
                    "message": f"message_item:{typ}",
                    "extra": {
                        "coze_event": coze_event,
                        "message_item_type": typ,
                    },
                }
        return None

    if coze_event == "conversation.message.completed":
        mi = obj.get("message_item")
        extra: dict[str, Any] = {"coze_event": coze_event}
        if isinstance(mi, dict):
            extra["message_item_type"] = mi.get("type")
        return {
            "type": "coze_message",
            "step_id": sid,
            "message": "message completed",
            "extra": _sanitize(obj, max_depth=2, max_str=300),
        }

    if coze_event == "conversation.chat.completed":
        return {
            "type": "done",
            "status": "completed",
            "step_id": sid,
            "extra": {"coze_event": coze_event},
        }

    if coze_event == "conversation.error":
        err = obj.get("error")
        msg = (
            err.get("message")
            if isinstance(err, dict)
            else str(err or obj.get("msg") or "error")
        )
        return {
            "type": "status",
            "step_id": sid,
            "message": _short(str(msg), 500),
            "status": "failed",
            "extra": {"coze_event": coze_event, "detail": _sanitize(obj, 1)},
        }

    if coze_event in ("conversation.stream.done", "done"):
        return None

    # 其余 conversation.* 与其它事件：可观测的「原始轨迹」
    if coze_event.startswith("conversation.") or coze_event:
        return {
            "type": "coze_sse",
            "step_id": sid,
            "message": coze_event,
            "extra": {
                "coze_event": coze_event,
                "data": _sanitize(obj, max_depth=2, max_str=300),
            },
        }
    return None


def build_agent_event_envelope(
    inner: dict[str, Any], created: int | None = None, evt_id: str | None = None
) -> dict[str, Any]:
    return {
        "id": evt_id or new_evt_id(),
        "object": "agent.event",
        "created": int(created or time.time()),
        "x_agent": inner,
    }


def build_x_agent_root(
    *,
    trace_id: str,
    conversation_id: Optional[str],
    run_id: Optional[str],
    events: list[dict[str, Any]],
    status: str = "completed",
    status_detail: Optional[dict[str, Any]] = None,
    meta_extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version": X_AGENT_VERSION,
        "trace_id": trace_id,
        "mode": "coze",
        "status": status,
        "events": events,
        "ui": {},
        "state": {},
        "meta": {
            "source_type": "coze",
            **(meta_extra or {}),
        },
    }
    if conversation_id:
        out["conversation_id"] = conversation_id
    if run_id:
        out["run_id"] = run_id
    if status_detail:
        out["status_detail"] = status_detail
    return out


def fold_events_for_x_agent(
    coze_traces: list[tuple[str, dict]], max_events: int = 50
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    step = 0
    for ev, obj in coze_traces:
        inner = map_coze_sse_to_inner_event(ev, obj, step)
        if inner is not None:
            out.append(inner)
            step += 1
        if len(out) >= max_events:
            out.append(
                {
                    "type": "status",
                    "step_id": f"s{step}",
                    "status": "running",
                    "message": f"coze 轨迹仅保留前 {max_events} 条",
                }
            )
            break
    return out
