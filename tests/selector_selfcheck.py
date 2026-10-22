# -*- coding: utf-8 -*-
"""选号器自检（纯函数，不触网、不读库）。

用法：python tests/selector_selfcheck.py
每轮改动后必须全绿才能进入下一轮。
"""
import sys

sys.path.insert(0, "/opt/turb-gpt-register")

from core import pool_selector as sel

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def _stats(region=None, country=None, provider=None, gl=None, hour=None):
    def bucket(ok, q, f):
        return {"w_ok": ok, "w_q": q, "w_f": f, "n": 1, "ok": 1 if ok > 0 else 0}

    return {
        "region": region or {},
        "country": country or {},
        "provider": provider or {},
        "global": gl or {"__all__": bucket(10.0, 90.0, 0.0)},
        "hour": hour or {},
        "weekday": {},
        "spent_cny_1h": 0.0, "spent_cny_24h": 0.0,
        "refunded_cny_24h": 0.0, "unknown_cny_24h": 0.0, "inflight_cny_24h": 0.0,
    }


@case
def test_classify_failure():
    assert sel.classify_failure("completed", "") == "ok"
    assert sel.classify_failure("released", "fraud_guard") == "quality"
    assert sel.classify_failure("released", "voip_phone_disallowed") == "quality"
    assert sel.classify_failure("released", "code_timeout") == "flow"
    assert sel.classify_failure("released", "worker_shutdown") == "flow"
    assert sel.classify_failure("orphaned", "activation_state_missing") == "flow"


@case
def test_prior_follows_data():
    s = _stats()
    a, b = sel.prior_pair(s)
    p = a / (a + b)
    # 全局 10/100 → 先验应接近 10%，不是写死的 30%
    assert 0.05 < p < 0.20, p


@case
def test_posterior_shrinks_small_samples():
    s = _stats(region={("p", "c", "r"): {"w_ok": 1.0, "w_q": 0.0, "w_f": 0.0, "n": 1, "ok": 1}})
    prior = sel.prior_pair(s)
    p = sel._posterior(s["region"][("p", "c", "r")], prior)
    assert p < 0.5, p          # 1/1 不能被当成 100%
    assert p > 0.10, p         # 也不能压到 0


@case
def test_blend_prefers_better_layer():
    s = _stats(
        region={("p", "c", "r"): {"w_ok": 0.0, "w_q": 20.0, "w_f": 0.0, "n": 20, "ok": 0}},
        gl={"__all__": {"w_ok": 50.0, "w_q": 50.0, "w_f": 0.0, "n": 100, "ok": 50}},
    )
    blend = sel.blended_probability(s, "p", "c", "r")
    assert blend["p"] < 0.3, blend   # 该地区 20 连败，应明显低于全局 50%


@case
def test_rank_prefers_cheaper_expected_cost():
    sel.burst_failed = lambda *a, **k: False
    sel.country_streak = lambda *a, **k: 0
    s = _stats(
        country={
            ("p", "good"): {"w_ok": 8.0, "w_q": 2.0, "w_f": 0.0, "n": 10, "ok": 8},
            ("p", "bad"): {"w_ok": 0.0, "w_q": 10.0, "w_f": 0.0, "n": 10, "ok": 0},
        },
        gl={"__all__": {"w_ok": 8.0, "w_q": 92.0, "w_f": 0.0, "n": 100, "ok": 8}},
    )
    rows = [
        {"country": "bad", "cost": 0.02, "count": 100},
        {"country": "good", "cost": 0.04, "count": 100},
    ]
    ranked = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False)
    assert ranked[0]["country"] == "good", ranked[0]
    assert ranked[0]["expected_cost_usd"] < ranked[1]["expected_cost_usd"]


@case
def test_diversity_penalty_applies():
    sel.burst_failed = lambda *a, **k: False
    sel.country_streak = lambda *a, **k: 5
    s = _stats(gl={"__all__": {"w_ok": 5.0, "w_q": 95.0, "w_f": 0.0, "n": 100, "ok": 5}})
    rows = [{"country": "c1", "cost": 0.05, "count": 100}]
    ranked = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False)
    assert ranked[0]["streak"] == 5
    assert ranked[0]["ts_cost_usd"] > ranked[0]["expected_cost_usd"]


@case
def test_hour_factor_clamped():
    s = _stats(
        provider={"p": {"w_ok": 10.0, "w_q": 90.0, "w_f": 0.0, "n": 100, "ok": 10}},
        hour={("p", 3): {"w_ok": 0.0, "w_q": 50.0, "w_f": 0.0, "n": 50, "ok": 0}},
    )
    f = sel.hour_factor(s, "p", hour=3)
    assert 0.6 <= f <= 1.4, f


