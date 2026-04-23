"""Runtime configuration loaded from environment (.env at repo root)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")

MODE_MAPPED = "mapped"
MODE_PASSTHROUGH = "passthrough"
_VALID_MODES = (MODE_MAPPED, MODE_PASSTHROUGH)

_DEFAULT_GROUP = "coze-plus"


@dataclass(frozen=True)
class Settings:
    mode: str
    coze_api_base: str
    coze_chat_path: str
    coze_user_id: str
    coze_api_key_fallback: str | None
    bot_id: str
    default_model: str
    # 扁平路由表：model -> bot_id（聊天请求查这张表）
    bot_config: dict[str, str] = field(default_factory=dict)
    # 显示分组：model -> group_name（/v1/models 的 owned_by 用它）
    bot_groups: dict[str, str] = field(default_factory=dict)
    # 有序 model 列表（尊重 groups 顺序 + group 内 models 顺序）
    ordered_models: tuple[str, ...] = field(default_factory=tuple)
    log_level: str = "INFO"
    log_sse_events: bool = False

    @property
    def is_passthrough(self) -> bool:
        return self.mode == MODE_PASSTHROUGH

    @property
    def upstream_url(self) -> str:
        return f"{self.coze_api_base}{self.coze_chat_path}"

    def group_of(self, model: str) -> str:
        return self.bot_groups.get(model, _DEFAULT_GROUP)

    def qualified_id(self, model: str) -> str:
        """`/v1/models` 里对外的 id：有显式分组时返回 `{group}-{model}`，否则裸名。

        这样 Cherry Studio 等按破折号前缀识别家族的客户端可以自动合组。
        """
        g = self.bot_groups.get(model)
        if not g or g == _DEFAULT_GROUP:
            return model
        return f"{g}-{model}"

    def registered_models(self) -> list[str]:
        """Models exposed by /v1/models.

        mapped: DEFAULT_MODEL + BOT_CONFIG keys（按 BOT_CONFIG 中的顺序，去重）
        passthrough: 空（model 即 bot_id，无从枚举）
        """
        if self.is_passthrough:
            return []
        ids: list[str] = []
        seen: set[str] = set()
        if self.default_model:
            ids.append(self.default_model)
            seen.add(self.default_model)
        for k in self.ordered_models:
            if k and k not in seen:
                ids.append(k)
                seen.add(k)
        return ids


# --------------------------------------------------------------------
# BOT_CONFIG 解析：
#
# 形式 A（旧，扁平）：
#   {"客服":"7628...","工地":"7595..."}
# 形式 B（新，分组）：
#   [
#     {"group":"客服组","models":{"客服":"7628..."}},
#     {"group":"工地组","models":{"工地":"7595..."}}
#   ]
#
# 两种格式都返回同一组 (bot_config, bot_groups, ordered_models)。
# --------------------------------------------------------------------


def _sanitize_model_map(obj: object) -> list[tuple[str, str]]:
    """Dict -> 有序 [(model, bot_id)]，过滤空串/非字符串。"""
    if not isinstance(obj, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for k, v in obj.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            pairs.append((k.strip(), v.strip()))
    return pairs


def _parse_grouped(obj: list) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Array-of-groups 形式 → (config, groups, order)。同 model 取首次出现。"""
    config: dict[str, str] = {}
    groups: dict[str, str] = {}
    order: list[str] = []
    for idx, entry in enumerate(obj):
        if not isinstance(entry, dict):
            continue
        group_name = entry.get("group")
        if not isinstance(group_name, str) or not group_name.strip():
            group_name = f"group-{idx + 1}"
        group_name = group_name.strip()
        for model, bot_id in _sanitize_model_map(entry.get("models")):
            if model in config:
                continue
            config[model] = bot_id
            groups[model] = group_name
            order.append(model)
    return config, groups, order


def _parse_bot_config_json(obj: object) -> tuple[dict[str, str], dict[str, str], list[str]]:
    if isinstance(obj, list):
        return _parse_grouped(obj)
    if isinstance(obj, dict):
        pairs = _sanitize_model_map(obj)
        config = dict(pairs)
        groups = {k: _DEFAULT_GROUP for k, _ in pairs}
        order = [k for k, _ in pairs]
        return config, groups, order
    return {}, {}, []


def _parse_bot_config_raw(raw: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    raw = (raw or "").strip()
    if not raw:
        return {}, {}, []
    try:
        return _parse_bot_config_json(json.loads(raw))
    except json.JSONDecodeError:
        return {}, {}, []


def _load_bot_config_file(path: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    p = Path(path.strip()).expanduser()
    if not p.is_absolute():
        p = _ROOT / p
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}, {}, []
    try:
        return _parse_bot_config_json(json.loads(text))
    except json.JSONDecodeError:
        return {}, {}, []


def _resolve_bot_config() -> tuple[dict[str, str], dict[str, str], list[str]]:
    """优先级：BOT_CONFIG_FILE > BOT_CONFIG 环境变量。"""
    file_path = os.getenv("BOT_CONFIG_FILE", "").strip()
    if file_path:
        fromfile = _load_bot_config_file(file_path)
        if fromfile[0]:
            return fromfile
    return _parse_bot_config_raw(os.getenv("BOT_CONFIG", ""))


def _parse_mode(raw: str) -> str:
    m = (raw or "").strip().lower()
    if m in _VALID_MODES:
        return m
    return MODE_MAPPED


def load_settings() -> Settings:
    # passthrough 模式下 COZE_API_BASE 也给个默认，开箱即用
    mode = _parse_mode(os.getenv("MODE", MODE_MAPPED))
    default_base = "https://api.coze.cn" if mode == MODE_PASSTHROUGH else ""
    bot_config, bot_groups, order = _resolve_bot_config()
    return Settings(
        mode=mode,
        coze_api_base=(os.getenv("COZE_API_BASE", "").strip() or default_base).rstrip("/"),
        coze_chat_path=os.getenv("COZE_CHAT_PATH", "/v3/chat"),
        coze_user_id=os.getenv("COZE_USER_ID", "openai-proxy"),
        coze_api_key_fallback=(os.getenv("COZE_API_KEY", "").strip() or None),
        bot_id=os.getenv("BOT_ID", "").strip(),
        default_model=os.getenv("DEFAULT_MODEL", "coze").strip(),
        bot_config=bot_config,
        bot_groups=bot_groups,
        ordered_models=tuple(order),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip() or "INFO",
        log_sse_events=os.getenv("COZE_LOG_SSE", "").strip().lower()
        in ("1", "true", "yes", "on"),
    )


settings: Settings = load_settings()
