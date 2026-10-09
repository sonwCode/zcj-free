# -*- coding: utf-8 -*-
"""
SMSBower 客户端。

用于 Codex OAuth "全新 session" 流程过 OpenAI 的 /phone-verification 手机号验证：
    1. acquire_number()       按价格/库存取一个手机号（返回激活 ID + 号码）
    2. wait_for_sms_code()    轮询 getStatus 直到拿到短信验证码
    3. complete() / cancel()  setStatus 标记完成(6) / 取消(8)

价格相关：每取一个号、收到短信都会计费，所以：
    - 取号后若收不到短信，必须 cancel(8) 释放，避免白扣钱；
    - 成功拿到码后 complete(6) 正式完成激活。
"""
import json
import logging
import random
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from urllib.request import Request, urlopen

from curl_cffi.requests import Session as CurlSession

# 注意：用 `from config import codex` 而不是 `from config.codex import X`，
# 这样 WebUI 调 config.reload_all() 后，本模块通过 codex.X 读到的是最新值。
from config import codex as _cfg
from config import IMPERSONATE
from core.stop_control import sleep as _stop_sleep

logger = logging.getLogger(__name__)

_STATE_LOCK = threading.RLock()
_ACTIVATION_STATE: dict[str, dict] = {}
_CODE_HISTORY: dict[str, set[str]] = {}
_RECENT_REJECTED_NUMBERS: dict[str, float] = {}
_TIER_FAILURES: dict[tuple[str, str, str, str], int] = {}
_TIER_COOLDOWNS: dict[tuple[str, str, str, str], float] = {}
_METRICS: dict[str, int] = {}
_FX_RATE_CACHE: tuple[float, Decimal, str] | None = None


class SmsProviderError(RuntimeError):
    """接码平台通用错误。"""


class SmsProviderConfigurationError(SmsProviderError):
    """Provider 授权或地址配置错误，重复换号没有意义。"""


class SmsNumberRejectedError(SmsProviderError):
    """号码已被当前目标流程或本地质量策略排除。"""


class SmsCodeRejectedError(SmsProviderError):
    """目标拒绝了当前短信验证码。"""


class SmsNoNumbersError(SmsProviderError):
    """暂无可用号码（NO_NUMBERS），可换国家或稍后重试。"""


class SmsNoBalanceError(SmsProviderError):
    """余额不足（NO_BALANCE），必须充值，重试无意义——上层应立即停止。"""


class SmsCodeTimeout(SmsProviderError):
    """单个号等短信超时（OpenAI 没发或没到达）。"""


def _http() -> CurlSession:
    s = CurlSession(impersonate=IMPERSONATE)
    s.timeout = _cfg.SMS_REQUEST_TIMEOUT
    return s


_VALID_PROVIDERS = ("smsbower", "tiger")


def _provider_chain() -> list[str]:
    """解析有序 provider 链；没有链配置时兼容旧的 SMS_PROVIDER。"""
    raw_chain = str(getattr(_cfg, "SMS_PROVIDER_CHAIN", "") or "").strip()
    if raw_chain:
        raw_values = raw_chain.split(",")
    else:
        primary = str(getattr(_cfg, "SMS_PROVIDER", "smsbower") or "smsbower")
        raw_values = [primary] + [provider for provider in _VALID_PROVIDERS if provider != primary]
    result: list[str] = []
    for raw in raw_values:
        value = str(raw or "").strip().lower()
        if not value:
            continue
        if value not in _VALID_PROVIDERS:
            logger.warning("[SMS] 忽略不支持的短信渠道=%s", value[:40])
            continue
        if value not in result:
            result.append(value)
    return result or ["smsbower"]


def _configured_provider(provider: str) -> bool:
    """只把已配置 API 地址和密钥的平台纳入实际尝试链。"""
    provider = str(provider or "").strip().lower()
    if provider == "smsbower":
        key_name, base_name = "SMSBOWER_API_KEY", "SMSBOWER_API_BASE"
    elif provider == "tiger":
        key_name, base_name = "TIGER_SMS_API_KEY", "TIGER_SMS_API_BASE"
    else:
        return False
    return bool(
        str(getattr(_cfg, key_name, "") or "").strip()
        and str(getattr(_cfg, base_name, "") or "").strip()
    )


def _attempt_provider_chain() -> list[str]:
    chain = _provider_chain()
    configured = [provider for provider in chain if _configured_provider(provider)]
    if configured:
        skipped = [provider for provider in chain if provider not in configured]
        if skipped:
            logger.info("[SMS] 跳过未配置 provider=%s", ",".join(skipped))
        return configured
    # 保留原始链，让调用方返回明确的 provider 配置错误，而不是伪装成无库存。
    return chain


def _provider_error_allows_fallback(reason: object) -> bool:
    """只对库存耗尽或平台瞬时错误切换，配置/余额错误立即停止。"""
    if isinstance(reason, (SmsProviderConfigurationError, SmsNoBalanceError)):
        return False
    return isinstance(reason, SmsProviderError)


def _provider() -> str:
    """返回当前有序链的首选短信渠道。"""
    return _provider_chain()[0]


def _provider_for_activation(activation_id: str) -> str:
    state = _activation_state(activation_id)
    provider = str(state.get("provider") or "").strip().lower()
    return provider if provider in _VALID_PROVIDERS else _provider()


def _setting_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(float(getattr(_cfg, name, default) or default))
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)


def _csv_values(value: object) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _fx_rate_sources() -> list[str]:
    """返回按优先级排列的汇率源列表。

    SMS_FX_RATE_URL 非空时视为显式指定，单独使用；否则用 SMS_FX_RATE_URLS
    逗号分隔的级联列表。任一源成功即返回，避免单一第三方接口抖动就让价格
    换算失真。
    """
    explicit = str(getattr(_cfg, "SMS_FX_RATE_URL", "") or "").strip()
    if explicit:
        return [explicit]
    raw = str(getattr(_cfg, "SMS_FX_RATE_URLS", "") or "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_usd_rate_from_payload(payload: object) -> Decimal | None:
    """从各汇率服务的响应里取出 CNY -> USD 汇率。

    兼容三种常见结构：
      - {"rates": {"USD": 0.14}}           frankfurter / er-api
      - {"conversion_rates": {"USD": ...}} exchangerate-api
      - {"data": {"USD": ...}}             部分聚合服务
    以及 direct 形式的 {"USD": ...}。
    """
    if not isinstance(payload, dict):
        return None
    candidates: list[object] = []
    for key in ("rates", "conversion_rates", "data", "quotes"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value.get("USD"))
    candidates.append(payload.get("USD"))
    for value in candidates:
        if value is None:
            continue
        try:
            rate = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if rate.is_finite() and rate > 0:
            return rate
    return None


def _format_usd_cny_rate(cny_to_usd_rate: Decimal) -> str:
    """以稳定的小数格式记录 1 USD 对应的人民币汇率。"""
    value = Decimal("1") / Decimal(cny_to_usd_rate)
    formatted = format(value.quantize(Decimal("0.000001")), "f")
    return formatted.rstrip("0").rstrip(".") or "0"


def _fallback_usd_cny_rate() -> tuple[Decimal, str]:
    """网络不可用时的回退汇率。

    优先使用最近一次成功获取并写回配置的实时汇率，其次使用管理员显式配置的
    SMSBOWER_USD_CNY_RATE。回退值表示 1 USD = N CNY，与实时汇率同向，
    避免把价格边界钉死在一个陈旧常数上。
    """
    remembered = str(getattr(_cfg, "SMS_LAST_KNOWN_USD_CNY_RATE", "") or "").strip()
    configured = str(getattr(_cfg, "SMSBOWER_USD_CNY_RATE", "") or "").strip()
    for raw, source in (
        (remembered, "last known USD/CNY"),
        (configured, "configured USD/CNY"),
    ):
        if not raw:
            continue
        try:
            usd_cny = Decimal(raw)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if usd_cny.is_finite() and usd_cny > 0:
            return Decimal("1") / usd_cny, f"fallback {source}={raw}"
    raise SmsProviderConfigurationError(
        "实时汇率不可用，且本地回退汇率无效（检查 SMS_LAST_KNOWN_USD_CNY_RATE / "
        "SMSBOWER_USD_CNY_RATE 是否为正数）"
    )


def _remember_usd_cny_rate(usd_cny: Decimal) -> None:
    """把本次实时 USD/CNY 写入配置模块，供后续回退使用。"""
    try:
        _cfg.SMS_LAST_KNOWN_USD_CNY_RATE = format(usd_cny, "f")
    except Exception as exc:
        logger.debug("[SMS] 记录最近汇率失败（不影响本次取号）：%s", exc)


def _cny_to_usd_rate() -> tuple[Decimal, str]:
    """读取短期缓存的实时 CNY -> USD 汇率。

    按 SMS_FX_RATE_URLS 顺序级联尝试，全部失败时回退到最近一次已知汇率，
    而不是中断取号。实时汇率只是价格边界的换算依据，不该成为硬依赖。
    """
    global _FX_RATE_CACHE
    now = time.monotonic()
    ttl = _setting_int("SMS_FX_RATE_TTL", 900, 60)
    with _STATE_LOCK:
        cached = _FX_RATE_CACHE
        if cached and now - cached[0] < ttl:
            return cached[1], cached[2]

    timeout = min(max(_setting_int("SMS_REQUEST_TIMEOUT", 30, 1), 1), 10)
    errors: list[str] = []
    for url in _fx_rate_sources():
        try:
            request = Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "turb-gpt-register/1"},
            )
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            errors.append(f"{url} -> {type(exc).__name__}: {str(exc)[:120]}")
            continue
        rate = _parse_usd_rate_from_payload(payload)
        if rate is None:
            errors.append(f"{url} -> 响应缺少有效 USD 汇率")
            continue
        source = f"live CNY/USD ({url})"
        with _STATE_LOCK:
            _FX_RATE_CACHE = (now, rate, source)
        _remember_usd_cny_rate(Decimal("1") / rate)
        logger.info(
            "[SMS] 已刷新汇率：rate_usd_cny=%s fx_source=%s",
            _format_usd_cny_rate(rate), source,
        )
        return rate, source

    fallback_rate, fallback_source = _fallback_usd_cny_rate()
    logger.warning(
        "[SMS] 实时汇率源全部失败，使用回退汇率：rate_usd_cny=%s fx_source=%s errors=%s",
        _format_usd_cny_rate(fallback_rate),
        fallback_source,
        "；".join(errors[:3]) or "无可用来源",
    )
    return fallback_rate, fallback_source


