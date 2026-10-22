# -*- coding: utf-8 -*-
"""CloakBrowser profile 缓存模板。

目标：让"注册之间的静态资源（JS/CSS/字体）"复用，减少重复下载；
但绝不共享身份信息。

做法
----
1. 每次运行仍使用独立 profile（指纹隔离不变）。
2. 新 profile 创建前，如果存在模板目录，把模板复制进去（本地磁盘复制，
   不产生网络流量），这样首屏静态资源直接命中缓存。
3. 运行结束、关闭浏览器前，先清 Cookie / localStorage / sessionStorage /
   CacheStorage（但**不清 HTTP cache**），再把这个"干净且带缓存"的目录
   快照成模板。
4. 模板只保留一份；超过 TTL 的旧 profile 目录会被清理。

开关（.env）
   CLOAK_PROFILE_CACHE="True"        总开关
   CLOAK_PROFILE_CACHE_TTL_DAYS="3"  模板与旧 profile 的保留天数
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import logging

logger = logging.getLogger(__name__)


def _cfg_get(name: str, default=None):
    """配置可能位于 config.cloakbrowser / config.sms_pool / config.codex。"""
    for module_name in ("cloakbrowser", "sms_pool", "codex"):
        try:
            mod = __import__("config." + module_name, fromlist=["x"])
        except Exception:
            continue
        value = getattr(mod, name, None)
        if value not in (None, ""):
            return value
    return default


def _cfg_bool(name: str, default: bool) -> bool:
    value = _cfg_get(name, None)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _cfg_int(name: str, default: int) -> int:
    value = _cfg_get(name, None)
    try:
        return int(value) if value not in (None, "") else default
    except Exception:
        return default


def enabled() -> bool:
    return _cfg_bool("CLOAK_PROFILE_CACHE", True)


def ttl_days() -> int:
    return max(0, _cfg_int("CLOAK_PROFILE_CACHE_TTL_DAYS", 3))


def _root() -> Path | None:
    try:
        raw = str(_cfg_get("CLOAK_USER_DATA_DIR", "") or "").strip()
        if not raw:
            return None
        root = Path(raw).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        return None


def template_dir() -> Path | None:
    root = _root()
    if root is None:
        return None
    return root / "_cache_template"


def prepare(profile_dir: Path) -> bool:
    """为新 profile 注入缓存模板；失败时静默跳过，不影响注册。"""
    if not enabled():
        return False
    tpl = template_dir()
    if tpl is None or not tpl.is_dir():
        return False
    try:
        if profile_dir.exists() and any(profile_dir.iterdir()):
            return False
        profile_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tpl, profile_dir, dirs_exist_ok=True, symlinks=True)
        logger.info("[Cloak缓存] 已注入缓存模板：%s", profile_dir.name)
        return True
    except Exception as exc:
        logger.warning("[Cloak缓存] 注入模板失败（忽略）：%s", str(exc)[:160])
        return False


def snapshot(profile_dir: Path) -> bool:
    """把当前 profile（应已清除身份数据）快照为模板。"""
    if not enabled():
        return False
    root = _root()
    if root is None:
        return False
    try:
        if not profile_dir.is_dir():
            return False
        tmp = root / "_cache_template.tmp"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(profile_dir, tmp, symlinks=True, ignore=shutil.ignore_patterns(
            "Singleton*", "*.lock", "lockfile", "DevToolsActivePort",
        ))
        tpl = template_dir()
        if tpl is not None and tpl.exists():
            shutil.rmtree(tpl, ignore_errors=True)
        tmp.rename(tpl or (root / "_cache_template"))
        logger.info("[Cloak缓存] 模板已更新（缓存保留，身份已清除）")
        return True
    except Exception as exc:
        logger.warning("[Cloak缓存] 生成模板失败（忽略）：%s", str(exc)[:160])
        return False


def cleanup(max_age_days: int | None = None) -> int:
    """清理过期 profile 目录（只删普通 run profile，不动模板）。"""
    root = _root()
    if root is None:
        return 0
    days = ttl_days() if max_age_days is None else max(0, int(max_age_days))
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    try:
        for entry in root.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except Exception:
                continue
    except Exception:
        return removed
    if removed:
        logger.info("[Cloak缓存] 已清理 %d 个过期 profile", removed)
    return removed

def finalize(driver, profile_dir: Path) -> bool:
    """关闭浏览器前：清身份数据（保留 HTTP 缓存）→ 快照为模板。"""
    if not enabled():
        return False
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
        for origin in (
            "https://auth.openai.com",
            "https://chatgpt.com",
            "https://openai.com",
            "https://platform.openai.com",
        ):
            driver.execute_cdp_cmd(
                "Storage.clearDataForOrigin",
                {"origin": origin, "storageTypes": "cookies,localstorage,sessionstorage,cachestorage"},
            )
        logger.info("[Cloak缓存] 已清除身份数据（保留 HTTP 缓存）")
    except Exception as exc:
        logger.warning("[Cloak缓存] 清除身份数据失败（继续快照）：%s", str(exc)[:140])
    return snapshot(profile_dir)
