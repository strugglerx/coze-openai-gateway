"""Pure conversions between OpenAI wire format and Coze Plus /v3/chat."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Optional

from config import Settings


def effective_model(body: dict, s: Settings) -> tuple[Optional[str], Optional[str]]:
    """Return (error_message, effective_model).

    passthrough 模式下 model 必传（它就是 bot_id，没法 fallback）。
    """
    m = body.get("model")
    if isinstance(m, str) and m.strip():
        return None, m.strip()
    if s.is_passthrough:
        return "Missing model: in passthrough mode `model` must be a Coze bot_id", None
    if s.default_model:
        return None, s.default_model
    return "Missing model and DEFAULT_MODEL not set", None


def _strip_group_prefix(model: str, s: Settings) -> Optional[str]:
    """若 model 形如 `{group}-{name}` 且 group 是 BOT_CONFIG 里已知分组，返回 `name`。

    同一个分组前缀可能对应多个 model，只要剥完之后的 tail 在 bot_config 里即可。
    用 set 去重，优先按"最长前缀"匹配，避免 "A" 和 "AB" 这种同开头分组互相吃。
    """
    groups = {g for g in s.bot_groups.values() if g}
    for g in sorted(groups, key=len, reverse=True):
        prefix = f"{g}-"
        if model.startswith(prefix):
            tail = model[len(prefix):]
            if tail in s.bot_config:
                return tail
    return None


_PERSONA_SUFFIX = re.compile(r"^(.*?)-persona-(\d+)$", re.IGNORECASE)
_BOT_ID_PATTERN = re.compile(r"^\d{16,32}$")


def _strip_persona_suffix(model: str) -> str:
    """支持把模型别名 `xxx-persona-19` 还原成 `xxx`，便于继续走 BOT_CONFIG 映射。"""
    m = _PERSONA_SUFFIX.match(model)
    if not m:
        return model
    return (m.group(1) or "").strip("- ") or model


def _looks_like_bot_id(model: str) -> bool:
    return bool(_BOT_ID_PATTERN.match((model or "").strip()))


def resolve_bot_id(model: str, s: Settings) -> str:
    """passthrough 模式直接把 model 当 bot_id；mapped 模式走 BOT_CONFIG / BOT_ID。

    mapped 下先精确匹配 BOT_CONFIG 键；若没命中且 model 形如 `{group}-{name}`，
    就剥去已知分组前缀再查一次。兜底回落到 BOT_ID。
    """
    model_key = _strip_persona_suffix(model)
    if s.is_passthrough:
        return model_key
    if model_key in s.bot_config:
        return s.bot_config[model_key]
    stripped = _strip_group_prefix(model_key, s)
    if stripped is not None:
        return s.bot_config[stripped]
    # mapped 模式下：若显式传入的是 bot_id（纯数字长串），优先直连该 bot。
    if _looks_like_bot_id(model_key):
        return model_key
    return s.bot_id


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text") or "")
        return "".join(parts) if parts else json.dumps(content, ensure_ascii=False)
    return str(content) if content is not None else ""


def openai_messages_to_additional(messages: list[dict]) -> list[dict[str, str]]:
    """Map OpenAI messages → Coze additional_messages.

    system messages are concatenated and prepended to the first user message
    (or inserted as one if none exists).
    """
    system_chunks: list[str] = []
    tail: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role", "")
        content = _normalize_content(m.get("content"))
        if role == "system":
            system_chunks.append(content)
        elif role in ("user", "assistant"):
            tail.append({"role": role, "content": content, "content_type": "text"})

    sys_text = "\n\n".join(s for s in system_chunks if s).strip()
    if sys_text:
        for item in tail:
            if item["role"] == "user":
                item["content"] = f"[System]\n{sys_text}\n\n{item['content']}"
                break
        else:
            tail.insert(
                0,
                {"role": "user", "content": f"[System]\n{sys_text}", "content_type": "text"},
            )
    return tail


def build_coze_payload(
    bot_id: str,
    stream: bool,
    additional_messages: list[dict],
    user_id: str,
    conversation_id: Optional[str],
) -> dict:
    payload: dict[str, Any] = {
        "bot_id": str(bot_id),
        "user_id": user_id,
        "stream": stream,
        "additional_messages": additional_messages,
    }
    if conversation_id:
        # Coze Plus docs field name is `ConversationID`; some deployments accept
        # snake_case. Send both for compatibility.
        payload["ConversationID"] = conversation_id
        payload["conversation_id"] = conversation_id
    return payload


def extract_delta_text(obj: dict) -> str:
    mi = obj.get("message_item")
    if isinstance(mi, dict):
        c = mi.get("content")
        return c if isinstance(c, str) else ""
    c = obj.get("content")
    return c if isinstance(c, str) else ""


def extract_answer_text(obj: dict) -> str:
    """Full answer text from a `conversation.message.completed` event (type=answer)."""
    mi = obj.get("message_item")
    if isinstance(mi, dict) and mi.get("type") == "answer":
        c = mi.get("content")
        return c if isinstance(c, str) else ""
    return ""


def new_chat_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:12]}"


def completion_response(
    model: str, content: str, usage: dict | None, x_agent: dict | None = None
) -> dict:
    u = usage or {}
    d: dict[str, Any] = {
        "id": new_chat_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": u.get("prompt_tokens", 0),
            "completion_tokens": u.get("completion_tokens", 0),
            "total_tokens": u.get("total_tokens", 0),
        },
    }
    if x_agent is not None:
        d["x_agent"] = x_agent
    return d


def chunk_content(chat_id: str, created: int, model: str, text: str) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }


def chunk_done(chat_id: str, created: int, model: str, usage: dict | None) -> dict:
    delta: dict[str, Any] = {}
    if usage:
        delta["usage"] = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": "stop"}],
    }