def _mask_phone(phone: str) -> str:
    digits = _normalize_phone_digits(phone)
    if len(digits) <= 4:
        return digits or "-"
    return "*" * max(len(digits) - 4, 2) + digits[-4:]


def _phone_location_metadata(phone: str) -> dict:
    """按号码本身解析国家/地区；供应商 country 可能只是内部国家 ID。"""
    digits = _normalize_phone_digits(phone)
    if not digits:
        return {}
    try:
        import phonenumbers
        from phonenumbers import geocoder

        parsed = phonenumbers.parse(f"+{digits}", None)
        if not phonenumbers.is_possible_number(parsed):
            return {}
        region = str(phonenumbers.region_code_for_number(parsed) or "").strip().upper()
        if not region:
            return {}
        result = {
            "phone_region": region,
            "phone_dial_code": str(parsed.country_code),
        }
        country_name = str(geocoder.description_for_number(parsed, "en") or "").strip()
        if country_name:
            result["phone_country_name"] = country_name
        return result
    except Exception:
        return {}


def _price_limit_metadata(provider: str) -> dict:
    """把人民币配置、实时换算后的 USD 边界和来源写入激活快照。"""
    provider = str(provider or "").strip().lower()
    result = {}
    try:
        if provider not in {"smsbower", "tiger"}:
            return result
        min_usd, max_usd = _provider_price_bounds_usd(provider)
        if min_usd is not None:
            result["price_limit_min"] = format(min_usd, "f")
        if max_usd is not None:
            result["price_limit_max"] = format(max_usd, "f")
        if min_usd is None and max_usd is None:
            return result
        result["price_limit_currency"] = "USD"
        result["price_limit_source"] = "SMS_MAX_PRICE CNY converted with " + _cny_to_usd_rate()[1]
        _, configured_max = _configured_price_bounds_cny(provider)
        if configured_max is not None:
            result["price_limit_configured_max"] = format(configured_max, "f")
            result["price_limit_configured_currency"] = "CNY"
        result["price_limit_fx_rate"] = format(_cny_to_usd_rate()[0], "f")
        result["price_limit_fx_source"] = _cny_to_usd_rate()[1]
    except Exception as exc:
        logger.warning("[SMS] 记录价格边界失败：provider=%s error=%s", provider, exc)
    return result


def _update_activation_state(activation_id: str, updates: dict) -> None:
    with _STATE_LOCK:
        state = _ACTIVATION_STATE.get(str(activation_id))
        if state is not None:
            state.update({key: value for key, value in updates.items() if value not in (None, "")})


def _metric(event: str, state: dict | None = None) -> None:
    state = state or {}
    provider = str(state.get("provider") or _provider())
    service = str(state.get("service") or getattr(_cfg, "SMS_SERVICE", "") or "-")
    country = str(state.get("country") or getattr(_cfg, "SMS_COUNTRY", "") or "-")
    tier = str(state.get("provider_id") or state.get("tier") or "default")
    with _STATE_LOCK:
        _METRICS[event] = _METRICS.get(event, 0) + 1
    logger.info(
        "[SMS_METRIC] event=%s provider=%s service=%s country=%s tier=%s",
        event, provider, service, country, tier,
    )


def _prune_strategy_state() -> None:
    now = time.monotonic()
    with _STATE_LOCK:
        for phone, expires_at in list(_RECENT_REJECTED_NUMBERS.items()):
            if expires_at <= now:
                _RECENT_REJECTED_NUMBERS.pop(phone, None)
        for key, expires_at in list(_TIER_COOLDOWNS.items()):
            if expires_at <= now:
                _TIER_COOLDOWNS.pop(key, None)


def _tier_key(state: dict) -> tuple[str, str, str, str]:
    return (
        str(state.get("provider") or _provider()),
        str(state.get("service") or getattr(_cfg, "SMS_SERVICE", "") or ""),
        str(state.get("country") or getattr(_cfg, "SMS_COUNTRY", "") or ""),
        str(state.get("provider_id") or state.get("tier") or "default"),
    )


def _tier_cooldown_key(state: dict) -> tuple[str, str, str, str] | None:
    key = _tier_key(state)
    if key[0] in {"smsbower", "tiger"} and key[3].strip().lower() in ("", "default", "unknown"):
        return None
    return key


def _cooldown_provider_ids(service: str, country: str) -> set[str]:
    _prune_strategy_state()
    now = time.monotonic()
    with _STATE_LOCK:
        return {
            key[3]
            for key, expires_at in _TIER_COOLDOWNS.items()
            if key[0] in {"smsbower", "tiger"}
            and key[1] == str(service or "")
            and key[2] == str(country or "")
            and expires_at > now
            and key[3] not in ("", "default", "unknown")
        }


def _tier_is_cooled(state: dict) -> bool:
    _prune_strategy_state()
    key = _tier_cooldown_key(state)
    if key is None:
        return False
    with _STATE_LOCK:
        return _TIER_COOLDOWNS.get(key, 0) > time.monotonic()


def _remember_activation(activation_id: str, phone: str, metadata: dict | None = None) -> tuple[str, str]:
    metadata = dict(metadata or {})
    state = {
        "activation_id": str(activation_id),
        "phone_number": str(phone),
        "provider": str(metadata.pop("provider", _provider())),
        "service": str(metadata.pop("service", getattr(_cfg, "SMS_SERVICE", "") or "")),
        "country": str(metadata.pop("country", getattr(_cfg, "SMS_COUNTRY", "") or "")),
        **metadata,
    }
    for key, value in _phone_location_metadata(phone).items():
        state.setdefault(key, value)
    for key, value in _price_limit_metadata(state["provider"]).items():
        state.setdefault(key, value)
    with _STATE_LOCK:
        _ACTIVATION_STATE[str(activation_id)] = state
    _metric("acquired", state)
    price = " ".join(
        part for part in (str(state.get("price_amount") or ""), str(state.get("price_currency") or "")) if part
    ) or "unknown"
    limit = str(state.get("price_limit_max") or "unlimited")
    logger.info(
        "[SMS] 激活登记：id=%s provider=%s service=%s provider_country=%s region=%s "
        "phone=+%s price=%s price_limit_max=%s price_source=%s",
        activation_id, state["provider"], state["service"], state["country"] or "-",
        state.get("phone_country_name") or state.get("phone_region") or "unknown",
        _mask_phone(phone), price, limit, state.get("price_source") or "unknown",
    )
    return str(activation_id), str(phone)


def _activation_state(activation_id: str) -> dict:
    with _STATE_LOCK:
        return dict(_ACTIVATION_STATE.get(str(activation_id)) or {})


def get_activation_info(activation_id: str) -> dict:
    """返回当前激活的脱敏/可审计快照，供 Codex 日志和结果记录使用。"""
    return _activation_state(activation_id)


def _forget_activation(activation_id: str) -> None:
    activation_id = str(activation_id or "")
    with _STATE_LOCK:
        _ACTIVATION_STATE.pop(activation_id, None)
        _CODE_HISTORY.pop(activation_id, None)


