# -*- coding: utf-8 -*-
"""号码池运行日志：每轮采购/分配写 sms-pool-worker.log，每个任务写 sms-pool-<id>-<email>.log。

设计目的：号码池是独立后台进程，原来只有 journald + 数据库事件，页面上看不到
"这一轮到底买了什么、分给了谁、补跑卡在哪"。这里给每个任务和每轮轮询落一份
可读日志，供 WebUI 直接查看。
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parents[1] / "注册日志"
_LOCK = threading.RLock()
_MAX_BYTES = 4 * 1024 * 1024
_NAME_RE = re.compile(r"^sms-pool-[A-Za-z0-9_.@-]{0,140}\.log$")


def log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def worker_log_path() -> Path:
    return log_dir() / "sms-pool-worker.log"


def _safe_email(email: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@-]", "_", str(email or ""))
    return cleaned[:80] or "account"


def task_log_path(reservation_id: int, email: str = "") -> Path:
    return log_dir() / f"sms-pool-{int(reservation_id)}-{_safe_email(email)}.log"


def append(path: Path, line: str) -> None:
    text = str(line or "").rstrip()
    if not text:
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    with _LOCK:
        try:
            if path.exists() and path.stat().st_size > _MAX_BYTES:
                path.unlink()
        except OSError:
            pass
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {text}\n")
        except OSError:
            logger.debug("号码池日志写入失败：%s", path, exc_info=True)


def append_worker(line: str) -> None:
    append(worker_log_path(), line)


def append_task(reservation_id: int, email: str, line: str) -> None:
    append(task_log_path(reservation_id, email), line)


def is_valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(str(name or "")))


def read_tail(name: str, tail: int = 400) -> str:
    if not is_valid_name(name):
        raise ValueError("非法日志名")
    path = log_dir() / name
    if not path.exists():
        return ""
    with _LOCK:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    return "".join(lines[-max(1, min(2000, int(tail))):])


def list_logs(limit: int = 20) -> list[dict]:
    out: list[dict] = []
    try:
        files = sorted(
            log_dir().glob("sms-pool-*.log"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return out
    for path in files[: max(1, int(limit))]:
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({
            "name": path.name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return out


def find_task_log(reservation_id: int) -> Path | None:
    """按号码记录 ID 找到对应的任务日志文件。"""
    try:
        matches = sorted(
            log_dir().glob(f"sms-pool-{int(reservation_id)}-*.log"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    return matches[0] if matches else None


def read_task_tail(reservation_id: int, tail: int = 600) -> str:
    """读取某个号码的接码日志尾部；没有日志返回空串。"""
    path = find_task_log(reservation_id)
    if path is None:
        return ""
    with _LOCK:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    return "".join(lines[-max(1, min(2000, int(tail))):])
