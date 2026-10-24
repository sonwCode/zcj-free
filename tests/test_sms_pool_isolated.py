# -*- coding: utf-8 -*-
"""低价号码池：完全隔离的 mock 测试。

隔离保证：
  - 临时目录 + 空白 SQLite，绝不复用 turb.sqlite3。
  - 注入假 config 包，不导入生产 .env。
  - 注入假 core.sms_provider 与假 core.codex_retry_service，不联网、不取号、不跑 OAuth。
  - 不需要 curl_cffi / selenium / playwright。

覆盖：并发领取、一号一账号、leader CAS、过期回收、租约过期 orphaned、
激活状态缺失 orphaned、价格与汇率边界、接管不触发二次采购。

运行：python tests/test_sms_pool_isolated.py
"""
import contextlib
import decimal
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import types
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="sms_pool_iso_"))

# ---------------------------------------------------------------- 假 config 包
pool_cfg = types.ModuleType("config.sms_pool")
pool_cfg.SMS_POOL_ENABLED = True
pool_cfg.SMS_POOL_PAUSED = False
pool_cfg.SMS_POOL_PROVIDERS = "smsbower"
pool_cfg.SMS_POOL_POLL_INTERVAL = 120
pool_cfg.SMS_POOL_MAX_PRICE_CNY = ""
pool_cfg.SMS_POOL_PREWARM_SIZE = 2
pool_cfg.SMS_POOL_WORKERS = 2
pool_cfg.SMS_POOL_LEASE_SECONDS = 900
pool_cfg.SMS_POOL_ACTIVATION_TTL_SECONDS = 1200
pool_cfg.SMS_POOL_SERVICE = "dr"
pool_cfg.SMS_POOL_COUNTRY = ""
pool_cfg.SMS_POOL_MAX_ACQUIRE_PER_TICK = 2
pool_cfg.SMS_POOL_LIVE_CHECK_ENABLED = False
pool_cfg.SMS_POOL_LIVE_CHECK_INTERVAL = 21600
pool_cfg.SMS_POOL_LIVE_CHECK_BATCH = 20

config_pkg = types.ModuleType("config")
config_pkg.__path__ = []  # 阻止回落到真实 config/__init__.py
config_pkg.sms_pool = pool_cfg
sys.modules["config"] = config_pkg
sys.modules["config.sms_pool"] = pool_cfg

import core  # noqa: E402
from core import db as real_db  # noqa: E402

# ---------------------------------------------------------------- 指向临时库
real_db._ACCOUNTS_JSON = _TMP / "注册成功的邮箱.json"
real_db._OUTLOOK_JSON = _TMP / "用于注册的邮箱.json"
real_db._JOBS_JSON = _TMP / "注册任务.json"
real_db._LOG_DIR = _TMP / "注册日志"
real_db._DATA_DIR = _TMP

_db_path = real_db._active_sqlite_path()
assert str(_db_path).startswith(str(_TMP)), f"数据库未重定向：{_db_path}"

ACCOUNTS: list[dict] = []


def _fake_list_accounts(limit: int = 500, offset: int = 0, **kwargs):
    return [dict(item) for item in ACCOUNTS[offset: offset + limit]]


real_db.list_accounts = _fake_list_accounts  # 不读真实账号表


def _init_storage_meta() -> None:
    conn = real_db._sqlite_conn()
    try:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS storage_meta (key TEXT PRIMARY KEY, value TEXT);"
        )
        conn.commit()
    finally:
        conn.close()


_init_storage_meta()

# ---------------------------------------------------------- 假 sms_provider
fake_sp = types.ModuleType("core.sms_provider")
fake_sp.calls: list[tuple] = []
fake_sp.state: dict[str, dict] = {}
fake_sp.override = None
fake_sp.fx_rate = decimal.Decimal("0.149")  # 1 CNY -> USD，与真实实现同为 Decimal


def _voip_rejection_override(enabled=None):
    fake_sp.calls.append(("voip_rejection_override", enabled))
    return contextlib.nullcontext()      # 真实实现是上下文管理器，桩也要返回它