def _remember_code(activation_id: str, code: str) -> bool:
    code = str(code or "").strip()
    if not code:
        return False
    activation_id = str(activation_id)
    with _STATE_LOCK:
        history = _CODE_HISTORY.setdefault(activation_id, set())
        live_state = _ACTIVATION_STATE.get(activation_id)
        if live_state is not None:
            live_state["code_received"] = True
        state = dict(live_state or {})
        if code in history:
            duplicate = True
        else:
            history.add(code)
            duplicate = False
    if duplicate:
        _metric("historical_code", state)
        logger.info("[SMS] 忽略激活 %s 的历史验证码", activation_id)
        return False
    _metric("code_received", state)
    return True


def _record_rejected_number(state: dict, reason: str) -> None:
    phone = _normalize_phone_digits(str(state.get("phone_number") or ""))
    if not phone:
        return
    ttl = _setting_int("SMS_NUMBER_REJECT_TTL", 1800, 60)
    with _STATE_LOCK:
        _RECENT_REJECTED_NUMBERS[phone] = time.monotonic() + ttl
    _metric("number_rejected", state)
    logger.info(
        "[SMS] 记录号码质量失败：phone=%s reason=%s ttl=%ss",
        _mask_phone(phone), reason[:120], ttl,
    )


def _record_tier_failure(state: dict, category: str) -> None:
    key = _tier_cooldown_key(state)
    if key is None:
        logger.info(
            "[SMS] 跳过 Provider/tier 冷却统计：provider=%s tier=default category=%s",
            state.get("provider") or _provider(), category,
        )
        return
    threshold = _setting_int("SMS_TIER_FAILURE_THRESHOLD", 2, 1)
    cooldown_seconds = _setting_int("SMS_TIER_COOLDOWN_SECONDS", 45 * 60, 60)
    with _STATE_LOCK:
        count = _TIER_FAILURES.get(key, 0) + 1
        _TIER_FAILURES[key] = count
        if count >= threshold:
            _TIER_COOLDOWNS[key] = time.monotonic() + cooldown_seconds
            metric_state = dict(state)
        else:
            metric_state = None
    if metric_state is not None:
        _metric("tier_cooldown", metric_state)
        logger.warning(
            "[SMS] Provider/tier 进入冷却：key=%s failures=%s seconds=%s category=%s",
            key, count, cooldown_seconds, category,
        )


def _record_success(activation_id: str) -> None:
    state = _activation_state(activation_id)
    if state:
        key = _tier_cooldown_key(state)
        if key is not None:
            with _STATE_LOCK:
                _TIER_FAILURES.pop(key, None)
                _TIER_COOLDOWNS.pop(key, None)
    _metric("success", state)


def _classify_failure(reason: object) -> str:
    if isinstance(reason, SmsProviderConfigurationError):
        return "provider_config"
    if isinstance(reason, SmsNoBalanceError):
        return "no_balance"
    if isinstance(reason, SmsNoNumbersError):
        return "no_inventory"
    if isinstance(reason, SmsCodeTimeout):
        return "code_timeout"
    if isinstance(reason, SmsCodeRejectedError):
        return "code_rejected"
    text = str(reason or "").strip().lower()
    if any(marker in text for marker in (
        "admin_auth_code", "api key", "api_key", "bad_key", "http 401", "http 403",
        "api_base 不能为空", "api key 不能为空",
    )):
        return "provider_config"
    if any(marker in text for marker in (
        "no_numbers", "no numbers", "暂无可用号码", "没有可用号码", "库存为空",
    )):
        return "no_inventory"
    if any(marker in text for marker in ("no_balance", "no_money", "余额不足", "insufficient balance")):
        return "no_balance"
    if any(marker in text for marker in (
        "invalid_phone_code", "invalid code", "incorrect code", "wrong code", "expired code",
        "验证码无效", "验证码错误", "验证码已过期",
    )):
        return "code_rejected"
    if any(marker in text for marker in (
        "invalid_phone", "not a valid phone", "phone number is not valid", "phone_in_use",
        "already used", "voip", "号码无效", "手机号无效", "手机号已被使用", "whatsapp_channel",
    )):
        return "number_rejected"
    if any(marker in text for marker in (
        "send_not_accepted", "delivery_refused", "cannot send", "could not send",
        "unable to send", "failed to send", "send failed", "send_limited", "限流", "发送失败",
    )):
        return "send_failed"
    if any(marker in text for marker in ("timeout", "timed out", "超时")):
        return "code_timeout"
    if "status_cancel" in text or "已被取消" in text:
        return "activation_cancelled"
    return "unknown"


def _report_failure_with_state(
    activation_id: str,
    reason: object = "",
    category: str | None = None,
    state: dict | None = None,
    release_result: dict | None = None,
) -> str:
    state = state if state is not None else _activation_state(activation_id)
    category = category or _classify_failure(reason)
    if category == "provider_config":
        _metric("provider_config_error", state)
    elif category == "no_balance":
        _metric("no_balance", state)
    elif category == "no_inventory":
        _metric("no_inventory", state)
    elif category in ("number_rejected", "send_failed", "code_rejected", "code_timeout"):
        if state and category in ("number_rejected", "send_failed", "code_rejected"):
            _record_rejected_number(state, str(reason or category))
        if state:
            _record_tier_failure(state, category)
        _metric(category, state)
    else:
        _metric(category, state)
    price = " ".join(
        part for part in (str(state.get("price_amount") or ""), str(state.get("price_currency") or "")) if part
    ) or "unknown"
    logger.info(
        "[SMS] 记录激活反馈：id=%s category=%s phone=+%s region=%s provider_country=%s "
        "price=%s price_limit_max=%s release=%s reason=%s",
        activation_id, category, _mask_phone(str(state.get("phone_number") or "")),
        state.get("phone_country_name") or state.get("phone_region") or "unknown",
        state.get("country") or "unknown", price, state.get("price_limit_max") or "unlimited",
        str((release_result or {}).get("release") or "unknown"),
        str(reason or "")[:180],
    )
    return category


def report_failure(activation_id: str, reason: object = "", category: str | None = None) -> str:
    """接收注册层反馈，只更新短信调度状态，不改变页面流程。"""
    return _report_failure_with_state(activation_id, reason=reason, category=category)


def cancel_and_report_failure(
    activation_id: str,
    http: CurlSession | None = None,
    reason: object = "",
    category: str | None = None,
) -> str:
    """先同步关闭平台激活，再用关闭前快照记录失败状态。"""
    state = _activation_state(activation_id)
    release_result = cancel(activation_id, http=http)
    return _report_failure_with_state(
        activation_id,
        reason=reason,
        category=category,
        state=state,
        release_result=release_result,
    )


def report_success(activation_id: str) -> None:
    """记录成功反馈，清除该 Provider/tier 的连续失败计数。"""
    _record_success(activation_id)


def get_sms_runtime_metrics() -> dict:
    """返回脱敏的进程级短信指标，供健康检查和单测使用。"""
    _prune_strategy_state()
    with _STATE_LOCK:
        return {
            "events": dict(_METRICS),
            "active_activations": len(_ACTIVATION_STATE),
            "rejected_numbers": len(_RECENT_REJECTED_NUMBERS),
            "cooled_tiers": len(_TIER_COOLDOWNS),
        }


def _reset_runtime_state_for_tests() -> None:
    global _FX_RATE_CACHE
    with _STATE_LOCK:
        _ACTIVATION_STATE.clear()
        _CODE_HISTORY.clear()
        _RECENT_REJECTED_NUMBERS.clear()
        _TIER_FAILURES.clear()
        _TIER_COOLDOWNS.clear()
        _METRICS.clear()
        _FX_RATE_CACHE = None


def _request_smsbower(http: CurlSession, params: dict) -> str:
    """发 SMSBower handler_api 请求，保留历史错误映射。"""
    api_key = str(getattr(_cfg, "SMSBOWER_API_KEY", "") or "").strip()
    if not api_key:
        raise SmsProviderConfigurationError("SMSBower API Key 不能为空")
    base = str(getattr(_cfg, "SMSBOWER_API_BASE", "") or "").strip()
    if not base:
        raise SmsProviderConfigurationError("SMSBOWER_API_BASE 不能为空")
    resp = http.get(base, params={"api_key": api_key, **params})
    text = (resp.text or "").strip()
    if resp.status_code in (401, 403) or text == "BAD_KEY":
        raise SmsProviderConfigurationError("SMSBower API key 无效（BAD_KEY）")
    if resp.status_code != 200:
        raise SmsProviderError(f"SMSBower HTTP {resp.status_code}: {text[:200]}")
    if text in ("BAD_ACTION", "BAD_SERVICE", "WRONG_SERVICE", "BAD_STATUS", "NO_ACTIVATION"):
        if text in ("BAD_SERVICE", "WRONG_SERVICE"):
            raise SmsProviderConfigurationError(f"SMSBower 服务代码无效（{text}），OpenAI/ChatGPT 请填写 dr")
        if text == "NO_ACTIVATION":
            raise SmsProviderError("SMSBower 激活 ID 不存在（NO_ACTIVATION）")
        raise SmsProviderError(f"SMSBower 请求参数错误：{text}")
    if text in ("NO_NUMBERS", "NO_BALANCE", "NO_MONEY"):
        if text in ("NO_BALANCE", "NO_MONEY"):
            raise SmsNoBalanceError(f"SMSBower 余额不足（{text}），请充值")
        raise SmsNoNumbersError("SMSBower 暂无可用号码（NO_NUMBERS）")
    if text.startswith("The service is prohibited"):
        raise SmsProviderError(f"SMSBower 该服务被禁售：{text}")
    return text


