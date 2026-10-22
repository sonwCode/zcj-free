# -*- coding: utf-8 -*-
"""选号器真实数据回测：用早期数据训练，评估最近一段的 p 是否校准。

用法：python tests/selector_backtest.py
输出：Brier 分数、按预测分箱的实际成功率、与"全局均值基线"的对比。
"""
import json
import sys

sys.path.insert(0, "/opt/turb-gpt-register")

import config as cfg  # noqa: E402

cfg.reload_all()

from core import db, pool_selector as sel  # noqa: E402

TRAIN_HOURS = 120
TEST_AGE_H = 24          # 最近 24 小时作为评估集


def load_test_rows():
    conn = db._sqlite_conn()
    try:
        rows = conn.execute(
            "select provider, country, status, failure_code, created_at, activation_metadata "
            "from sms_pool_reservations "
            "where created_at >= datetime('now', ?) and status in "
            "('completed','released','failed','expired','orphaned')",
            ("-%d hour" % TEST_AGE_H,),
        ).fetchall()
    finally:
        conn.close()
    return rows


def main():
    train = sel.build_stats(hours=TRAIN_HOURS, older_than_hours=TEST_AGE_H)
    rows = load_test_rows()
    print("训练窗口: %d~%d 小时前 | 评估样本: %d" % (TRAIN_HOURS, TEST_AGE_H, len(rows)))
    if not rows:
        print("没有可评估的样本（数据不足）")
        return 0

    prior = sel.prior_pair(train)
    g = train["global"]["__all__"]
    base_rate = (g["w_ok"] / (g["w_ok"] + g["w_q"])) if (g["w_ok"] + g["w_q"]) > 0 else 0.05
    print("训练集全局成功率(质量口径): %.3f%%" % (100 * base_rate))

    buckets = {}
    brier = 0.0
    n = 0
    for row in rows:
        provider = str(row["provider"] or "").lower()
        country = str(row["country"] or "")
        try:
            meta = json.loads(row["activation_metadata"] or "{}")
            region = str(meta.get("phone_country_name") or "").strip()
        except Exception:
            region = ""
        blend = sel.blended_probability(train, provider, country, region, sample=False)
        p = min(0.95, max(0.001, blend["p"]))
        ok = 1.0 if sel.classify_failure(row["status"], row["failure_code"]) == "ok" else 0.0
        brier += (p - ok) ** 2
        n += 1
        key = min(9, int(p * 10))
        b = buckets.setdefault(key, [0, 0])
        b[0] += 1
        b[1] += int(ok)

    brier /= max(1, n)
    base_brier = sum((base_rate - (1.0 if sel.classify_failure(r["status"], r["failure_code"]) == "ok" else 0.0)) ** 2 for r in rows) / max(1, n)
    print("Brier(模型) = %.5f | Brier(全局基线) = %.5f" % (brier, base_brier))
    print("\n预测区间    样本   实际成功   实际成功率   预测均值")
    for key in sorted(buckets):
        cnt, okc = buckets[key]
        lo, hi = key / 10.0, (key + 1) / 10.0
        print("  %.1f%%~%.1f%%  %5d  %6d     %6.2f%%     %.2f%%" % (
            lo * 100, hi * 100, cnt, okc, 100.0 * okc / max(1, cnt), 100.0 * (lo + hi) / 2))
    verdict = "模型优于基线" if brier <= base_brier else "模型未优于基线（需重新校准）"
    print("\n结论:", verdict)
    return 0 if brier <= base_brier else 2


if __name__ == "__main__":
    sys.exit(main())