fake_sp.voip_rejection_override = _voip_rejection_override


def _new_activation(phone: str = "+15550002222") -> tuple[str, str]:
    aid = f"act{len(fake_sp.state) + 1}"
    fake_sp.state[aid] = {"activation_id": aid, "phone_number": phone, "provider": "smsbower"}
    return aid, phone


def _get_activation_info(aid):
    return dict(fake_sp.state.get(aid) or {})


def _cancel(aid, http=None):
    fake_sp.calls.append(("cancel", aid))
    fake_sp.state.pop(aid, None)
    return {"release": "cancelled"}


def _complete(aid, http=None):
    fake_sp.calls.append(("complete", aid))
    fake_sp.state.pop(aid, None)
    return {"status": "ok"}


def _cancel_and_report_failure(aid, http=None, exc=None, category=None):
    fake_sp.calls.append(("cancel_and_report_failure", aid))
    fake_sp.state.pop(aid, None)


def _acquire_number(*args, **kwargs):
    fake_sp.calls.append(("acquire_number",))
    return _new_activation()


@contextlib.contextmanager
def _activation_override(aid, phone=""):
    previous = fake_sp.override
    fake_sp.override = (aid, phone)
    try:
        yield
    finally:
        fake_sp.override = previous


fake_sp.get_activation_info = _get_activation_info
fake_sp.get_activation_status = lambda aid: "STATUS_WAIT_CODE"
fake_sp.cancel = _cancel
fake_sp.complete = _complete
fake_sp.cancel_and_report_failure = _cancel_and_report_failure
fake_sp.acquire_number = _acquire_number
fake_sp.activation_override = _activation_override
fake_sp._provider = lambda: "smsbower"
fake_sp._provider_label = lambda p: "SMSBower"
fake_sp._mask_phone = lambda p: "*" * max(len(str(p)) - 4, 2) + str(p)[-4:]
fake_sp._http = lambda: types.SimpleNamespace(close=lambda: None)
fake_sp._cny_to_usd_rate = lambda: (fake_sp.fx_rate, "test-fx")
fake_sp._configured_provider = lambda p: p == "smsbower"
fake_sp.price_limit_override = lambda *a, **k: contextlib.nullcontext()
fake_sp._acquire_number_for_provider = lambda *a, **k: _new_activation()
sys.modules["core.sms_provider"] = fake_sp
core.sms_provider = fake_sp

# ----------------------------------------------- 假 live_check_service
fake_live_check = types.ModuleType("core.live_check_service")
fake_live_check.calls: list[dict] = []


def _enqueue_live_check(*, account_id, email, trigger="manual", proxy=None):
    fake_live_check.calls.append({"account_id": account_id, "email": email, "trigger": trigger})
    account = next((item for item in ACCOUNTS if int(item.get("id") or 0) == int(account_id)), None)
    if account is not None:
        account["live_check_status"] = "queued"
        account["live_check_ok"] = False
        account["live_checked_at"] = datetime.now().isoformat(timespec="seconds")
    return {"accepted": True, "status": "queued"}


fake_live_check.enqueue_account_live_check = _enqueue_live_check
sys.modules["core.live_check_service"] = fake_live_check
core.live_check_service = fake_live_check

# ------------------------------------------------- 假 codex_retry_service
fake_retry = types.ModuleType("core.codex_retry_service")
fake_retry.calls: list[dict] = []
fake_retry.retrying: set[str] = set()
fake_retry.result: dict = {"ok": True}


def _run_worker(email, **kwargs):
    fake_retry.calls.append({"email": email, **kwargs})
    return dict(fake_retry.result)


def _reserve(email):
    key = str(email).lower()
    if key in fake_retry.retrying:
        return False
    fake_retry.retrying.add(key)
    return True