def _tiger_error_code(text: str) -> str:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return str(text or "").split(":", 1)[0].strip().upper()
    if isinstance(payload, dict):
        return str(payload.get("title") or payload.get("error") or "").strip().upper()
    return ""


def _request_tiger(http: CurlSession, params: dict) -> str:
    """发 Tiger SMS handler_api 请求；Tiger key 永远只放在请求参数中。"""
    api_key = str(getattr(_cfg, "TIGER_SMS_API_KEY", "") or "").strip()
    if not api_key:
        raise SmsProviderConfigurationError("Tiger SMS API Key 不能为空")
    base = str(getattr(_cfg, "TIGER_SMS_API_BASE", "") or "").strip()
    if not base:
        raise SmsProviderConfigurationError("TIGER_SMS_API_BASE 不能为空")
    resp = http.get(base, params={"api_key": api_key, **params})
    text = (resp.text or "").strip()
    if resp.status_code in (401, 403) or text == "BAD_KEY":
        raise SmsProviderConfigurationError("Tiger SMS API key 无效（BAD_KEY）")
    if resp.status_code == 402 or _tiger_error_code(text) == "NO_BALANCE":
        raise SmsNoBalanceError("Tiger SMS 余额不足（NO_BALANCE），请充值")
    if resp.status_code != 200 and resp.status_code != 400:
        raise SmsProviderError(f"Tiger SMS HTTP {resp.status_code}: {text[:200]}")
    code = _tiger_error_code(text)
    if code in {"NO_NUMBERS", "NO_PROVIDERS", "WRONG_MAX_PRICE"}:
        raise SmsNoNumbersError(f"Tiger SMS 当前价格/库存没有可用号码（{code}）")
    if code in {"BAD_SERVICE", "BAD_COUNTRY", "BAD_VALUES", "BAD_MAX_PRICE", "BAD_MULTIPLE", "BAD_PROVIDER_IDS", "BAD_EXCEPT_PROVIDER_IDS", "BAD_ACTIVATION_TYPE", "BAD_FIXED_PRICE", "UNPROCESSABLE_ENTITY"}:
        raise SmsProviderConfigurationError(f"Tiger SMS 请求参数错误（{code}）")
    if code in {"BAD_ACTION", "BAD_STATUS", "NO_ACTIVATION"}:
        raise SmsProviderError(f"Tiger SMS 请求失败（{code}）")
    if text in {"NO_NUMBERS", "NO_PROVIDERS"}:
        raise SmsNoNumbersError(f"Tiger SMS 当前没有可用号码（{text}）")
    return text


def _smsbower_price_bound(name: str) -> Decimal | None:
    value = str(getattr(_cfg, name, "") or "").strip()
    if name == "SMS_MAX_PRICE":
        value = str(getattr(_cfg, "SMS_MAX_PRICE", "") or "").strip()
    if not value:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SmsProviderConfigurationError(f"{name} 必须是有效价格：{value!r}") from exc
    if not parsed.is_finite():
        raise SmsProviderConfigurationError(f"{name} 必须是有限价格：{value!r}")
    if parsed < 0:
        raise SmsProviderConfigurationError(f"{name} 不能小于 0：{value!r}")
    return parsed


def _configured_price_bounds_cny(provider: str) -> tuple[Decimal | None, Decimal | None]:
    provider = str(provider or "").strip().lower()
    if provider == "smsbower":
        return _smsbower_price_bound("SMSBOWER_MIN_PRICE"), _smsbower_price_bound("SMS_MAX_PRICE")
    if provider == "tiger":
        return None, _smsbower_price_bound("SMS_MAX_PRICE")
    return None, None


def _provider_price_bounds_usd(
    provider: str,
    *,
    cny_to_usd_rate: Decimal | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    min_cny, max_cny = _configured_price_bounds_cny(provider)
    if min_cny is None and max_cny is None:
        return None, None
    if min_cny is not None and max_cny is not None and min_cny > max_cny:
        raise SmsProviderConfigurationError("短信价格最低值不能大于通用人民币最高值")
    if cny_to_usd_rate is None:
        cny_to_usd_rate, _ = _cny_to_usd_rate()
    return (
        min_cny * cny_to_usd_rate if min_cny is not None else None,
        max_cny * cny_to_usd_rate if max_cny is not None else None,
    )


def _smsbower_price_bounds_usd() -> tuple[Decimal | None, Decimal | None]:
    return _provider_price_bounds_usd("smsbower")


def _tiger_price_bounds_usd() -> tuple[Decimal | None, Decimal | None]:
    return _provider_price_bounds_usd("tiger")


def _smsbower_api_price(value: Decimal, rounding: str) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=rounding), "f")


def _sms_service_code(service: str | None = None) -> str:
    service_code = str(service or getattr(_cfg, "SMS_SERVICE", "") or "").strip()
    if service_code.lower() in ("openai", "chatgpt"):
        service_code = "dr"
    if not service_code:
        raise SmsProviderConfigurationError("SMS_SERVICE 不能为空")
    return service_code


def _smsbower_price_rows(http: CurlSession, service: str) -> list[dict]:
    """读取 SMSBower 价格/库存表；该接口只查询库存，不申请号码。"""
    try:
        payload = json.loads(_request_smsbower(http, {"action": "getPrices", "service": service}))
    except json.JSONDecodeError as exc:
        raise SmsProviderError("SMSBower getPrices 响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise SmsProviderError("SMSBower getPrices 响应不是 JSON 对象")

    rows: list[dict] = []
    for country_id, services in payload.items():
        if not isinstance(services, dict):
            continue
        row = services.get(service)
        if not isinstance(row, dict):
            continue
        try:
            cost = Decimal(str(row.get("cost")))
            count = int(row.get("count") or 0)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if count > 0 and cost.is_finite() and cost >= 0:
            rows.append({"country": str(country_id), "cost": cost, "count": count})
    return rows


def _activation_price_metadata(
    data: dict | None,
    provider: str,
    price_quote: dict | None = None,
) -> dict:
    sources = []
    if isinstance(data, dict):
        sources.append(data)
        for key in ("item", "data"):
            nested = data.get(key)
            if isinstance(nested, dict):
                sources.append({**data, **nested})

    def currency_code(value: object) -> str | None:
        text = str(value or "").strip().upper()
        if text in ("USD", "US DOLLAR", "$", "840"):
            return "USD"
        if text in ("CNY", "RMB", "CNH", "¥", "￥"):
            return "CNY"
        return None

    def make_price(value: object, currency: object, source: str) -> dict | None:
        raw = str(value or "").strip()
        detected = currency_code(currency)
        if raw.startswith(("$", "¥", "￥")):
            detected = detected or currency_code(raw[0])
            raw = raw[1:].strip()
        for suffix in ("USD", "CNY", "RMB"):
            if raw.upper().endswith(suffix):
                detected = detected or currency_code(suffix)
                raw = raw[:-len(suffix)].strip()
                break
        if not detected:
            return None
        try:
            amount = Decimal(raw)
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not amount.is_finite() or amount < 0:
            return None
        return {
            "price_amount": format(amount, "f"),
            "price_currency": detected,
            "price_source": source,
        }

    for source in sources:
        for key, currency in (("price_usd", "USD"), ("cost_usd", "USD"), ("activationCost", "USD"), ("activation_cost", "USD"), ("price_cny", "CNY"), ("cost_cny", "CNY")):
            value = source.get(key)
            if value is not None:
                result = make_price(value, currency, f"{provider.upper()} 激活响应")
                if result:
                    return result
        currency = next((source.get(key) for key in ("currency", "price_currency", "priceCurrency") if source.get(key)), None)
        if currency is None and provider in {"smsbower", "tiger"}:
            currency = "USD"
        for key in ("price", "cost", "activationCost", "activation_cost"):
            value = source.get(key)
            if value is not None:
                result = make_price(value, currency, f"{provider.upper()} 激活响应")
                if result:
                    return result

    if provider in {"smsbower", "tiger"} and isinstance(price_quote, dict):
        label = "SMSBower getPrices 报价" if provider == "smsbower" else "Tiger SMS getPrices 报价"
        result = make_price(price_quote.get("cost"), "USD", label)
        if result:
            return result
    return {}


def _validate_activation_price(activation_id: str) -> dict:
    """硬校验取号价格，防止供应商忽略 maxPrice 后继续使用超价号码。"""
    state = _activation_state(activation_id)
    provider = str(state.get("provider") or _provider()).strip().lower()
    raw_amount = str(state.get("price_amount") or "").strip()
    currency = str(state.get("price_currency") or "").strip().upper()
    amount = None
    if raw_amount:
        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, TypeError, ValueError):
            amount = None

    min_limit = max_limit = None
    comparable_amount = amount
    if provider == "smsbower":
        min_limit, max_limit = _smsbower_price_bounds_usd()
        if currency == "CNY" and amount is not None:
            comparable_amount = amount * _cny_to_usd_rate()[0]
        elif currency not in ("", "USD"):
            comparable_amount = None
    elif provider == "tiger":
        min_limit, max_limit = _tiger_price_bounds_usd()
        if currency not in ("", "USD"):
            comparable_amount = None

    if min_limit is None and max_limit is None:
        _update_activation_state(activation_id, {"price_validated": True})
        return state
    if comparable_amount is None or not comparable_amount.is_finite():
        if provider not in {"smsbower", "tiger"}:
            logger.warning(
                "[SMS] provider=%s 取号响应未返回价格，无法做本地价格硬校验；保留供应商请求限制",
                provider,
            )
            _update_activation_state(activation_id, {
                "price_validated": False,
                "price_validation_reason": "provider_price_unavailable",
            })
            return _activation_state(activation_id)
        raise SmsNumberRejectedError(
            f"{provider} 取号响应缺少可核验价格，已拒绝号码以遵守价格上限"
        )
    if min_limit is not None and comparable_amount < min_limit:
        raise SmsNumberRejectedError(
            f"{provider} 号码价格低于最低范围：actual={comparable_amount} min={min_limit}"
        )
    if max_limit is not None and comparable_amount > max_limit:
        raise SmsNumberRejectedError(
            f"{provider} 号码价格超过最高范围：actual={comparable_amount} max={max_limit}"
        )
    _update_activation_state(activation_id, {
        "price_validated": True,
        "price_checked_amount": format(comparable_amount, "f"),
        "price_checked_currency": "USD" if provider in {"smsbower", "tiger"} else currency or "provider",
    })
    return _activation_state(activation_id)


