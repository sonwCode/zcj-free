# -*- coding: utf-8 -*-
"""号码池智能选号器 v3。

相比 v2 的三项改进（都有数据依据）：

1. 失败类型分离
   不是所有失败都说明"号段不好"：
     quality_failure（号段/号码质量被拒）：fraud_guard, phone_fraud_guard,
         voip_phone_disallowed, phone_number_in_use, invalid_number, add_phone_rejected
     flow_failure（我们自己的流程/网络问题）：code_timeout, transient_*,
         worker_shutdown, session_circuit_open, lease_expired, cleanup_release,
         unconfirmed_failure, activation_state_missing
   只有 quality_failure 作为负面证据降低该号段成功率；flow_failure 单独统计，
   否则我们自己的 bug 会把好号段一起打死（实测大量 transient 是通道/提交问题）。

2. Thompson 采样替代 UCB
   从 Beta 后验采样 p，用 单价/p 排序。探索是自然的：样本少的号段后验方差大，
   偶尔会被采样到高值而被尝试，不需要人为加奖励项。

3. 同号段连买降权
   同一 (平台,国家) 最近连续买入过多时，按次数乘 1.0→1.5 的惩罚系数，
   避免把一个还没验证的号段一次性烧完。

花费口径保持三分类：confirmed / refunded / unknown。
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone

from core import db

HALF_LIFE_HOURS = 12.0
PRIOR_STRENGTH = 4.0
BURST_WINDOW_MIN = 30
BURST_FAIL_LIMIT = 3
HOUR_BUCKET_PENALTY = 0.25
STOCK_FULL_CREDIT = 3.0
DIVERSITY_WINDOW_MIN = 60
DIVERSITY_FREE = 2
DIVERSITY_STEP = 0.15

QUALITY_FAILURES = {
    "fraud_guard", "phone_fraud_guard", "voip_phone_disallowed",
    "phone_number_in_use", "invalid_number", "add_phone_rejected",
    "phone_region_not_supported", "incorrect_number",
}
FLOW_FAILURES = {
    "code_timeout", "transient_retry_exhausted", "transient_network",
    "worker_shutdown", "session_circuit_open", "lease_expired",
    "cleanup_release", "unconfirmed_failure", "activation_state_missing",
    "cleanup_cancelled", "not_released_retry_exhausted", "not_released_retry_pending",
}


def _decay(age_hours: float) -> float:
    return 1.0 if age_hours <= 0 else 0.5 ** (age_hours / HALF_LIFE_HOURS)


def _empty() -> dict:
    return {"w_ok": 0.0, "w_q": 0.0, "w_f": 0.0, "n": 0, "ok": 0}


def _add(bucket: dict, key, weight: float, kind: str) -> None:
    item = bucket.setdefault(key, _empty())
    item["n"] += 1
    if kind == "ok":
        item["w_ok"] += weight
        item["ok"] += 1
    elif kind == "quality":
        item["w_q"] += weight
    else:
        item["w_f"] += weight


def classify_failure(status: str, code: str) -> str:
    st = str(status or "")
    cd = str(code or "").lower()
    if st == "completed":
        return "ok"
    if cd in QUALITY_FAILURES:
        return "quality"
    if cd in FLOW_FAILURES:
        return "flow"
    # 未知失败码按质量失败处理（保守），避免漏掉真实风控
    return "quality" if st in {"released", "orphaned"} else "flow"


def build_stats(hours: int = 72) -> dict:
    stats = {
        "region": {}, "country": {}, "provider": {},
        "global": _empty(), "hour": {},
        "spent_cny_1h": 0.0, "spent_cny_24h": 0.0,
        "refunded_cny_24h": 0.0, "unknown_cny_24h": 0.0, "inflight_cny_24h": 0.0,
    }
    conn = None
    now = datetime.now(timezone.utc)
    try:
        conn = db._sqlite_conn()
        rows = conn.execute(
            "select provider, country, status, failure_code, release_result, created_at, "
            "activation_metadata, price_cny from sms_pool_reservations "
            "where created_at >= datetime('now', ?)",
            ("-%d hour" % int(hours),),
        ).fetchall()
    except Exception:
        return stats
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    for row in rows:
        provider = str(row["provider"] or "").lower()
        country = str(row["country"] or "")
        status = str(row["status"] or "")
        kind = classify_failure(status, row["failure_code"])
        try:
            meta = json.loads(row["activation_metadata"] or "{}")
            region = str(meta.get("phone_country_name") or "").strip()
        except Exception:
            region = ""
        try:
            ts = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = max(0.0, (now - ts).total_seconds() / 3600.0)
            hour = ts.astimezone(timezone.utc).hour
        except Exception:
            age_h, hour = 0.0, now.hour
        w = _decay(age_h)

        _add(stats["region"], (provider, country, region or "#" + country), w, kind)
        _add(stats["country"], (provider, country), w, kind)
        _add(stats["provider"], provider, w, kind)
        _add(stats["global"], "__all__", w, kind)
        _add(stats["hour"], (provider, hour), w, kind)

        rel = str(row["release_result"] or "").lower()
        try:
            price = float(row["price_cny"] or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        refunded = ("cancel" in rel) or ("refund" in rel) or ("released_by_oauth" in rel)
        in_flight = status in {"reserved", "running"}
        settled_fail = status in {"failed", "expired", "orphaned"} or rel.startswith("not_released")
        if refunded:
            key = "refunded_cny_24h"
        elif status == "completed" or settled_fail:
            key = "spent_cny_24h"
            if age_h <= 1.0:
                stats["spent_cny_1h"] += price
        elif status == "released" and not rel:
            key = "unknown_cny_24h"
        elif in_flight:
            key = "inflight_cny_24h"
        else:
            key = "unknown_cny_24h"
        if age_h <= 24.0:
            stats[key] = float(stats.get(key) or 0.0) + price
    return stats


def prior_pair(stats: dict | None = None) -> tuple:
    """先验取全局实测成功率（只看质量失败），不再写死百分比。"""
    item = (stats or {}).get("global", {}).get("__all__") if stats else None
    if item:
        denom = item["w_ok"] + item["w_q"]
        p = (item["w_ok"] / denom) if denom > 0 else 0.05
    else:
        p = 0.05
    p = min(0.9, max(0.001, p))
    return p * PRIOR_STRENGTH, (1.0 - p) * PRIOR_STRENGTH


def _beta_params(item: dict | None, prior: tuple) -> tuple:
    a0, b0 = prior
    if not item or item["n"] <= 0:
        return a0, b0
    return item["w_ok"] + a0, item["w_q"] + b0


def _posterior(item: dict | None, prior: tuple | None = None) -> float:
    a, b = _beta_params(item, prior or (0.2, 3.8))
    return a / (a + b) if (a + b) > 0 else 0.05


def sample_probability(item: dict | None, prior: tuple | None = None) -> float:
    """Thompson 采样：从 Beta 后验抽一个 p。"""
    a, b = _beta_params(item, prior or (0.2, 3.8))
    try:
        return random.betavariate(max(0.05, a), max(0.05, b))
    except Exception:
        return a / (a + b)


def blended_probability(stats: dict, provider: str, country: str, region: str = "",
                        *, sample: bool = False) -> dict:
    prior = prior_pair(stats)
    keys = [
        (stats["region"], (provider, country, region or "#" + country), 1.0),
        (stats["country"], (provider, country), 0.6),
        (stats["provider"], provider, 0.3),
        (stats["global"], "__all__", 0.1),
    ]
    total_w = 0.0
    acc = 0.0
    detail = {}
    for bucket, key, factor in keys:
        item = bucket.get(key)
        n = item["n"] if item else 0
        p = sample_probability(item, prior) if sample else _posterior(item, prior)
        weight = float(n) * factor
        total_w += weight
        acc += p * weight
        detail[key if isinstance(key, str) else key[-1]] = (round(p, 4), n)
    p = (acc / total_w) if total_w > 0 else _posterior(None, prior)
    return {"p": p, "detail": detail, "n_total": int(total_w)}


def hour_factor(stats: dict, provider: str, hour: int | None = None) -> float:
    hour = datetime.now(timezone.utc).hour if hour is None else int(hour)
    item = stats["hour"].get((provider, hour % 24))
    if not item or item["n"] < 3:
        return 1.0
    p_hour = _posterior(item, prior_pair(stats))
    p_prov = _posterior(stats["provider"].get(provider), prior_pair(stats))
    if p_prov <= 0:
        return 1.0
    return max(1.0 - HOUR_BUCKET_PENALTY, min(1.0 + HOUR_BUCKET_PENALTY, p_hour / p_prov))


def burst_failed(provider: str, country: str, *, window_min: int = BURST_WINDOW_MIN,
                 limit: int = BURST_FAIL_LIMIT) -> bool:
    conn = None
    try:
        conn = db._sqlite_conn()
        rows = conn.execute(
            "select status, failure_code from sms_pool_reservations "
            "where provider=? and country=? and created_at >= datetime('now', ?) "
            "order by id desc limit ?",
            (provider, country, "-%d minute" % int(window_min), int(limit)),
        ).fetchall()
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if len(rows) < int(limit):
        return False
    # 只有质量类失败才触发熔断；我们自己的流程失败不该封号段
    return all(classify_failure(r["status"], r["failure_code"]) != "ok" for r in rows) and all(
        classify_failure(r["status"], r["failure_code"]) == "quality" for r in rows
    )


def country_streak(provider: str, country: str, *, window_min: int = DIVERSITY_WINDOW_MIN) -> int:
    """近 N 分钟同一 (平台,国家) 的购买次数，用于连买降权。"""
    conn = None
    try:
        conn = db._sqlite_conn()
        row = conn.execute(
            "select count(*) c from sms_pool_reservations "
            "where provider=? and country=? and created_at >= datetime('now', ?)",
            (provider, country, "-%d minute" % int(window_min)),
        ).fetchone()
        return int(row["c"] or 0) if row else 0
    except Exception:
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def rank_candidates(provider: str, rows, max_usd: float, stats: dict | None = None,
                    *, need: int = 1, sample: bool = True) -> list:
    """打分排序：期望成本 = 单价 / p（p 可用 Thompson 采样）。"""
    stats = stats if stats is not None else build_stats()
    prior = prior_pair(stats)
    out = []
    for row in rows or []:
        try:
            cost = float(row.get("cost") or 0)
            stock = int(row.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if cost <= 0 or stock <= 0 or cost > float(max_usd or 0):
            continue
        country = str(row.get("country") or "")
        blend = blended_probability(stats, provider, country, sample=sample)
        p_mean = _posterior(stats["country"].get((provider, country)), prior)
        stock_credit = min(1.0, stock / max(1.0, float(need) * STOCK_FULL_CREDIT))
        p_eff = max(0.002, blend["p"] * hour_factor(stats, provider) * (0.6 + 0.4 * stock_credit))
        streak = country_streak(provider, country)
        diversity = 1.0 + DIVERSITY_STEP * max(0, streak - DIVERSITY_FREE)
        out.append({
            "provider": provider,
            "country": country,
            "cost_usd": round(cost, 5),
            "stock": stock,
            "p_success": round(p_eff, 4),
            "p_mean": round(p_mean, 4),
            "samples": int(blend["n_total"]),
            "streak": streak,
            "expected_cost_usd": round(cost / max(1e-6, p_eff), 5),
            "ts_cost_usd": round(cost / max(1e-6, p_eff) * diversity, 5),
            "burst_blocked": burst_failed(provider, country),
        })
    out.sort(key=lambda item: (item["burst_blocked"], item["ts_cost_usd"]))
    return out


def budget_verdict(stats: dict, cap_cny_hour: float | None, cap_cny_24h: float | None = None) -> dict:
    spent_1h = float(stats.get("spent_cny_1h") or 0)
    spent_24h = float(stats.get("spent_cny_24h") or 0)
    verdict = {
        "spent_cny_1h": round(spent_1h, 3),
        "spent_cny_24h": round(spent_24h, 3),
        "refunded_cny_24h": round(float(stats.get("refunded_cny_24h") or 0), 3),
        "unknown_cny_24h": round(float(stats.get("unknown_cny_24h") or 0), 3),
        "inflight_cny_24h": round(float(stats.get("inflight_cny_24h") or 0), 3),
        "blocked": False, "reason": "",
    }
    if cap_cny_hour and spent_1h >= float(cap_cny_hour):
        verdict["blocked"] = True
        verdict["reason"] = "近 1 小时实扣 %.2f 已达上限 %.2f" % (spent_1h, float(cap_cny_hour))
    elif cap_cny_24h and spent_24h >= float(cap_cny_24h):
        verdict["blocked"] = True
        verdict["reason"] = "近 24 小时实扣 %.2f 已达上限 %.2f" % (spent_24h, float(cap_cny_24h))
    return verdict
