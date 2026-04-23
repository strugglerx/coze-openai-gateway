"""Per-request 追踪 id + coze_proxy 日志命名空间。"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from contextlib import contextmanager
from typing import Iterator

from config import Settings

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "coze_request_id", default=""
)

_LOG_NS = "coze_proxy"
_FMT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


@contextmanager
def request_context(rid: str) -> Iterator[None]:
    tok = _request_id.set(rid)
    try:
        yield
    finally:
        _request_id.reset(tok)


def req_prefix() -> str:
    rid = _request_id.get()
    return f"rid={rid} " if rid else ""


def get_logger(suffix: str) -> logging.Logger:
    return logging.getLogger(f"{_LOG_NS}.{suffix}")


def configure(settings: Settings) -> None:
    """幂等：给 `coze_proxy` 装一个 stdout StreamHandler，关 propagate 以免和 root 打重。"""
    log = logging.getLogger(_LOG_NS)
    lvl = getattr(logging, settings.log_level.upper(), logging.INFO)
    log.setLevel(lvl)

    already = any(
        getattr(h, "_coze_proxy_handler", False) for h in log.handlers
    )
    if not already:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt=_FMT, datefmt=_DATEFMT))
        handler.setLevel(lvl)
        handler._coze_proxy_handler = True  # type: ignore[attr-defined]
        log.addHandler(handler)
    else:
        for h in log.handlers:
            if getattr(h, "_coze_proxy_handler", False):
                h.setLevel(lvl)
    log.propagate = False