def _smsbower_random_country_candidates(http: CurlSession, service: str) -> list[dict]:
    """返回有库存且价格在配置区间内的国家，并随机打乱顺序。"""
    if not bool(getattr(_cfg, "SMSBOWER_RANDOM_COUNTRY", True)):
        return []
    min_price_cny = _smsbower_price_bound("SMSBOWER_MIN_PRICE")
    max_price_cny = _smsbower_price_bound("SMS_MAX_PRICE")
    cny_to_usd_rate, fx_source = _cny_to_usd_rate()
    min_price_usd, max_price_usd = _provider_price_bounds_usd(
        "smsbower", cny_to_usd_rate=cny_to_usd_rate
    )
    rows = _smsbower_price_rows(http, service)
    stocked = [row for row in rows if row.get("count", 0) > 0]
    candidates = [
        row for row in stocked
        if (max_price_usd is None or row["cost"] <= max_price_usd)
        and (min_price_usd is None or row["cost"] >= min_price_usd)
    ]

    random.shuffle(candidates)
    logger.info(
        "[SMSBower] 随机国家候选：service=%s count=%s price_usd=%s..%s price_cny=%s..%s rate_usd_cny=%s fx_source=%s",
        service, len(candidates), min_price_usd if min_price_usd is not None else 0,
        max_price_usd if max_price_usd is not None else "unbounded",
        min_price_cny if min_price_cny is not None else 0,
        max_price_cny if max_price_cny is not None else "unbounded",
        _format_usd_cny_rate(cny_to_usd_rate), fx_source,
    )
    if not candidates:
        if stocked:
            cheapest = min(stocked, key=lambda row: row["cost"])
            detail = (
                "；当前在库最低价 USD {}，国家={}，库存={}"
                .format(cheapest["cost"], cheapest["country"], cheapest.get("count", 0))
            )
        else:
            detail = "；getPrices 当前没有在库国家"
        if max_price_cny is None:
            raise SmsNoNumbersError(f"SMSBower 当前价格/库存范围内没有可用国家{detail}")
        raise SmsNoNumbersError(
            f"SMSBower 在人民币最高价格 ¥{max_price_cny}（约 USD {max_price_usd:.6f}）内没有可用国家{detail}"
        )
    return candidates