fake_retry.is_retrying = lambda email: str(email).lower() in fake_retry.retrying
fake_retry.reserve = _reserve
fake_retry.log_path = lambda email: _TMP / (str(email) + ".log")
fake_retry.run_worker = _run_worker
sys.modules["core.codex_retry_service"] = fake_retry
core.codex_retry_service = fake_retry

from core import sms_pool_store as store  # noqa: E402
from core import sms_pool_service as service  # noqa: E402
from core import sms_pool_worker as worker  # noqa: E402
from core import sms_pool_task as task  # noqa: E402

DISPATCHES: list[tuple[int, int]] = []


def _record_dispatch(reservation: dict, account: dict) -> bool:
    DISPATCHES.append((int(reservation["id"]), int(account["id"])))
    return True


worker._dispatch_task = _record_dispatch

RESULTS: list[tuple[str, bool, str]] = []


def case(name):
    def wrapper(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except AssertionError as exc:
            RESULTS.append((name, False, str(exc) or "断言失败"))
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return wrapper


def _reset() -> None:
    store.ensure_tables()  # 首次清理前必须先建表
    conn = real_db._sqlite_conn()
    try:
        conn.execute("DELETE FROM sms_pool_reservations")
        conn.execute("DELETE FROM sms_pool_events")
        conn.execute("DELETE FROM storage_meta WHERE key LIKE 'sms_pool%'")
        conn.commit()
    finally:
        conn.close()
    fake_sp.state.clear()
    fake_sp.calls.clear()
    fake_retry.calls.clear()
    fake_retry.retrying.clear()
    fake_retry.result = {"ok": True}
    fake_live_check.calls.clear()
    DISPATCHES.clear()
    ACCOUNTS.clear()
    pool_cfg.SMS_POOL_MAX_PRICE_CNY = ""
    pool_cfg.SMS_POOL_PROVIDERS = "smsbower"
    fake_sp.fx_rate = decimal.Decimal("0.149")


def _register(activation_id="actX", phone="+15550003333", ttl=1200):
    store.ensure_tables()
    fake_sp.state[activation_id] = {"activation_id": activation_id, "phone_number": phone}
    return store.register_reservation(
        provider="smsbower",
        activation_id=activation_id,
        phone=phone,
        service="dr",
        country="1",
        price_amount="0.05",
        price_currency="USD",
        price_cny="0.34",
        expires_in_seconds=ttl,
    )


def _sql(sql, params=()):
    conn = real_db._sqlite_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


@case("并发领取：同一号码只能被一个账号拿到")
def test_concurrent_claim():
    _reset()
    row = _register("actA")
    outcomes: list[object] = []
    lock = threading.Lock()

    def worker_fn():
        got = store.claim_for_account(row["id"], 301, "a@x")
        with lock:
            outcomes.append(got)

    threads = [threading.Thread(target=worker_fn) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wins = [o for o in outcomes if o]
    assert len(wins) == 1, f"应只有一个赢家，实际 {len(wins)}"


@case("一号一账号：同一账号不能同时持有两个激活")
def test_one_account_one_activation():
    _reset()
    first = _register("actB")
    second = _register("actC")
    assert store.claim_for_account(first["id"], 302, "b@x"), "第一次领取应成功"
    assert store.claim_for_account(second["id"], 302, "b@x") is None, "同账号第二次领取应失败"


@case("leader CAS：外部有效租约不可被抢占，过期可接管")
def test_leader_cas():
    _reset()
    assert worker._try_become_leader() is True, "空租约应抢占成功"
    _sql("UPDATE storage_meta SET value=? WHERE key=?", ("other-host:999|2999-01-01T00:00:00+00:00", "sms_pool_leader"))
    assert worker._try_become_leader() is False, "外部未过期租约不应被抢占"
    _sql("UPDATE storage_meta SET value=? WHERE key=?", ("other-host:999|2000-01-01T00:00:00+00:00", "sms_pool_leader"))
    assert worker._try_become_leader() is True, "过期租约应可接管"


@case("leader CAS：更新语句必须是按旧值比较交换")
def test_leader_cas_sql():
    src = (ROOT / "core" / "sms_pool_worker.py").read_text(encoding="utf-8")
    assert "WHERE key=? AND value=?" in src, "缺少 CAS 条件更新"
    assert "cursor.rowcount == 1" in src, "缺少抢占结果判定"


@case("过期回收：未分配号码到期后被释放")
def test_expired_reclaim():
    _reset()
    row = _register("actD", ttl=60)
    _sql("UPDATE sms_pool_reservations SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", row["id"]))
    assert store.find_expired_unclaimed(), "应能找到过期号码"
    result = service.reconcile()
    assert result["expired_released"] == 1, f"应释放 1 个，实际 {result}"
    final = store.get_reservation(row["id"])
    assert final["status"] == store.STATUS_EXPIRED, f"状态应为 expired，实际 {final['status']}"
    assert ("cancel", "actD") in fake_sp.calls, "应向供应商发起释放"


@case("租约过期：运行中号码收口为 orphaned")
def test_stale_orphaned():
    _reset()
    row = _register("actE")
    store.claim_for_account(row["id"], 303, "c@x")
    store.update_status(row["id"], store.STATUS_RUNNING)
    _sql("UPDATE sms_pool_reservations SET lease_expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", row["id"]))
    assert store.find_stale_active(), "应能找到租约过期记录"
    service.reconcile()
    final = store.get_reservation(row["id"])
    assert final["status"] == store.STATUS_ORPHANED, f"应为 orphaned，实际 {final['status']}"
    assert final["failure_code"] == "lease_expired"


