# -*- coding: utf-8 -*-
"""号码池智能选号器 v2。

核心思想：把每个 (平台, 国家, 地区) 当成一个赌臂，按"每个成功号码的期望成本"
下注，而不是按绝对价格或地区黑名单。

模型
----
1. 分层经验贝叶斯（解决样本稀疏）：
     p = (n_r*p_r + n_c*p_c + n_p*p_p + n_g*p_g) / (n_r + n_c + n_p + n_g)
   p_r 地区级、p_c 国家级、p_p 平台级、p_g 全局；各层用 Beta 后验，先验 30%。
   样本多的层级自动占更大权重，冷门地区向平台/全局水平收缩，不会因为 1/1
   就被当成 100%。

2. 时间衰减：半衰期 12 小时，越近的结果权重越高。

3. 时段修正：按小时桶统计成功率，乘以 (0.75~1.25) 的修正系数。

4. 库存折算：库存远小于需求时，实际可成交概率打折。

5. 探索：UCB 项，样本越少加分越多，避免冷门号段永远不被尝试。

6. 熔断：同一 (平台,国家) 30 分钟内连续失败 >=3 次，本轮跳过（不是永久拉黑）。

7. 预算节流：最近 60 分钟的号码花费超过上限时给出 pause 建议。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from core import db

HALF_LIFE_HOURS = 12.0
PRIOR_ALPHA = 1.5
PRIOR_BETA = 3.5
UCB_C = 0.45
BURST_WINDOW_MIN = 30
BURST_FAIL_LIMIT = 3
HOUR_BUCKET_PENALTY = 0.25      # 时段修正幅度上限
STOCK_FULL_CREDIT = 3.0         # 库存 >= 3 倍需求时视为充足


def _decay(age_hours: float) -> float:
    if age_hours <= 0:
        return 1.0
    return 0.5 ** (age_hours / HALF_LIFE_HOURS)


def _empty() -> dict:
    return {"w_ok": 0.0, "w_n": 0.0, "n": 0, "ok": 0}


def _add(bucket: dict, key, weight: float, ok: bool) -> None:
    item = bucket.setdefault(key, _empty())
    item["w_n"] += weight
    item["n"] += 1
    if ok:
        item["w_ok"] += weight
        item["ok"] += 1


def build_stats(hours: int = 72) -> dict:
    """一次性把四个层级 + 时段桶算出来，供本轮所有候选复用。"""
    stats = {
        "region": {},
        "country": {},
        "provider": {},
        "global": _empty(),
        "hour": {},
        "spent_cny_1h": 0.0,
        "spent_cny_24h": 0.0,
        "refunded_cny_24h": 0.0,
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
        ok = str(row["status"] or "") == "completed"
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
        except Exception:
            age_h = 0.0
        w = _decay(age_h)

        _add(stats["region"], (provider, country, region or "#" + country), w, ok)
        _add(stats["country"], (provider, country), w, ok)
        _add(stats["provider"], provider, w, ok)
        _add(stats["global"], "__all__", w, ok)
        _add(stats["hour"], (provider, ts.astimezone(timezone.utc).hour if age_h else now.hour), w, ok)

        # 花费只能算"真正扣钱"的号码：cancelled / *cancelled 会退款，
        # 算进去会把预算统计放大十几倍（实测 736 个 cancelled 占 ¥126）。
        rel = str(row["release_result"] or "").lower()
        refunded = ("cancel" in rel) or ("refund" in rel) or ("released_by_oauth" in rel)
        in_flight = str(row["status"] or "") in {"reserved", "running"}
        try:
            price = float(row["price_cny"] or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price > 0:
            if refunded:
                if age_h <= 24.0:
                    stats["refunded_cny_24h"] += price
            elif not in_flight:
                if age_h <= 1.0:
                    stats["spent_cny_1h"] += price
                if age_h <= 24.0:
                    stats["spent_cny_24h"] += price
    return stats


def _posterior(item: dict | None) -> float:
    if not item or item["n"] <= 0:
        return PRIOR_ALPHA / (PRIOR_ALPHA + PRIOR_BETA)
    return (item["w_ok"] + PRIOR_ALPHA) / (item["w_n"] + PRIOR_ALPHA + PRIOR_BETA)


def blended_probability(stats: dict, provider: str, country: str, region: str = "") -> dict:
    """分层混合后的成功率，附各层明细，便于页面上解释。"""
    p_region = _posterior(stats["region"].get((provider, country, region or "#" + country)))
    p_country = _posterior(stats["country"].get((provider, country)))
    p_provider = _posterior(stats["provider"].get(provider))
    p_global = _posterior(stats["global"].get("__all__"))

    n_region = stats["region"].get((provider, country, region or "#" + country), {}).get("n", 0)
    n_country = stats["country"].get((provider, country), {}).get("n", 0)
    n_provider = stats["provider"].get(provider, {}).get("n", 0)
    n_global = stats["global"].get("__all__", {}).get("n", 0)

    weights = [float(n_region), float(n_country) * 0.6, float(n_provider) * 0.3, float(n_global) * 0.1]
    total_w = sum(weights) or 1.0
    p = (p_region * weights[0] + p_country * weights[1]
         + p_provider * weights[2] + p_global * weights[3]) / total_w
    return {
        "p": p, "p_region": p_region, "p_country": p_country,
        "p_provider": p_provider, "p_global": p_global,
        "n_region": int(n_region), "n_country": int(n_country),
        "n_provider": int(n_provider), "n_global": int(n_global),
    }


def hour_factor(stats: dict, provider: str, hour: int | None = None) -> float:
    """当前时段的成功率修正系数。样本不足返回 1.0。"""
    hour = datetime.now(timezone.utc).hour if hour is None else int(hour)
    item = stats["hour"].get((provider, hour % 24))
    if not item or item["n"] < 3:
        return 1.0
    p_hour = _posterior(item)
    p_provider = _posterior(stats["provider"].get(provider))
    if p_provider <= 0:
        return 1.0
    factor = p_hour / p_provider
    return max(1.0 - HOUR_BUCKET_PENALTY, min(1.0 + HOUR_BUCKET_PENALTY, factor))


def burst_failed(provider: str, country: str, *, window_min: int = BURST_WINDOW_MIN,
                 limit: int = BURST_FAIL_LIMIT) -> bool:
    conn = None
    try:
        conn = db._sqlite_conn()
        rows = conn.execute(
            "select status from sms_pool_reservations "
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
    return all(str(r["status"] or "") != "completed" for r in rows)


def _ucb(total_weight: float, n: int) -> float:
    if n <= 0:
        return 0.5
    return UCB_C * math.sqrt(math.log(max(2.0, total_weight)) / max(1.0, float(n)))


def rank_candidates(provider: str, rows, max_usd: float, stats: dict | None = None,
                    *, need: int = 1) -> list:
    """给一个平台的价格表打分排序，返回可直接消费的候选列表。"""
    stats = stats if stats is not None else build_stats()
    total_weight = stats["global"].get("__all__", {}).get("w_n", 0.0) or 1.0
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
        blend = blended_probability(stats, provider, country)
        hf = hour_factor(stats, provider)
        p_eff = min(0.95, blend["p"] * hf)
        stock_credit = min(1.0, stock / max(1.0, float(need) * STOCK_FULL_CREDIT))
        p_adj = max(0.01, p_eff * (0.6 + 0.4 * stock_credit))
        p_score = min(0.95, p_adj + _ucb(total_weight, blend["n_region"] or blend["n_country"]))
        expected_cost = cost / max(1e-6, p_score)
        out.append({
            "provider": provider,
            "country": country,
            "cost_usd": round(cost, 5),
            "stock": stock,
            "p_success": round(p_eff, 3),
            "p_base": round(blend["p"], 3),
            "hour_factor": round(hf, 3),
            "samples": blend["n_region"] or blend["n_country"],
            "score": round(p_score, 3),
            "expected_cost_usd": round(expected_cost, 5),
            "burst_blocked": burst_failed(provider, country),
        })
    out.sort(key=lambda item: (item["burst_blocked"], item["expected_cost_usd"]))
    return out


def budget_verdict(stats: dict, cap_cny_hour: float | None, cap_cny_24h: float | None = None) -> dict:
    """预算节流建议：不直接停，只给调用方一个可解释的判断。"""
    spent_1h = float(stats.get("spent_cny_1h") or 0)
    spent_24h = float(stats.get("spent_cny_24h") or 0)
    verdict = {"spent_cny_1h": round(spent_1h, 3), "spent_cny_24h": round(spent_24h, 3),
               "refunded_cny_24h": round(float(stats.get("refunded_cny_24h") or 0), 3),
               "blocked": False, "reason": ""}
    if cap_cny_hour and spent_1h >= float(cap_cny_hour):
        verdict["blocked"] = True
        verdict["reason"] = "近 1 小时花费 %.2f 已达上限 %.2f" % (spent_1h, float(cap_cny_hour))
    elif cap_cny_24h and spent_24h >= float(cap_cny_24h):
        verdict["blocked"] = True
        verdict["reason"] = "近 24 小时花费 %.2f 已达上限 %.2f" % (spent_24h, float(cap_cny_24h))
    return verdict