def _tiger_price_rows(http: CurlSession, service: str, country: str | None = None) -> list[dict]:
    """读取 Tiger getPrices 的 country -> service -> cost/count 表。"""
    params = {"action": "getPrices", "service": service}
    if country is not None and str(country).strip():
        params["country"] = str(country).strip()
    try:
        payload = json.loads(_request_tiger(http, params))
    except json.JSONDecodeError as exc:
        raise SmsProviderError("Tiger SMS getPrices 响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise SmsProviderError("Tiger SMS getPrices 响应不是 JSON 对象")
    rows: list[dict] = []
    for country_id, services in payload.items():
        if not isinstance(services, dict):
            continue
        row = services.get(service)
        if not isinstance(row, dict):
            continue
        try:
            cost = Decimal(str(row.get("cost") if row.get("cost") is not None else row.get("price")))
            count = int(row.get("count") or 0)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if count > 0 and cost.is_finite() and cost >= 0:
            rows.append({"country": str(country_id), "cost": cost, "count": count})
    return rows


def _tiger_random_country_candidates(http: CurlSession, service: str) -> list[dict]:
    if not bool(getattr(_cfg, "TIGER_SMS_RANDOM_COUNTRY", True)):
        return []
    min_price_usd, max_price_usd = _tiger_price_bounds_usd()
    if max_price_usd is None:
        return []
    candidates = [
        row for row in _tiger_price_rows(http, service)
        if row["cost"] <= max_price_usd and (min_price_usd is None or row["cost"] >= min_price_usd)
    ]
    random.shuffle(candidates)
    if not candidates:
        raise SmsNoNumbersError("Tiger SMS 当前人民币最高价格范围内没有可用国家")
    return candidates


def _acquire_tiger_number(
    http: CurlSession,
    service: str | None = None,
    country: str | None = None,
    provider: str = "tiger",
) -> tuple[str, str]:
    service_code = _sms_service_code(service)
    attempts = _setting_int("SMS_NUMBER_ACQUIRE_RETRIES", 3, 1)
    candidates: list[dict] = []
    fixed_quote = None
    if country is None:
        candidates = _tiger_random_country_candidates(http, service_code)
        if candidates:
            attempts = min(max(1, _setting_int("TIGER_SMS_RANDOM_COUNTRY_ATTEMPTS", 12, 1)), len(candidates))
    if not candidates:
        configured_country = str(country or getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
        if not configured_country:
            raise SmsProviderConfigurationError("Tiger SMS 需要 SMS_COUNTRY，或开启价格范围内的随机国家")
        try:
            fixed_quote = next(
                (row for row in _tiger_price_rows(http, service_code, configured_country)
                 if row["country"] == configured_country),
                None,
            )
        except SmsNoNumbersError:
            raise
        except Exception as exc:
            logger.warning("[Tiger SMS] 获取价格快照失败，不影响取号：%s", exc)
    try:
        for attempt in range(1, attempts + 1):
            candidate = candidates[attempt - 1] if candidates else None
            selected_country = candidate["country"] if candidate else str(country or getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
            try:
                activation_id, phone = _acquire_number_once(
                    http, service=service_code, country=selected_country,
                    price_quote=candidate or fixed_quote, provider=provider,
                )
            except SmsNoNumbersError:
                if candidates and attempt < attempts:
                    continue
                raise
            try:
                state = _validate_activation_price(activation_id)
            except SmsNumberRejectedError as exc:
                try:
                    cancel_and_report_failure(activation_id, http=http, reason=exc)
                except Exception as release_exc:
                    logger.warning("[Tiger SMS] 超价号码释放失败，继续换号：id=%s error=%s", activation_id, release_exc)
                continue
            if _number_is_recently_rejected(phone) or _tier_is_cooled(state):
                _release_strategy_rejected(activation_id, http)
                continue
            return activation_id, phone
        raise SmsNumberRejectedError(f"Tiger SMS 连续 {attempts} 次取号候选均被策略排除")
    finally:
        pass


def _preflight_tiger(http: CurlSession, service: str, country: str | None) -> dict:
    rows = _tiger_price_rows(http, service, country if country else None)
    min_price_usd, max_price_usd = _tiger_price_bounds_usd()
    candidates = [
        row for row in rows
        if (max_price_usd is None or row["cost"] <= max_price_usd)
        and (min_price_usd is None or row["cost"] >= min_price_usd)
    ]
    if country is None and bool(getattr(_cfg, "TIGER_SMS_RANDOM_COUNTRY", True)) and max_price_usd is not None:
        if not candidates:
            raise SmsNoNumbersError("Tiger SMS 当前人民币价格范围内没有可用国家")
        return {
            "provider": "tiger", "service": service, "country": None,
            "probe": "getPrices", "available": True,
            "candidate_count": len(candidates), "countries": [row["country"] for row in candidates],
        }
    target = str(country if country is not None else getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
    if not target:
        raise SmsProviderConfigurationError("SMS_COUNTRY 不能为空")
    if not candidates:
        raise SmsNoNumbersError(f"Tiger SMS 国家 {target} 暂无符合价格/库存条件的号码")
    return {
        "provider": "tiger", "service": service, "country": target,
        "probe": "getPrices", "available": True,
        "candidate_count": len(candidates), "countries": [target],
    }


def _preflight_sms_dependency_for_provider(
    provider: str,
    http: CurlSession,
    service: str | None = None,
    country: str | None = None,
) -> dict:
    """只查询指定平台价格/库存，不申请消费型号码。"""
    service_code = _sms_service_code(service)
    country_code = str(country if country is not None else getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
    provider = str(provider or "").strip().lower()
    if provider == "tiger":
        return _preflight_tiger(http, service_code, country)
    if provider != "smsbower":
        raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")
    rows = _smsbower_price_rows(http, service_code)
    min_price_usd, max_price_usd = _smsbower_price_bounds_usd()
    if bool(getattr(_cfg, "SMSBOWER_RANDOM_COUNTRY", True)) and country is None:
        candidates = [
            row for row in rows
            if (max_price_usd is None or row["cost"] <= max_price_usd)
            and (min_price_usd is None or row["cost"] >= min_price_usd)
        ]
        if not candidates:
            raise SmsNoNumbersError("SMSBower 当前价格范围内没有可用国家")
        return {
            "provider": "smsbower",
            "service": service_code,
            "country": None,
            "probe": "getPrices",
            "available": True,
            "candidate_count": len(candidates),
            "countries": [row["country"] for row in candidates],
        }
    if not country_code:
        raise SmsProviderConfigurationError("SMS_COUNTRY 不能为空")
    target = country_code.lstrip("+")
    candidates = [
        row for row in rows
        if row["country"].lstrip("+") == target
        and (max_price_usd is None or row["cost"] <= max_price_usd)
        and (min_price_usd is None or row["cost"] >= min_price_usd)
    ]
    if not candidates:
        raise SmsNoNumbersError(f"SMSBower 国家 {country_code} 暂无符合价格/库存条件的号码")
    return {
        "provider": "smsbower",
        "service": service_code,
        "country": country_code,
        "probe": "getPrices",
        "available": True,
        "candidate_count": len(candidates),
        "countries": [country_code],
    }


def preflight_sms_dependency(
    http: CurlSession | None = None,
    service: str | None = None,
    country: str | None = None,
) -> dict:
    """按 provider 链探测价格/库存，不申请消费型号码。"""
    own_http = http is None
    http = http or _http()
    try:
        providers = _attempt_provider_chain()
        last_error: SmsProviderError | None = None
        for index, provider in enumerate(providers):
            try:
                result = _preflight_sms_dependency_for_provider(provider, http, service=service, country=country)
                logger.info("[SMS] provider 预检可用：provider=%s", provider)
                return result
            except SmsProviderError as exc:
                last_error = exc
                if index + 1 < len(providers) and _provider_error_allows_fallback(exc):
                    logger.warning("[SMS] provider=%s 预检失败，切换下一个 provider：%s", provider, str(exc)[:180])
                    continue
                raise
        raise last_error or SmsProviderConfigurationError("没有可用的短信平台配置")
    finally:
        if own_http:
            http.close()


def classify_sms_exception(reason: object) -> dict:
    """把短信依赖异常转换为 Codex/注册任务可持久化的状态。"""
    if isinstance(reason, SmsNoBalanceError):
        status, error_code, retryable = "blocked", "sms_no_balance", False
    elif isinstance(reason, SmsProviderConfigurationError):
        status, error_code, retryable = "blocked", "sms_provider_configuration", False
    elif isinstance(reason, SmsNoNumbersError):
        status, error_code, retryable = "failed", "sms_no_numbers", True
    elif isinstance(reason, SmsCodeTimeout):
        status, error_code, retryable = "failed", "sms_code_timeout", True
    elif isinstance(reason, SmsNumberRejectedError):
        status, error_code, retryable = "failed", "sms_number_rejected", True
    elif isinstance(reason, SmsCodeRejectedError):
        status, error_code, retryable = "failed", "sms_code_rejected", True
    elif isinstance(reason, SmsProviderError):
        status, error_code, retryable = "failed", "sms_provider_error", True
    else:
        status, error_code, retryable = "failed", "sms_error", True
    return {
        "status": status,
        "error_code": error_code,
        "retryable": retryable,
        "message": str(reason or ""),
    }


def _smsbower_number_params(service: str | None, country: str | None) -> dict:
    service_code = _sms_service_code(service)
    params = {
        "action": "getNumberV2" if bool(getattr(_cfg, "SMSBOWER_USE_V2", True)) else "getNumber",
        "service": service_code,
        "country": str(country or _cfg.SMS_COUNTRY or "").strip(),
    }
    min_price_usd, max_price_usd = _smsbower_price_bounds_usd()
    for key, value in (
        ("maxPrice", _smsbower_api_price(max_price_usd, ROUND_DOWN) if max_price_usd is not None else ""),
        ("minPrice", _smsbower_api_price(min_price_usd, ROUND_UP) if min_price_usd is not None else ""),
        ("providerIds", getattr(_cfg, "SMSBOWER_PROVIDER_IDS", "")),
        ("exceptProviderIds", getattr(_cfg, "SMSBOWER_EXCEPT_PROVIDER_IDS", "")),
        ("phoneException", getattr(_cfg, "SMSBOWER_PHONE_EXCEPTION", "")),
    ):
        value = str(value or "").strip()
        if value:
            params[key] = value
    dynamic_excluded = _cooldown_provider_ids(service_code, params.get("country", ""))
    if dynamic_excluded:
        params["exceptProviderIds"] = ",".join(sorted(_csv_values(params.get("exceptProviderIds")) | dynamic_excluded))
    return params


def _tiger_number_params(service: str | None, country: str | None) -> dict:
    service_code = _sms_service_code(service)
    params = {
        "action": "getNumberV2" if bool(getattr(_cfg, "TIGER_SMS_USE_V2", True)) else "getNumber",
        "service": service_code,
        "country": str(country or getattr(_cfg, "SMS_COUNTRY", "") or "").strip(),
        "activationType": "SMS",
    }
    _, max_price_usd = _tiger_price_bounds_usd()
    for key, value in (
        ("maxPrice", _smsbower_api_price(max_price_usd, ROUND_DOWN) if max_price_usd is not None else ""),
        ("providerIds", getattr(_cfg, "TIGER_SMS_PROVIDER_IDS", "")),
        ("exceptProviderIds", getattr(_cfg, "TIGER_SMS_EXCEPT_PROVIDER_IDS", "")),
    ):
        value = str(value or "").strip()
        if value:
            params[key] = value
    dynamic_excluded = _cooldown_provider_ids(service_code, params.get("country", ""))
    if dynamic_excluded:
        params["exceptProviderIds"] = ",".join(sorted(_csv_values(params.get("exceptProviderIds")) | dynamic_excluded))
    return params


def _request_tiger_number(http: CurlSession, params: dict) -> tuple[dict, str]:
    candidates = [dict(params)]
    if params.get("action") == "getNumberV2":
        candidates.append({**params, "action": "getNumber"})
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    for index, candidate in enumerate(unique):
        try:
            return candidate, _request_tiger(http, candidate)
        except SmsNoNumbersError:
            raise
        except SmsProviderError as exc:
            if candidate.get("action") == "getNumberV2" and "BAD_ACTION" in str(exc) and index + 1 < len(unique):
                continue
            raise
    raise SmsProviderError("Tiger SMS 没有可用的取号请求方案")


def _request_smsbower_number(http: CurlSession, params: dict) -> tuple[dict, str]:
    """按兼容性顺序取号，筛选无库存时放宽供应商条件。"""
    candidates: list[dict] = [dict(params)]
    if params.get("action") == "getNumberV2":
        candidates.append({**params, "action": "getNumber"})
    if params.get("providerIds"):
        without_provider = {key: value for key, value in params.items() if key != "providerIds"}
        candidates.append(without_provider)
        if params.get("action") == "getNumberV2":
            candidates.append({**without_provider, "action": "getNumber"})
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    for index, candidate in enumerate(unique):
        try:
            return candidate, _request_smsbower(http, candidate)
        except SmsNoNumbersError:
            if index + 1 < len(unique):
                logger.warning("[SMSBower] 取号筛选无库存，放宽条件重试：action=%s providerIds=%s", candidate.get("action"), candidate.get("providerIds", "-"))
                continue
            raise
        except SmsProviderError as exc:
            if candidate.get("action") == "getNumberV2" and "BAD_ACTION" in str(exc) and index + 1 < len(unique):
                logger.warning("[SMSBower] getNumberV2 不被当前接口支持，回退兼容接口")
                continue
            raise
    raise SmsProviderError("SMSBower 没有可用的取号请求方案")


def _normalize_phone_digits(value: str) -> str:
    """把平台返回的号码规范化为纯数字，避免非法 E.164。"""
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit())


def _number_is_recently_rejected(phone: str) -> bool:
    normalized = _normalize_phone_digits(phone)
    if not normalized:
        return False
    _prune_strategy_state()
    with _STATE_LOCK:
        return _RECENT_REJECTED_NUMBERS.get(normalized, 0) > time.monotonic()


def _release_strategy_rejected(activation_id: str, http: CurlSession) -> None:
    try:
        cancel(activation_id, http=http)
    except Exception as exc:
        logger.warning("[SMSBower] 策略排除号码释放请求失败：id=%s error=%s", activation_id, exc)
    finally:
        _forget_activation(activation_id)


# ============================================================
# 取号
# ============================================================

def _acquire_number_once(
    http: CurlSession | None = None,
    service: str | None = None,
    country: str | None = None,
    price_quote: dict | None = None,
    provider: str | None = None,
) -> tuple[str, str]:
    """向指定短信平台申请一个号码并登记激活快照。"""
    own_http = http is None
    http = http or _http()
    try:
        service_code = _sms_service_code(service)
        provider = str(provider or _provider()).strip().lower()
        if provider == "tiger":
            params, text = _request_tiger_number(http, _tiger_number_params(service_code, country))
        elif provider == "smsbower":
            params, text = _request_smsbower_number(http, _smsbower_number_params(service_code, country))
        else:
            raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")
        if params["action"] == "getNumberV2":
            try:
                data = json.loads(text)
            except Exception as exc:
                raise SmsProviderError(f"SMSBower getNumberV2 响应不是 JSON：{text[:200]}") from exc
            if not isinstance(data, dict):
                raise SmsProviderError(f"SMSBower getNumberV2 响应格式异常：{text[:200]}")
            activation_id = str(data.get("activationId") or data.get("id") or "").strip()
            phone = str(data.get("phoneNumber") or data.get("phone") or "").strip()
            if not activation_id or not phone:
                raise SmsProviderError(f"SMSBower getNumberV2 响应缺少激活 ID/号码：{text[:200]}")
            provider_id = str(
                data.get("providerId")
                or data.get("provider_id")
                or data.get("activationOperator")
                or data.get("operator")
                or ""
            ).strip()
            tier = str(
                data.get("activationOperator")
                or data.get("operator")
                or data.get("tier")
                or provider_id
                or "default"
            ).strip()
            metadata = {
                "provider": provider,
                "service": service_code,
                "country": str(country or getattr(_cfg, "SMS_COUNTRY", "") or ""),
                "tier": tier,
                "acquire_mode": "getNumberV2",
            }
            if provider_id:
                metadata["provider_id"] = provider_id
            metadata.update(_activation_price_metadata(data, provider, price_quote))
            return _remember_activation(activation_id, phone, metadata)
        if not text.startswith("ACCESS_NUMBER:"):
            raise SmsProviderError(f"SMSBower getNumber 非预期响应：{text[:200]}")
        parts = text.split(":", 2)
        if len(parts) < 3:
            raise SmsProviderError(f"SMSBower getNumber 响应格式异常：{text[:200]}")
        activation_id, phone = parts[1].strip(), parts[2].strip()
        if not activation_id or not phone:
            raise SmsProviderError(f"SMSBower getNumber 响应缺少激活 ID/号码：{text[:200]}")
        return _remember_activation(
            activation_id,
            phone,
            {
                "provider": provider,
                "service": service_code,
                "country": str(country or getattr(_cfg, "SMS_COUNTRY", "") or ""),
                "tier": "default",
                "acquire_mode": "getNumber",
                **_activation_price_metadata(None, provider, price_quote),
            },
        )
    finally:
        if own_http:
            http.close()


def _acquire_number_for_provider(
    provider: str,
    http: CurlSession,
    service: str | None = None,
    country: str | None = None,
) -> tuple[str, str]:
    """在单个平台内按价格/库存策略取号；一个激活只归属该平台。"""
    provider = str(provider or "").strip().lower()
    if provider == "tiger":
        return _acquire_tiger_number(http, service=service, country=country, provider=provider)
    if provider != "smsbower":
        raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")

    attempts = _setting_int("SMS_NUMBER_ACQUIRE_RETRIES", 3, 1)
    service_code = _sms_service_code(service)
    random_country_mode = (
        provider == "smsbower"
        and country is None
        and bool(getattr(_cfg, "SMSBOWER_RANDOM_COUNTRY", True))
    )
    random_countries: list[dict] = []
    tried_countries: set[str] = set()
    fixed_price_quote = None
    last_no_numbers: SmsNoNumbersError | None = None
    if random_country_mode:
        attempts = max(
            attempts,
            _setting_int("SMSBOWER_RANDOM_COUNTRY_ATTEMPTS", 12, 1),
        )
        random_countries = _smsbower_random_country_candidates(http, service_code)
    elif not random_country_mode:
        configured_country = str(country or getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
        if configured_country:
            try:
                fixed_price_quote = next(
                    (row for row in _smsbower_price_rows(http, service_code)
                     if row["country"] == configured_country),
                    None,
                )
            except Exception as exc:
                logger.warning("[SMSBower] 获取号码价格快照失败，不影响取号：%s", exc)
    for attempt in range(1, attempts + 1):
        candidate = None
        if random_country_mode:
            if not random_countries:
                try:
                    refreshed = _smsbower_random_country_candidates(http, service_code)
                except SmsNoNumbersError as exc:
                    last_no_numbers = exc
                    logger.warning("[SMSBower] 刷新随机国家库存后仍无候选：%s", str(exc)[:220])
                    break
                fresh = [row for row in refreshed if row["country"] not in tried_countries]
                random_countries = fresh or refreshed
            candidate = random_countries.pop(0)
            tried_countries.add(candidate["country"])
        selected_country = candidate["country"] if candidate else country
        try:
            activation_id, phone = _acquire_number_once(
                http,
                service=service_code,
                country=selected_country,
                price_quote=candidate or fixed_price_quote,
                provider=provider,
            )
        except SmsNoNumbersError as exc:
            last_no_numbers = exc
            if random_country_mode and attempt < attempts:
                logger.info(
                    "[SMSBower] 随机国家无号，刷新库存并继续候选：attempt=%s/%s country=%s cost=%s",
                    attempt, attempts, candidate["country"], candidate["cost"],
                )
                continue
            raise
        try:
            state = _validate_activation_price(activation_id)
        except SmsNumberRejectedError as exc:
            state = _activation_state(activation_id)
            logger.warning(
                "[SMSBower] 号码不符合价格策略，释放并换号：attempt=%s/%s phone=%s region=%s reason=%s",
                attempt, attempts, _mask_phone(phone),
                state.get("phone_country_name") or state.get("phone_region") or "unknown",
                str(exc)[:180],
            )
            try:
                cancel_and_report_failure(activation_id, http=http, reason=exc)
            except Exception as release_exc:
                logger.warning("[SMSBower] 违规号码释放失败，继续换号：id=%s error=%s", activation_id, release_exc)
            continue
        recently_rejected = _number_is_recently_rejected(phone)
        cooled = _tier_is_cooled(state)
        if recently_rejected or cooled:
            reason = "recently_rejected_number" if recently_rejected else "cooled_tier"
            _metric("candidate_skipped", state)
            logger.info(
                "[SMSBower] 跳过策略排除候选：attempt=%s/%s reason=%s phone=%s",
                attempt, attempts, reason, _mask_phone(phone),
            )
            _release_strategy_rejected(activation_id, http)
            continue
        return activation_id, phone
    if random_country_mode:
        detail = f"；最后一次取号结果：{last_no_numbers}" if last_no_numbers else ""
        raise SmsNoNumbersError(
            f"随机尝试/刷新 {attempts} 个价格范围内国家后仍无可用号码{detail}"
        ) from last_no_numbers
    raise SmsNumberRejectedError(f"连续 {attempts} 次取到的号码或供应商层级均被策略排除")


def acquire_number(
    http: CurlSession | None = None,
    service: str | None = None,
    country: str | None = None,
) -> tuple[str, str]:
    """按 provider 链顺序采购；每次只采购一个平台的一个激活，不并发跨平台取号。"""
    own_http = http is None
    http = http or _http()
    errors: list[SmsProviderError] = []
    try:
        providers = _attempt_provider_chain()
        for index, provider in enumerate(providers):
            try:
                logger.info("[SMS] 开始 provider 尝试：%s (%s/%s)", provider, index + 1, len(providers))
                return _acquire_number_for_provider(provider, http, service=service, country=country)
            except SmsProviderError as exc:
                errors.append(exc)
                if index + 1 < len(providers) and _provider_error_allows_fallback(exc):
                    logger.warning(
                        "[SMS] provider=%s 取号失败，按顺序切换到下一个 provider：%s",
                        provider, str(exc)[:180],
                    )
                    continue
                raise
        raise SmsProviderConfigurationError("没有可用的短信平台配置")
    finally:
        if own_http:
            http.close()


# ============================================================
# 取短信验证码
# ============================================================

def wait_for_sms_code(
    activation_id: str,
    http: CurlSession | None = None,
    max_wait: int | None = None,
    poll_interval: int | None = None,
) -> str:
    """轮询 SMSBower getStatus，直到拿到当前激活的新验证码。"""
    own_http = http is None
    http = http or _http()
    total_wait = max_wait if max_wait is not None else _setting_int("SMS_CODE_WAIT", 120, 0)
    interval = poll_interval if poll_interval is not None else _setting_int("SMS_POLL_INTERVAL", 5, 0)
    deadline = time.monotonic() + total_wait
    retry_status_sent = False
    try:
        provider_name = "Tiger SMS" if _provider_for_activation(activation_id) == "tiger" else "SMSBower"
        logger.info("[%s] 等待短信验证码 activation_id=%s，最长 %ss", provider_name, activation_id, total_wait)
        round_no = 0
        while time.monotonic() < deadline:
            try:
                from core.registration_service import check_stop_requested
                check_stop_requested()
            except ImportError:
                pass
            round_no += 1
            provider = _provider_for_activation(activation_id)
            request = _request_tiger if provider == "tiger" else _request_smsbower
            text = request(http, {"action": "getStatus", "id": activation_id})
            if text.startswith("STATUS_OK:"):
                code = text.split(":", 1)[1].strip().strip("'")
                _update_activation_state(activation_id, {"code_received": True})
                if _remember_code(activation_id, code):
                    logger.info("[%s] 第 %s 轮收到验证码：length=%s", provider_name, round_no, len(code))
                    return code
                logger.info("[%s] 第 %s 轮忽略历史验证码：length=%s", provider_name, round_no, len(code))
            elif text in {"STATUS_CANCEL", "ACCESS_CANCEL"}:
                raise SmsProviderError(f"{provider_name} 激活已被取消（{text}）")
            elif text.startswith("STATUS_WAIT_RETRY"):
                old_code = text.split(":", 1)[1].strip() if ":" in text else ""
                if old_code:
                    _update_activation_state(activation_id, {"code_received": True})
                    _remember_code(activation_id, old_code)
                if not retry_status_sent:
                    retry_status_sent = True
                    try:
                        set_status(activation_id, 3, http=http)
                    except Exception as exc:
                        logger.warning("[%s] 请求下一条短信失败（继续轮询）：%s", provider_name, exc)
            remaining = max(0, int(deadline - time.monotonic()))
            logger.info(
                "[%s] 第 %s 轮未收到验证码，状态=%s，%ss 后重试（剩余 %ss）",
                provider_name, round_no, text, interval, remaining,
            )
            _stop_sleep(interval)
        raise SmsCodeTimeout(f"等待 {provider_name} 短信超时（>{total_wait}s），activation_id={activation_id}")
    finally:
        if own_http:
            http.close()


# ============================================================
# 改状态、完成和释放
# ============================================================

def set_status(activation_id: str, status: int, http: CurlSession | None = None) -> str:
    """通过当前短信平台 setStatus 更新激活状态。"""
    own_http = http is None
    http = http or _http()
    try:
        provider = _provider_for_activation(activation_id)
        if int(status) == 1 and provider == "smsbower":
            return "OK"
        request = _request_tiger if provider == "tiger" else _request_smsbower
        return request(http, {"action": "setStatus", "status": str(status), "id": activation_id})
    finally:
        if own_http:
            http.close()


def _completed_activation_info(activation_id: str) -> dict:
    state = _activation_state(activation_id)
    info = {}
    phone = str(state.get("phone_number") or "").strip()
    if phone:
        info["phone_number"] = phone if phone.startswith("+") else "+" + phone
    country = str(state.get("country") or "").strip()
    if country:
        info["country"] = country
    for key in (
        "provider", "phone_region", "phone_country_name", "phone_dial_code",
        "price_amount", "price_currency", "price_source",
        "price_limit_min", "price_limit_max", "price_limit_currency", "price_limit_source",
        "price_limit_configured_max", "price_limit_configured_currency", "price_limit_fx_rate", "price_limit_fx_source",
        "price_validated", "price_checked_amount", "price_checked_currency",
    ):
        value = state.get(key)
        if isinstance(value, bool):
            if value:
                info[key] = value
        else:
            value = str(value or "").strip()
            if value:
                info[key] = value
    return info


def complete(activation_id: str, http: CurlSession | None = None) -> dict:
    """标记当前短信平台激活完成并返回号码/价格快照。"""
    info = _completed_activation_info(activation_id)
    try:
        set_status(activation_id, 6, http=http)
        logger.info("[%s] 已标记完成 activation_id=%s", _provider_for_activation(activation_id), activation_id)
    except Exception as exc:
        logger.warning("[%s] 标记完成失败（不影响结果）：%s", _provider_for_activation(activation_id), exc)
    finally:
        _forget_activation(activation_id)
    return info


def cancel(activation_id: str, http: CurlSession | None = None, background: bool = True) -> dict:
    """同步关闭当前激活并返回平台结果；瞬时失败会立即重试。"""
    state = _activation_state(activation_id)
    provider = _provider_for_activation(activation_id)
    # status=8 只适用于尚未收到短信的号码；收到代码后平台要求 status=6，
    # 否则会返回 BAD_STATUS。两者都要在异常离开前同步发出，避免激活继续悬挂。
    close_status = 6 if state.get("code_received") else 8
    retries = _setting_int("SMS_RELEASE_RETRIES", 3, 1)
    retry_delay = max(0, _setting_int("SMS_RELEASE_RETRY_DELAY", 1, 0))
    last_error: Exception | None = None
    try:
        attempted = 0
        for attempt in range(1, retries + 1):
            attempted = attempt
            try:
                response = set_status(activation_id, close_status, http=http)
                release = "closed_after_code" if close_status == 6 else "cancelled"
                logger.info(
                    "[%s] 已关闭激活 activation_id=%s status=%s response=%s release=%s attempt=%s/%s",
                    provider, activation_id, close_status, str(response or "")[:80], release, attempt, retries,
                )
                return {
                    "release": release,
                    "status": close_status,
                    "response": str(response or ""),
                    "attempts": attempt,
                }
            except Exception as exc:
                last_error = exc
                terminal = isinstance(exc, (SmsProviderConfigurationError, SmsNoBalanceError)) or any(
                    marker in str(exc).upper()
                    for marker in ("BAD_STATUS", "NO_ACTIVATION", "BAD_ACTION", "BAD_KEY")
                )
                if attempt < retries and not terminal:
                    logger.warning(
                        "[%s] 释放号码瞬时失败，立即重试：activation_id=%s status=%s attempt=%s/%s error=%s",
                        provider, activation_id, close_status, attempt, retries, str(exc)[:160],
                    )
                    if retry_delay:
                        _stop_sleep(retry_delay)
                    continue
                if terminal:
                    logger.error(
                        "[%s] 释放响应为终态错误，不重复请求：activation_id=%s status=%s error=%s",
                        provider, activation_id, close_status, str(exc)[:160],
                    )
                    break
        logger.warning(
            "[%s] 释放号码失败：activation_id=%s status=%s attempts=%s error=%s",
            provider, activation_id, close_status, retries, str(last_error or "")[:180],
        )
        return {
            "release": "failed",
            "status": close_status,
            "error": str(last_error or "释放请求失败"),
            "attempts": attempted,
        }
    finally:
        _forget_activation(activation_id)