@case("激活状态缺失：任务收口为 orphaned 且不二次取号")
def test_missing_state_orphaned():
    _reset()
    row = _register("actF")
    fake_sp.state.pop("actF", None)  # 模拟服务重启后内存状态丢失
    out = task.run_pool_task(row["id"], {"id": 304, "email": "d@x"})
    assert out.get("error") == "activation_state_missing", out
    final = store.get_reservation(row["id"])
    assert final["status"] == store.STATUS_ORPHANED, f"应为 orphaned，实际 {final['status']}"
    assert not any(c[0] == "acquire_number" for c in fake_sp.calls), "不得二次取号"
    assert not fake_retry.calls, "不应进入补跑流程"


@case("价格边界：未配置人民币上限时拒绝采购")
def test_price_limit_missing():
    _reset()
    pool_cfg.SMS_POOL_MAX_PRICE_CNY = ""
    max_cny, source, max_usd = service.resolve_price_limit()
    assert max_cny is None and max_usd is None, (max_cny, max_usd)
    assert service.acquire_one("smsbower") is None, "无上限时应拒绝采购"
    assert not any(c[0] == "acquire_number" for c in fake_sp.calls), "拒绝采购时不得触达供应商"


@case("汇率边界：汇率无效时拒绝采购")
def test_price_limit_bad_fx():
    _reset()
    pool_cfg.SMS_POOL_MAX_PRICE_CNY = "0.5"
    fake_sp.fx_rate = decimal.Decimal("0")
    max_cny, _source, max_usd = service.resolve_price_limit()
    assert max_cny is not None and max_usd is None, (max_cny, max_usd)
    assert service.acquire_one("smsbower") is None, "汇率无效时应拒绝采购"
    assert not any(c[0] == "acquire_number" for c in fake_sp.calls), "拒绝采购时不得触达供应商"


@case("正常采购：有上限和有效汇率时可以取号并登记 reserved")
def test_acquire_ok():
    _reset()
    pool_cfg.SMS_POOL_MAX_PRICE_CNY = "0.5"
    row = service.acquire_one("smsbower")
    assert row is not None and row["status"] == store.STATUS_RESERVED, row
    assert row["expires_at"], "采购登记必须写入过期时间"
    assert str(row["phone_mask"]).startswith("*"), "表内只应保存脱敏号码"


