# -*- coding: utf-8 -*-
"""号码池智能选号器。

一句话：把每个 (平台, 国家, 地区) 当成一个赌臂，按"每个成功号码的期望成本"下注。

模型
----
1. 分层经验贝叶斯
   p = 地区层/国家层/平台层/全局层 的后验按样本量加权混合；
   各层用 Beta 后验，先验来自全局实测成功率（不是写死的 30%）。
   样本少的层自动向上一层收缩，1/1 不会被当成 100%。

2. 失败类型分离
   quality_failure（fraud_guard / voip / phone_number_in_use 等）计入成功率分母；
   flow_failure（code_timeout / transient_* / worker_shutdown 等）单列，
   避免我们自己的流程 bug 把好号段一起打死。

3. 时间衰减 + 时段/周末修正
   统计按 12 小时半衰期衰减；小时桶与工作日/周末各自算修正系数（0.75~1.25）。

4. 冷启动探索
   自有样本为 0 的号段用 Beta 后验的乐观上界（均值 + z·σ）估值，
   新号段因此有机会被试；样本积累后自然回归均值。

5. 分散与熔断
   同一 (平台,国家) 近 60 分钟买太多会按次数降权；
   30 分钟内连续 3 次"质量型"失败则本轮排到最后（不做永久黑名单）。

6. 花费三分类
   confirmed（completed / failed / expired / orphaned / 未释放）计入预算；
   refunded（cancelled 等）与 unknown（released 但无释放记录）单列，避免虚高。

参数（config/sms_pool.py，可用 .env 覆盖）
   SMS_POOL_MAX_EXPECTED_COST_USD   每成功号成本上限，0=不限制
   SMS_POOL_STATS_HALF_LIFE_HOURS   统计半衰期
   SMS_POOL_EXPLORE_Z               冷启动乐观系数
   SMS_POOL_BURST_WINDOW_MIN / _LIMIT   熔断窗口与次数
   SMS_POOL_DIVERSITY_FREE / _STEP  同号段免惩罚次数与步长
   SMS_POOL_HOURLY_BUDGET_CNY / SMS_POOL_DAILY_BUDGET_CNY  预算闸门

自检
   python tests/selector_selfcheck.py   纯函数单测（不触网、不读库）
   python tests/selector_backtest.py    真实数据回测（Brier 分数 + 分箱校准）
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone

from core import db

def _param(name: str, default):
    """参数优先取配置（config.sms_pool → config.codex），缺失时用默认值。"""
    for module_name in ("codex", "sms_pool"):
        try:
            mod = __import__("config." + module_name, fromlist=["x"])
        except Exception:
            continue
        value = getattr(mod, name, None)
        if value in (None, ""):
            continue
        try:
            return type(default)(value) if not isinstance(value, type(default)) else value
        except Exception:
            continue
    return default


def half_life_hours() -> float:
    return max(1.0, float(_param("SMS_POOL_STATS_HALF_LIFE_HOURS", 12.0)))


def explore_z() -> float:
    return max(0.0, min(4.0, float(_param("SMS_POOL_EXPLORE_Z", 2.0))))


def burst_params() -> tuple:
    return (
        max(5, int(_param("SMS_POOL_BURST_WINDOW_MIN", 30))),
        max(2, int(_param("SMS_POOL_BURST_FAIL_LIMIT", 3))),
    )


def diversity_params() -> tuple:
    return (
        max(1, int(_param("SMS_POOL_DIVERSITY_FREE", 2))),
        max(0.0, min(1.0, float(_param("SMS_POOL_DIVERSITY_STEP", 0.15)))),
    )


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
    return 1.0 if age_hours <= 0 else 0.5 ** (age_hours / half_life_hours())


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


def build_stats(hours: int = 72, older_than_hours: float = 0.0) -> dict:
    """统计窗口：[now-hours, now-older_than_hours)。

    older_than_hours > 0 用于回测：只用更早的数据训练，留出最近一段做评估。
    """
    stats = {
        "region": {}, "country": {}, "provider": {},
        "global": _empty(), "hour": {}, "weekday": {},
        "spent_cny_1h": 0.0, "spent_cny_24h": 0.0,
        "refunded_cny_24h": 0.0, "unknown_cny_24h": 0.0, "inflight_cny_24h": 0.0,
    }
    conn = None
    now = datetime.now(timezone.utc)
    try:
        conn = db._sqlite_conn()
        sql = (
            "select provider, country, status, failure_code, release_result, created_at, "
            "activation_metadata, price_cny from sms_pool_reservations "
            "where created_at >= datetime('now', ?)"
        )
        params = ["-%d hour" % int(hours)]
        if older_than_hours and float(older_than_hours) > 0:
            sql += " and created_at < datetime('now', ?)"
            params.append("-%d minute" % int(float(older_than_hours) * 60))
        rows = conn.execute(sql, tuple(params)).fetchall()
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
            weekday = ts.astimezone(timezone.utc).weekday()
        except Exception:
            age_h, hour, weekday = 0.0, now.hour, now.weekday()
        w = _decay(age_h)

        _add(stats["region"], (provider, country, region or "#" + country), w, kind)
        _add(stats["country"], (provider, country), w, kind)
        _add(stats["provider"], provider, w, kind)
        _add(stats["global"], "__all__", w, kind)
        _add(stats["hour"], (provider, hour), w, kind)
        _add(stats["weekday"], (provider, 1 if weekday >= 5 else 0), w, kind)

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


def provider_prior(stats: dict, provider: str) -> tuple:
    """平台级先验：样本足够时用平台自身成功率，否则向全局收缩。

    vaksms 27 次 0 成功，不该按全局 0.84% 估值——它更接近 0.3%。样本不足
    （<6）时按 n/(n+6) 的比例混合，避免单次偶然定调。
    """
    a_g, b_g = prior_pair(stats)
    item = stats["provider"].get(provider) if stats else None
    if not item:
        return a_g, b_g
    n = float(item["w_ok"] + item["w_q"])
    if n <= 0:
        return a_g, b_g
    p_prov = item["w_ok"] / n
    w = n / (n + 6.0)
    p_g = a_g / (a_g + b_g)
    p = w * p_prov + (1.0 - w) * p_g
    p = min(0.9, max(0.0005, p))
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
    prior = provider_prior(stats, provider)
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


def health_multiplier(total: int, flow: int) -> float:
    """平台健康度倍数：流程型失败过多说明是这个平台/链路有问题，不是号段。

    样本 <5 不判断（避免误伤）；流程失败占比 >=80% 打 5 折，>=60% 打 7.5 折。
    """
    if total < 5:
        return 1.0
    ratio = float(flow) / float(total)
    if ratio >= 0.8:
        return 0.5
    if ratio >= 0.6:
        return 0.75
    return 1.0


def provider_health(provider: str, *, window_min: int = 60) -> dict:
    """近 N 分钟该平台的真实表现（只统计已结算的记录）。"""
    conn = None
    try:
        conn = db._sqlite_conn()
        rows = conn.execute(
            "select status, failure_code from sms_pool_reservations "
            "where provider=? and created_at >= datetime('now', ?) "
            "and status in ('completed','released','failed','expired','orphaned')",
            (provider, "-%d minute" % int(window_min)),
        ).fetchall()
    except Exception:
        return {"multiplier": 1.0, "total": 0, "flow": 0, "quality": 0, "ok": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    ok = quality = flow = 0
    for row in rows:
        kind = classify_failure(row["status"], row["failure_code"])
        if kind == "ok":
            ok += 1
        elif kind == "quality":
            quality += 1
        else:
            flow += 1
    total = ok + quality + flow
    return {
        "multiplier": health_multiplier(total, flow),
        "total": total, "flow": flow, "quality": quality, "ok": ok,
    }


def weekday_factor(stats: dict, provider: str, weekend: bool | None = None) -> float:
    """周末/工作日修正系数，样本 <6 时不修正。"""
    if weekend is None:
        weekend = datetime.now(timezone.utc).weekday() >= 5
    item = stats["weekday"].get((provider, 1 if weekend else 0))
    if not item or item["n"] < 6:
        return 1.0
    prior = provider_prior(stats, provider)
    p_wd = _posterior(item, prior)
    p_all = _posterior(stats["provider"].get(provider), prior)
    if p_all <= 0:
        return 1.0
    factor = p_wd / p_all
    return max(1.0 - HOUR_BUCKET_PENALTY, min(1.0 + HOUR_BUCKET_PENALTY, factor))


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


def max_expected_cost_usd() -> float:
    """每成功一个号的成本上限（0 表示不限制）。"""
    try:
        from config import codex as _cfg

        return float(getattr(_cfg, "SMS_POOL_MAX_EXPECTED_COST_USD", 0) or 0)
    except Exception:
        return 0.0


EXPLORE_NEW = 1.0        # 兼容旧字段：成本不再打折，改为对 p 做乐观估计
EXPLORE_FEW = 1.0
EXPLORE_Z = 2.0          # 乐观系数：mean + Z*std


def optimistic_probability(item: dict | None, prior: tuple, z: float | None = None) -> float:
    """冷启动的乐观估计：Beta(α,β) 的 均值 + z×标准差。

    样本为 0 时用先验的乐观上界，让新号段有机会被试；样本足够后自然回归均值，
    探索强度由数据本身（方差）决定，不用人为设折扣。
    """
    z = explore_z() if z is None else float(z)
    a, b = _beta_params(item, prior)
    a, b = max(a, 1e-3), max(b, 1e-3)
    mean = a / (a + b)
    var = (a * b) / ((a + b) ** 2 * (a + b + 1.0))
    return min(0.95, mean + z * math.sqrt(max(0.0, var)))


def explore_bonus(samples: int) -> float:
    """保留字段用于展示；排序已改为对 p 做乐观估计，不再对成本打折。"""
    return 1.0


def rank_candidates(provider: str, rows, max_usd: float, stats: dict | None = None,
                    *, need: int = 1, sample: bool = True,
                    max_expected_cost: float | None = None) -> list:
    """打分排序：期望成本 = 单价 / p（p 可用 Thompson 采样）。

    max_expected_cost > 0 时，期望成本超过它的候选直接剔除——宁可这一轮不买，
    也不买"单个成功号要 $5"的号段。
    """
    stats = stats if stats is not None else build_stats()
    prior = provider_prior(stats, provider)
    _health_cache = {}
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
        health = _health_cache.get(provider)
        if health is None:
            health = provider_health(provider)
            _health_cache[provider] = health
        region_item = stats["region"].get((provider, country, "#" + country)) or {}
        country_item = stats["country"].get((provider, country)) or {}
        own_n = int(region_item.get("n") or 0) + int(country_item.get("n") or 0)
        samples_total = own_n
        if samples_total <= 0:
            # 全新号段：用先验的乐观上界，而不是先验均值，否则永远排不到前面
            base_p = max(blend["p"], optimistic_probability(None, prior))
        else:
            base_p = blend["p"]
        p_eff = max(
            0.002,
            base_p * hour_factor(stats, provider) * weekday_factor(stats, provider)
            * (0.6 + 0.4 * stock_credit)
            * float(health.get("multiplier") or 1.0),
        )
        streak = country_streak(provider, country)
        free_n, step = diversity_params()
        diversity = 1.0 + step * max(0, streak - free_n)
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
            "ts_cost_usd": round(cost / max(1e-6, p_eff) * diversity * explore_bonus(int(blend["n_total"])), 5),
            "burst_blocked": burst_failed(provider, country),
            "health_multiplier": float(health.get("multiplier") or 1.0),
            "health_total": int(health.get("total") or 0),
        })
    limit = max_expected_cost if max_expected_cost is not None else max_expected_cost_usd()
    if limit and limit > 0:
        out = [item for item in out if item["expected_cost_usd"] <= float(limit)]
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