@case
def test_budget_blocks_only_when_reached():
    s = _stats()
    s["spent_cny_1h"] = 5.0
    v = sel.budget_verdict(s, 3.0, 0)
    assert v["blocked"] is True
    v2 = sel.budget_verdict(s, 0, 0)
    assert v2["blocked"] is False
    s["spent_cny_24h"] = 100.0
    v3 = sel.budget_verdict(s, 0, 50.0)
    assert v3["blocked"] is True


@case
def test_thompson_sample_in_range():
    s = _stats(gl={"__all__": {"w_ok": 3.0, "w_q": 97.0, "w_f": 0.0, "n": 100, "ok": 3}})
    prior = sel.prior_pair(s)
    vals = [sel.sample_probability(s["global"]["__all__"], prior) for _ in range(200)]
    assert all(0.0 <= v <= 1.0 for v in vals)
    assert 0.005 < sum(vals) / len(vals) < 0.5, sum(vals) / len(vals)


@case
def test_provider_prior_uses_own_history():
    s = _stats(
        provider={"vaksms": {"w_ok": 0.0, "w_q": 27.0, "w_f": 0.0, "n": 27, "ok": 0},
                  "tiger": {"w_ok": 6.0, "w_q": 60.0, "w_f": 0.0, "n": 66, "ok": 6}},
        gl={"__all__": {"w_ok": 10.0, "w_q": 90.0, "w_f": 0.0, "n": 100, "ok": 10}},
    )
    a_v, b_v = sel.provider_prior(s, "vaksms")
    a_t, b_t = sel.provider_prior(s, "tiger")
    p_v, p_t = a_v / (a_v + b_v), a_t / (a_t + b_t)
    assert p_v < p_t, (p_v, p_t)          # 0/27 的平台应低于 6/66
    assert p_v < 0.05, p_v                # 明显低于全局 10%


@case
def test_provider_prior_shrinks_when_sparse():
    s = _stats(
        provider={"new": {"w_ok": 1.0, "w_q": 0.0, "w_f": 0.0, "n": 1, "ok": 1}},
        gl={"__all__": {"w_ok": 10.0, "w_q": 90.0, "w_f": 0.0, "n": 100, "ok": 10}},
    )
    a, b = sel.provider_prior(s, "new")
    p = a / (a + b)
    assert 0.05 < p < 0.35, p             # 1/1 向全局收缩，不会变 100%


@case
def test_health_multiplier_thresholds():
    assert sel.health_multiplier(4, 4) == 1.0        # 样本不足不判断
    assert sel.health_multiplier(10, 8) == 0.5       # 80% 流程失败
    assert sel.health_multiplier(10, 6) == 0.75      # 60%
    assert sel.health_multiplier(10, 2) == 1.0       # 正常


@case
def test_health_penalty_lowers_p():
    sel.burst_failed = lambda *a, **k: False
    sel.country_streak = lambda *a, **k: 0
    s = _stats(gl={"__all__": {"w_ok": 5.0, "w_q": 95.0, "w_f": 0.0, "n": 100, "ok": 5}})
    rows = [{"country": "c1", "cost": 0.05, "count": 100}]

    sel.provider_health = lambda *a, **k: {"multiplier": 1.0, "total": 10, "flow": 1, "quality": 9, "ok": 0}
    healthy = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False)[0]

    sel.provider_health = lambda *a, **k: {"multiplier": 0.5, "total": 10, "flow": 8, "quality": 2, "ok": 0}
    sick = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False)[0]

    assert sick["health_multiplier"] == 0.5, sick
    assert sick["p_success"] < healthy["p_success"], (sick, healthy)
    ratio = sick["p_success"] / healthy["p_success"]
    assert 0.45 <= ratio <= 0.55, ratio


@case
def test_expected_cost_gate_filters():
    sel.burst_failed = lambda *a, **k: False
    sel.country_streak = lambda *a, **k: 0
    sel.provider_health = lambda *a, **k: {"multiplier": 1.0, "total": 0, "flow": 0, "quality": 0, "ok": 0}
    s = _stats(
        country={("p", "c1"): {"w_ok": 0.0, "w_q": 10.0, "w_f": 0.0, "n": 10, "ok": 0}},
        gl={"__all__": {"w_ok": 1.0, "w_q": 99.0, "w_f": 0.0, "n": 100, "ok": 1}},
    )
    rows = [{"country": "c1", "cost": 0.05, "count": 100}]
    # p 约 1% → 期望成本约 $5
    all_rows = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False, max_expected_cost=0)
    assert all_rows, "无闸门时应有候选"
    assert all_rows[0]["expected_cost_usd"] > 1.0
    gated = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False, max_expected_cost=1.0)
    assert gated == [], gated