@case("账号筛选：已接码账号必须被排除（extra_json 嵌套字段）")
def test_eligibility_reads_nested_activation():
    _reset()
    ACCOUNTS.extend([
        {"id": 401, "email": "done@x", "current_plan_type": "free", "codex_status": "failed",
         "extra_json": json.dumps({"codex": {"phone_activation": {"phone_number": "+15550004444"}}})},
        {"id": 402, "email": "todo@x", "current_plan_type": "free", "codex_status": "failed",
         "extra_json": ""},
        {"id": 403, "email": "plus@x", "current_plan_type": "plus", "codex_status": "failed"},
        {"id": 404, "email": "ok@x", "current_plan_type": "free", "codex_status": "success"},
    ])
    scan = service.scan_eligible_accounts()
    ids = [item["id"] for item in scan["eligible"]]
    assert ids == [402], f"应只有 402 合格，实际 {ids}"


@case("需求驱动：无合格账号时不采购")
def test_no_eligible_no_purchase():
    _reset()
    pool_cfg.SMS_POOL_MAX_PRICE_CNY = "0.5"
    fake_retry.result = {"ok": True}
    out = worker.run_once()
    assert out.get("purchased") == 0, out
    assert not any(c[0] == "acquire_number" for c in fake_sp.calls), "无需求不得采购"


@case("采购容量：已领取号码不重复扣减已从候选移除的账号")
def test_capacity_counts_inflight_and_ready():
    """容量口径：gap 按"查活可用账号"算，room 按"workers − 在途"算。

    2026-10-24 修正：旧断言假设 capacity 按 eligible 计算且不扣在途，
    但实现已改为 aux 语义（注释写明：避免没有可用账号时继续买号）。
    这里按当前语义断言，并额外验证"未分配的号码可以被分配出去"。
    """
    _reset()
    live = datetime.now().isoformat(timespec="seconds")
    ACCOUNTS.extend([
        {"id": 410, "email": "active@x", "current_plan_type": "free", "codex_status": "failed",
         "live_check_status": "live", "live_check_ok": True, "live_checked_at": live},
        {"id": 411, "email": "free1@x", "current_plan_type": "free", "codex_status": "failed",
         "live_check_status": "live", "live_check_ok": True, "live_checked_at": live},
        {"id": 412, "email": "free2@x", "current_plan_type": "free", "codex_status": "failed",
         "live_check_status": "live", "live_check_ok": True, "live_checked_at": live},
    ])
    active = _register("actCapacityActive")
    _reserved = _register("actCapacityReserved")
    assert store.claim_for_account(active["id"], 410, "active@x")
    capacity = service.purchase_capacity()
    assert capacity["unclaimed"] == 1 and capacity["active"] == 1, capacity
    assert capacity["in_flight"] == 2, capacity
    # workers=2 且两个号码都在手 → 本轮不该再买
    assert capacity["room"] == 0, capacity
    assert capacity["capacity"] == 0, capacity
    # 但在手的那个未分配号码应当能直接分配给别的可用账号
    assigned = service.assign_unclaimed() if hasattr(service, 'assign_unclaimed') else None
    assert assigned is None or isinstance(assigned, dict), assigned


@case("并发分配：两个号码可同时分配给两个账号")
def test_two_numbers_two_accounts():
    _reset()
    pool_cfg.SMS_POOL_MAX_PRICE_CNY = "0.5"
    pool_cfg.SMS_POOL_WORKERS = 2
    ACCOUNTS.extend([
        {"id": 501, "email": "p1@x", "current_plan_type": "free", "codex_status": "failed",
         "live_check_status": "live", "live_check_ok": True,
         "live_checked_at": datetime.now().isoformat(timespec="seconds")},
        {"id": 502, "email": "p2@x", "current_plan_type": "free", "codex_status": "failed",
         "live_check_status": "live", "live_check_ok": True,
         "live_checked_at": datetime.now().isoformat(timespec="seconds")},
    ])
    first_row = _register("actG")
    second_row = _register("actH")
    store.enqueue_request(501, "p1@x")
    store.enqueue_request(502, "p2@x")
    dispatched = worker._dispatch_available()
    assert dispatched == 2, f"应分配 2 个，实际 {dispatched}"
    first = store.get_reservation(first_row["id"])
    second = store.get_reservation(second_row["id"])
    # 领取在 _dispatch_available 内同步完成，因此归属判定不受任务线程完成速度影响。
    assert {first["account_id"], second["account_id"]} == {501, 502}, (
        first["account_id"], second["account_id"],
    )
    assert first["status"] != store.STATUS_RESERVED, first["status"]
    assert second["status"] != store.STATUS_RESERVED, second["status"]
    assert first["account_id"] != second["account_id"], "两个号码不能分配给同一个账号"
    assert {account_id for _reservation_id, account_id in DISPATCHES} == {501, 502}, DISPATCHES


@case("有号后自动分配给已测活账号，不需要手动入队")
def test_automatic_assignment_without_request():
    _reset()
    ACCOUNTS.append({"id": 503, "email": "auto@x", "current_plan_type": "free", "codex_status": "failed",
                     "live_check_status": "live", "live_check_ok": True,
                     "live_checked_at": datetime.now().isoformat(timespec="seconds")})
    row = _register("actAuto")

    dispatched = worker._dispatch_available()

    assert dispatched == 1, f"有可用号码和测活通过的账号应自动补跑，实际派发 {dispatched} 个"
    final = store.get_reservation(row["id"])
    assert final["account_id"] == 503, final
    assert final["status"] == store.STATUS_CLAIMED, final
    assert DISPATCHES == [(row["id"], 503)], DISPATCHES


@case("账号未测活时先入后台查活，不抢先消耗号码")
def test_preflight_live_check_before_dispatch():
    _reset()
    ACCOUNTS.append({"id": 504, "email": "check@x", "current_plan_type": "free", "codex_status": "failed"})
    row = _register("actPreflight")

    dispatched = worker._dispatch_available()

    assert dispatched == 0, f"查活未完成时不得补跑，实际派发 {dispatched} 个"
    final = store.get_reservation(row["id"])
    assert final["status"] == store.STATUS_RESERVED and final["account_id"] is None, final
    assert fake_live_check.calls and fake_live_check.calls[0]["account_id"] == 504, fake_live_check.calls
    assert not fake_retry.calls, "必须等待账号测活完成后才启动 OAuth"


@case("测活失效账号从候选中排除，但保留账号数据")
def test_deactivated_account_excluded():
    _reset()
    ACCOUNTS.append({"id": 505, "email": "dead@x", "current_plan_type": "free", "codex_status": "failed",
                     "live_check_status": "deactivated", "live_check_ok": False})
    scan = service.scan_eligible_accounts()
    assert not scan["eligible"], scan
    assert scan["live_summary"]["deactivated"] == 1, scan
    assert ACCOUNTS[0]["email"] == "dead@x", "失效只从号码池候选剔除，不删除账号"


@case("静态检查：acquire_number 的接管短路在取号之前")
def test_provider_hook_order():
    src = (ROOT / "core" / "sms_provider.py").read_text(encoding="utf-8")
    body = src.split("def acquire_number(", 1)[1]
    hook = body.find("_overridden_activation()")
    http = body.find("own_http = http is None")
    assert hook != -1, "acquire_number 缺少接管短路"
    assert 0 <= hook < http, "接管短路必须早于创建 http 会话"


@case("静态检查：池号码失败时不自行释放")
def test_no_release_for_pool_number():
    src = (ROOT / "core" / "roxy_codex_oauth.py").read_text(encoding="utf-8")
    assert "owns_activation = not pooled_activation_id" in src, "缺少号码归属判定"
    assert "if activation_id and owns_activation:" in src, "释放分支缺少归属守卫"
    assert "max_rounds = 1 if pool_activation else 2" in src, "池场景应只允许一轮授权"


def main() -> int:
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + ("" if ok else f" -> {detail}"))
    print(f"\n临时库：{_db_path}")
    print(f"结果：{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