@case
def test_optimistic_probability_above_mean():
    prior = (0.03, 3.97)
    opt = sel.optimistic_probability(None, prior)
    mean = prior[0] / (prior[0] + prior[1])
    assert opt > mean, (opt, mean)
    assert opt < 0.5, opt
    # 乐观值必须落在 [mean, 0.95] 区间，且不低于均值
    item = {"w_ok": 50.0, "w_q": 50.0, "w_f": 0.0, "n": 100, "ok": 50}
    opt2 = sel.optimistic_probability(item, prior)
    mean2 = 50.0 / 100.0
    assert mean2 <= opt2 <= 0.95, (opt2, mean2)


@case
def test_unexplored_country_ranks_first():
    sel.burst_failed = lambda *a, **k: False
    sel.country_streak = lambda *a, **k: 0
    sel.provider_health = lambda *a, **k: {"multiplier": 1.0, "total": 0, "flow": 0, "quality": 0, "ok": 0}
    s = _stats(
        country={("p", "known"): {"w_ok": 1.0, "w_q": 9.0, "w_f": 0.0, "n": 10, "ok": 1}},
        gl={"__all__": {"w_ok": 1.0, "w_q": 99.0, "w_f": 0.0, "n": 100, "ok": 1}},
    )
    rows = [
        {"country": "known", "cost": 0.05, "count": 100},
        {"country": "fresh", "cost": 0.05, "count": 100},
    ]
    ranked = sel.rank_candidates("p", rows, 1.0, s, need=1, sample=False, max_expected_cost=0)
    assert ranked[0]["country"] == "fresh", ranked[0]


@case
def test_weekday_factor_clamped_and_noop():
    s = _stats(provider={"p": {"w_ok": 10.0, "w_q": 90.0, "w_f": 0.0, "n": 100, "ok": 10}})
    assert sel.weekday_factor(s, "p", weekend=True) == 1.0      # 无样本不修正
    s["weekday"] = {("p", 1): {"w_ok": 0.0, "w_q": 40.0, "w_f": 0.0, "n": 40, "ok": 0}}
    f = sel.weekday_factor(s, "p", weekend=True)
    assert 0.6 <= f <= 0.8, f                                  # 周末 0/40 → 明显下调
    s["weekday"] = {("p", 0): {"w_ok": 0.0, "w_q": 40.0, "w_f": 0.0, "n": 40, "ok": 0}}
    assert sel.weekday_factor(s, "p", weekend=True) == 1.0      # 周末无数据不修正


@case
def test_params_from_config():
    from config import codex as cc

    old = getattr(cc, "SMS_POOL_STATS_HALF_LIFE_HOURS", None)
    try:
        cc.SMS_POOL_STATS_HALF_LIFE_HOURS = 24.0
        assert abs(sel.half_life_hours() - 24.0) < 1e-6
        cc.SMS_POOL_STATS_HALF_LIFE_HOURS = 6.0
        assert abs(sel.half_life_hours() - 6.0) < 1e-6
        # 衰减：半衰期 6h 时，6 小时前权重应为 0.5
        w6 = sel._decay(6.0)
        assert 0.45 < w6 < 0.55, w6
        cc.SMS_POOL_EXPLORE_Z = 1.0
        assert abs(sel.explore_z() - 1.0) < 1e-6
    finally:
        if old is None:
            try:
                delattr(cc, "SMS_POOL_STATS_HALF_LIFE_HOURS")
            except AttributeError:
                pass
        else:
            cc.SMS_POOL_STATS_HALF_LIFE_HOURS = old
        try:
            delattr(cc, "SMS_POOL_EXPLORE_Z")
        except AttributeError:
            pass


@case
def test_config_defaults_present():
    from config import sms_pool as cc

    cfg_reload = getattr(cc, "reload_all", None)
    for name, want in (
        ("SMS_POOL_MAX_EXPECTED_COST_USD", float),
        ("SMS_POOL_STATS_HALF_LIFE_HOURS", float),
        ("SMS_POOL_EXPLORE_Z", float),
        ("SMS_POOL_BURST_WINDOW_MIN", int),
        ("SMS_POOL_BURST_FAIL_LIMIT", int),
        ("SMS_POOL_DIVERSITY_FREE", int),
        ("SMS_POOL_DIVERSITY_STEP", float),
    ):
        value = getattr(cc, name, None)
        assert value is not None, "缺少配置 " + name
        float(value)  # 必须可转数值
    w, limit = sel.burst_params()
    assert w >= 5 and limit >= 2
    free_n, step = sel.diversity_params()
    assert free_n >= 1 and 0 <= step <= 1


def main():
    passed = failed = 0
    for fn in CASES:
        try:
            fn()
            passed += 1
            print("PASS", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL", fn.__name__, "->", type(exc).__name__, str(exc)[:160])
    print("\n结果：%d 通过 / %d 失败 / 共 %d" % (passed, failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
