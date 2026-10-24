# -*- coding: utf-8 -*-
"""
短信平台客户端（SMSBower、Tiger SMS、HeroSMS、SMSPool、Vak SMS）。

用于 Codex OAuth "全新 session" 流程过 OpenAI 的 /phone-verification 手机号验证：
    1. acquire_number()       按价格/库存取一个手机号（返回激活 ID + 号码）
    2. wait_for_sms_code()    轮询 getStatus 直到拿到短信验证码
    3. complete() / cancel()  setStatus 标记完成(6) / 取消(8)

价格相关：每取一个号、收到短信都会计费，所以：
    - 取号后若收不到短信，必须 cancel(8) 释放，避免白扣钱；
    - 成功拿到码后 complete(6) 正式完成激活。
"""
import contextlib
import json
import hashlib
import logging
import os
import random
import re
import threading
import time
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from pathlib import Path
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
_RECENT_REJECTED_COUNTRIES: dict[tuple[str, str, str], float] = {}
_TIER_FAILURES: dict[tuple[str, str, str, str], int] = {}
_TIER_COOLDOWNS: dict[tuple[str, str, str, str], float] = {}
_METRICS: dict[str, int] = {}
_FX_RATE_CACHE: tuple[float, Decimal, str] | None = None
_QUALITY_LOCK = threading.RLock()
_QUALITY_CACHE: dict | None = None
_QUALITY_CACHE_PATH: str | None = None


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


_VALID_PROVIDERS = ("smsbower", "tiger", "herosms", "smspool", "vaksms")


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
    elif provider == "herosms":
        key_name, base_name = "HEROSMS_API_KEY", "HEROSMS_API_BASE"
    elif provider == "smspool":
        key_name, base_name = "SMSPOOL_API_KEY", "SMSPOOL_API_BASE"
    elif provider == "vaksms":
        key_name, base_name = "VAKSMS_API_KEY", "VAKSMS_API_BASE"
    else:
        return False
    return bool(
        str(getattr(_cfg, key_name, "") or "").strip()
        and str(getattr(_cfg, base_name, "") or "").strip()
    )


def _provider_label(provider: str) -> str:
    return {
        "smsbower": "SMSBower",
        "tiger": "Tiger SMS",
        "herosms": "HeroSMS",
        "smspool": "SMSPool",
        "vaksms": "Vak SMS",
    }.get(str(provider or "").strip().lower(), str(provider or "SMS"))


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


def provider_for_retry(attempt: int) -> str | None:
    """按手机号重试轮次选择起始平台，并保留配置链的相对顺序。"""
    providers = _attempt_provider_chain()
    if not providers:
        return None
    # 实际 OAuth 流程会把这里的结果作为 preferred_provider 传入 acquire_number；
    # 因此质量排序必须在这个入口生效，而不能只放在 acquire_number 的无首选分支。
    providers = _quality_rank_providers(providers, getattr(_cfg, "SMS_SERVICE", ""))
    try:
        index = max(0, int(attempt) - 1) % len(providers)
    except (TypeError, ValueError):
        index = 0
    return providers[index]


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


def _normalized_region(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text)


_REGION_SETTING_BY_PROVIDER = {
    "smsbower": "SMSBOWER_EXCEPT_REGIONS",
    "tiger": "TIGER_SMS_EXCEPT_REGIONS",
    "herosms": "HEROSMS_EXCEPT_REGIONS",
    "smspool": "SMSPOOL_EXCEPT_REGIONS",
    "vaksms": "VAKSMS_EXCEPT_REGIONS",
}


def _excluded_region(state: dict, provider: str | None = None) -> str:
    """Return a matching provider-specific excluded region."""
    state = state or {}
    provider_name = str(provider or state.get("provider") or "").strip().lower()
    specific_names = tuple(_REGION_SETTING_BY_PROVIDER.values())
    has_specific = any(str(getattr(_cfg, name, "") or "").strip() for name in specific_names)
    specific_name = _REGION_SETTING_BY_PROVIDER.get(provider_name)
    if has_specific and specific_name:
        raw = str(getattr(_cfg, specific_name, "") or "")
    else:
        raw = str(getattr(_cfg, "SMS_EXCEPT_REGIONS", "") or "")
    excluded: set[str] = set()
    for entry in re.split(r"[,，;；\n]+", raw):
        for alias in re.split(r"[/|]", entry):
            normalized = _normalized_region(alias)
            if normalized:
                excluded.add(normalized)
    if not excluded:
        return ""
    for key in ("phone_country_name", "phone_region", "region", "province", "state"):
        value = str(state.get(key) or "").strip()
        if value and _normalized_region(value) in excluded:
            return value
    return ""


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


def _fx_cache_path() -> Path | None:
    """返回最近实时汇率的持久化路径；空配置时不写磁盘。"""
    raw = str(getattr(_cfg, "SMS_FX_CACHE_FILE", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _read_persisted_usd_cny_rate() -> tuple[Decimal, str] | None:
    """读取上次成功的实时 USD/CNY 汇率，供进程重启后短期回退。"""
    path = _fx_cache_path()
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("usd_cny") if isinstance(payload, dict) else None
        usd_cny = Decimal(str(raw or ""))
        if not usd_cny.is_finite() or usd_cny <= 0:
            return None
        updated_at = str(payload.get("updated_at") or "").strip() if isinstance(payload, dict) else ""
        source = "persisted last known USD/CNY"
        if updated_at:
            source += f" at {updated_at}"
        return usd_cny, source
    except (OSError, ValueError, TypeError, InvalidOperation, json.JSONDecodeError) as exc:
        logger.warning("[SMS] 读取最近汇率缓存失败：path=%s error=%s", path, str(exc)[:160])
        return None


def _fallback_usd_cny_rate() -> tuple[Decimal, str]:
    """网络不可用时的回退汇率。

    只使用最近一次成功获取的实时汇率，不使用固定常数兜底。回退值表示
    1 USD = N CNY，与实时汇率同向，避免价格边界被钉死在陈旧汇率上。
    """
    remembered = str(getattr(_cfg, "SMS_LAST_KNOWN_USD_CNY_RATE", "") or "").strip()
    if remembered:
        candidates = ((remembered, "last known USD/CNY"),)
    else:
        persisted = _read_persisted_usd_cny_rate()
        candidates = ((persisted[0], persisted[1]),) if persisted else ()
    for raw, source in candidates:
        if not raw:
            continue
        try:
            usd_cny = Decimal(raw)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if usd_cny.is_finite() and usd_cny > 0:
            return Decimal("1") / usd_cny, f"fallback {source}={raw}"
    raise SmsProviderConfigurationError(
        "实时汇率不可用，且没有最近一次成功汇率（检查 SMS_FX_RATE_URLS 和 "
        "SMS_FX_CACHE_FILE）"
    )


def _remember_usd_cny_rate(usd_cny: Decimal) -> None:
    """把本次实时 USD/CNY 写入内存和运行时文件，供后续回退使用。"""
    try:
        formatted = format(usd_cny, "f")
        _cfg.SMS_LAST_KNOWN_USD_CNY_RATE = formatted
        path = _fx_cache_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps({
            "usd_cny": formatted,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    except Exception as exc:
        logger.warning("[SMS] 记录最近汇率失败（不影响本次取号）：%s", str(exc)[:160])


def _cny_to_usd_rate() -> tuple[Decimal, str]:
    """读取短期缓存的实时 CNY -> USD 汇率。

    按 SMS_FX_RATE_URLS 顺序级联尝试，全部失败时回退到最近一次已知汇率，
    而不是中断取号。实时汇率只是价格边界的换算依据，不该成为硬依赖。
    """
    global _FX_RATE_CACHE
    now = time.monotonic()
    ttl = _setting_int("SMS_FX_RATE_TTL", 600, 60)
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
        if provider not in {"smsbower", "tiger", "herosms", "smspool", "vaksms"}:
            return result
        min_usd, max_usd = _provider_price_bounds_usd(provider)
        if min_usd is not None:
            result["price_limit_min"] = format(min_usd, "f")
        if max_usd is not None:
            result["price_limit_max"] = format(max_usd, "f")
        if min_usd is None and max_usd is None:
            return result
        result["price_limit_currency"] = "USD"
        _, configured_max_cny = _configured_price_bounds_cny(provider)
        if configured_max_cny is not None:
            fx_rate, fx_source = _cny_to_usd_rate()
            result["price_limit_source"] = "SMS_MAX_PRICE CNY converted with " + fx_source
            result["price_limit_configured_max"] = format(configured_max_cny, "f")
            result["price_limit_configured_currency"] = "CNY"
            result["price_limit_fx_rate"] = format(fx_rate, "f")
            result["price_limit_fx_source"] = fx_source
        specific_max_name = {
            "herosms": "HEROSMS_MAX_PRICE",
            "smspool": "SMSPOOL_MAX_PRICE",
            "vaksms": "VAKSMS_MAX_PRICE",
        }.get(provider)
        if specific_max_name:
            specific_max = _smsbower_price_bound(specific_max_name)
            if specific_max is not None:
                result["price_limit_provider_max"] = format(specific_max, "f")
                result["price_limit_provider_currency"] = "USD"
                if configured_max_cny is None:
                    result["price_limit_source"] = f"{specific_max_name} USD"
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


def _quality_path() -> Path | None:
    raw = str(getattr(_cfg, "SMS_QUALITY_FILE", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def _quality_empty() -> dict:
    return {"version": 1, "updated_at": int(time.time()), "buckets": {}, "numbers": {}}


def _quality_data_locked() -> dict | None:
    global _QUALITY_CACHE, _QUALITY_CACHE_PATH
    path = _quality_path()
    path_key = str(path.resolve()) if path is not None else "<memory>"
    if _QUALITY_CACHE is not None and _QUALITY_CACHE_PATH == path_key:
        return _QUALITY_CACHE
    data = _quality_empty()
    if path is not None:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("buckets"), dict) and isinstance(loaded.get("numbers"), dict):
                data.update(loaded)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("[SMS质量] 账本读取失败，将创建新账本：%s", exc)
    _QUALITY_CACHE = data
    _QUALITY_CACHE_PATH = path_key
    return data


def _quality_save_locked(data: dict) -> None:
    path = _quality_path()
    data["updated_at"] = int(time.time())
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("[SMS质量] 账本写入失败：%s", exc)


def _quality_bucket_key(provider: str, service: str, country: str) -> str:
    return "|".join((str(provider or "").lower(), str(service or ""), str(country or "")))


def _quality_record_outcome(state: dict, outcome: str) -> None:
    provider = str(state.get("provider") or "").strip().lower()
    if provider not in _VALID_PROVIDERS:
        return
    service = str(state.get("service") or getattr(_cfg, "SMS_SERVICE", "") or "").strip()
    country = str(state.get("country") or "").strip()
    if not service or not country:
        return
    now = int(time.time())
    phone = _normalize_phone_digits(str(state.get("phone_number") or ""))
    number_key = hashlib.sha256(phone.encode("ascii", errors="ignore")).hexdigest() if phone else ""
    ttl = _setting_int("SMS_NUMBER_REJECT_TTL", 1800, 60)
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        if data is None:
            return
        key = _quality_bucket_key(provider, service, country)
        bucket = data["buckets"].setdefault(key, {
            "provider": provider, "service": service, "country": country,
            "attempts": 0, "successes": 0, "failures": {},
        })
        bucket["attempts"] = int(bucket.get("attempts") or 0) + 1
        if outcome == "success":
            bucket["successes"] = int(bucket.get("successes") or 0) + 1
        else:
            failures = bucket.setdefault("failures", {})
            failures[outcome] = int(failures.get(outcome) or 0) + 1
        bucket["last_at"] = now
        bucket["last_outcome"] = outcome
        failure_reason = str(state.get("last_failure_reason") or "").lower()
        voip_failure = any(marker in failure_reason for marker in ("voip_phone_disallowed", "voip phone", "voip"))
        if number_key:
            number = data["numbers"].setdefault(number_key, {})
            number.update({
                "provider": provider, "service": service, "country": country,
                "last_at": now, "last_outcome": outcome,
            })
            if outcome in {"number_rejected", "send_failed", "code_rejected"}:
                number["blocked_until"] = now + ttl
            if voip_failure:
                number["voip_blocked_until"] = now + ttl
                prefix = _phone_prefix_key(phone)
                if prefix:
                    prefix_table = data.setdefault("voip_prefixes", {})
                    entry = prefix_table.setdefault(prefix, {})
                    entry["blocked_until"] = now + ttl * 4
                    entry["last_at"] = now
                    entry["count"] = int(entry.get("count") or 0) + 1
                    logger.info(
                        "[SMS] 学习到 VoIP 号段前缀：prefix=%s count=%s 冷却=%ss",
                        prefix, entry["count"], ttl * 4,
                    )
            elif outcome == "success":
                number["blocked_until"] = 0
                number["voip_blocked_until"] = 0
        age_seconds = _setting_int("SMS_QUALITY_MAX_AGE_DAYS", 30, 1) * 86400
        max_entries = _setting_int("SMS_QUALITY_MAX_ENTRIES", 5000, 100)
        for table in (data["buckets"], data["numbers"]):
            for item_key, item in list(table.items()):
                last_at = int(item.get("last_at") or 0)
                if now - last_at > age_seconds:
                    table.pop(item_key, None)
            if len(table) > max_entries:
                for item_key, _ in sorted(table.items(), key=lambda row: int(row[1].get("last_at") or 0))[:len(table) - max_entries]:
                    table.pop(item_key, None)
        _quality_save_locked(data)


def _quality_number_is_blocked(phone: str) -> bool:
    normalized = _normalize_phone_digits(phone)
    if not normalized:
        return False
    key = hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        record = (data or {}).get("numbers", {}).get(key, {})
        return int(record.get("blocked_until") or 0) > int(time.time())


def _quality_voip_number_is_blocked(phone: str) -> bool:
    normalized = _normalize_phone_digits(phone)
    if not normalized:
        return False
    key = hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        record = (data or {}).get("numbers", {}).get(key, {})
        return int(record.get("voip_blocked_until") or 0) > int(time.time())


def _phone_prefix_key(phone: str) -> str:
    """取号码前 6 位作为号段前缀（用于学习被 OpenAI 判 VoIP 的号段）。"""
    digits = _normalize_phone_digits(str(phone or ""))
    return digits[:6] if len(digits) >= 6 else ""


def _quality_voip_prefix_is_blocked(phone: str) -> bool:
    prefix = _phone_prefix_key(phone)
    if not prefix:
        return False
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        record = (data or {}).get("voip_prefixes", {}).get(prefix, {})
        return int(record.get("blocked_until") or 0) > int(time.time())


def _phone_type_is_virtual(phone: str) -> bool:
    """号段判型：短信平台不返回 line type，只能用本地 phonenumbers 判断。

    实测 smsbower/tiger 的激活响应里没有 is_voip/virtual 字段，
    原 _activation_declares_voip 永远为 False，导致 59 条号码被 OpenAI 判为
    VoIP（voip_phone_disallowed）才事后发现。
    """
    if not _voip_rejection_enabled():
        return False
    raw = str(phone or "").strip()
    if not raw:
        return False
    try:
        import phonenumbers
        from phonenumbers import PhoneNumberType
    except Exception:
        return False
    try:
        digits = _normalize_phone_digits(raw)
        if not digits:
            return False
        parsed = phonenumbers.parse("+" + digits, None)
        if not phonenumbers.is_valid_number(parsed):
            return False
        return phonenumbers.number_type(parsed) == PhoneNumberType.VOIP
    except Exception:
        return False


def _phone_is_virtual_number(phone: str) -> bool:
    """综合判定：具体号码记忆 + 号段前缀黑名单 + 本地号段类型。"""
    if not _voip_rejection_enabled():
        return False
    return (
        _quality_voip_number_is_blocked(phone)
        or _quality_voip_prefix_is_blocked(phone)
        or _phone_type_is_virtual(phone)
    )


def _excluded_regions_for_provider(provider: str) -> set:
    """读取该平台当前配置的排除地区（已归一化）。"""
    provider_name = str(provider or "").strip().lower()
    specific_names = tuple(_REGION_SETTING_BY_PROVIDER.values())
    has_specific = any(str(getattr(_cfg, name, "") or "").strip() for name in specific_names)
    specific_name = _REGION_SETTING_BY_PROVIDER.get(provider_name)
    if has_specific and specific_name:
        raw = str(getattr(_cfg, specific_name, "") or "")
    else:
        raw = str(getattr(_cfg, "SMS_EXCEPT_REGIONS", "") or "")
    out = set()
    for entry in re.split(r"[,，;；\n]+", raw):
        for alias in re.split(r"[/|]", entry):
            normalized = _normalized_region(alias)
            if normalized:
                out.add(normalized)
    return out


def region_blocked_countries(provider: str) -> set:
    """返回因"地区被排除"而暂缓的 country id 集合，供查价阶段过滤。"""
    provider_name = str(provider or "").strip().lower()
    now = int(time.time())
    out = set()
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        table = (data or {}).get("region_blocked_countries", {})
        for key, item in table.items():
            if not str(key).startswith(provider_name + "|"):
                continue
            if int(item.get("until") or 0) > now:
                out.add(str(key).split("|", 1)[1])
    return out


def _record_region_blocked_country(provider: str, country: str, region: str) -> None:
    """买到被排除地区的号码时记住 (provider, country)，查价阶段不再选它。"""
    provider_name = str(provider or "").strip().lower()
    country_id = str(country or "").strip()
    if not provider_name or not country_id:
        return
    # 自适应退避：连续命中排除地区时冷却指数增长（10min → 20 → 40 … 上限 6h），
    # 而不是一刀切固定 12 小时；一旦该国家出现成功会立刻清零（见下）。
    try:
        base = _setting_int("SMS_REGION_BLOCK_BASE_SECONDS", 600, 60)
        cap = _setting_int("SMS_REGION_BLOCK_MAX_SECONDS", 6 * 3600, 600)
    except Exception:
        base, cap = 600, 6 * 3600
    now = int(time.time())
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        if data is None:
            return
        table = data.setdefault("region_blocked_countries", {})
        key = provider_name + "|" + country_id
        entry = table.setdefault(key, {})
        streak = int(entry.get("streak") or 0) + 1
        ttl = min(cap, base * (2 ** (streak - 1)))
        entry["until"] = now + ttl
        entry["last_at"] = now
        entry["streak"] = streak
        entry["region"] = str(region or "")
        entry["count"] = int(entry.get("count") or 0) + 1
        _quality_save_locked(data)
    logger.info(
        "[SMS] 地区冷却：provider=%s country=%s region=%s streak=%s 冷却=%ss",
        provider_name, country_id, region, streak, ttl,
    )


def region_cooldown_snapshot() -> list:
    """返回当前仍在冷却的 (平台, 国家) 明细，供页面展示剩余时间。"""
    now = int(time.time())
    out = []
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        table = (data or {}).get("region_blocked_countries", {})
        for key, item in table.items():
            until = int(item.get("until") or 0)
            if until <= now:
                continue
            provider, _, country = str(key).partition("|")
            out.append({
                "provider": provider,
                "country": country,
                "region": str(item.get("region") or ""),
                "streak": int(item.get("streak") or 0),
                "count": int(item.get("count") or 0),
                "remaining": until - now,
                "until": until,
            })
    out.sort(key=lambda row: (-row["remaining"], row["provider"]))
    return out


def clear_region_blocked_country(provider: str, country: str) -> None:
    """该国家拿到可用号码时立刻解除冷却：说明排除不是整国失效。"""
    provider_name = str(provider or "").strip().lower()
    country_id = str(country or "").strip()
    if not provider_name or not country_id:
        return
    key = provider_name + "|" + country_id
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        table = (data or {}).get("region_blocked_countries") or {}
        entry = table.get(key)
        if not entry:
            return
        if int(entry.get("streak") or 0) == 0 and int(entry.get("until") or 0) == 0:
            return
        entry["until"] = 0
        entry["streak"] = 0
        entry["cleared_at"] = int(time.time())
        _quality_save_locked(data)
    logger.info("[SMS] 地区冷却解除：provider=%s country=%s", provider_name, country_id)


def _quality_country_is_blocked(provider: str, service: str, country: str) -> bool:
    key = _quality_bucket_key(provider, service, country)
    with _QUALITY_LOCK:
        data = _quality_data_locked()
        record = (data or {}).get("buckets", {}).get(key, {})
        return int(record.get("country_blocked_until") or 0) > int(time.time())


def _quality_rank_candidates(rows: list[dict], provider: str, service: str) -> list[dict]:
    candidates = list(rows)
    random.shuffle(candidates)
    minimum = _setting_int("SMS_QUALITY_MIN_SAMPLES", 3, 1)
    with _QUALITY_LOCK:
        data = _quality_data_locked() or {}
        buckets = data.get("buckets", {})
        def rank(row: dict) -> float:
            bucket = buckets.get(_quality_bucket_key(provider, service, row.get("country")), {})
            attempts = int(bucket.get("attempts") or 0)
            if attempts < minimum:
                return 0.5
            successes = int(bucket.get("successes") or 0)
            failures = bucket.get("failures") or {}
            score = (successes + 1.0) / (attempts + 2.0)
            score -= 0.25 * int(failures.get("number_rejected") or 0) / attempts
            score -= 0.12 * int(failures.get("code_timeout") or 0) / attempts
            return score
        return sorted(candidates, key=rank, reverse=True)


def _quality_rank_providers(providers: list[str], service: str | None) -> list[str]:
    """有足够样本时按 provider 聚合质量排序；无样本保留配置链顺序。"""
    values = list(providers)
    if len(values) < 2:
        return values
    service_code = str(service or getattr(_cfg, "SMS_SERVICE", "") or "").strip()
    if not service_code:
        return values
    minimum = _setting_int("SMS_QUALITY_MIN_SAMPLES", 3, 1)
    with _QUALITY_LOCK:
        data = _quality_data_locked() or {}
        buckets = data.get("buckets", {})
        provider_scores: dict[str, float] = {}
        for provider in values:
            attempts = successes = 0
            rejected = timed_out = 0
            for row in buckets.values():
                if str(row.get("provider") or "").strip().lower() != provider:
                    continue
                if str(row.get("service") or "").strip() != service_code:
                    continue
                attempts += int(row.get("attempts") or 0)
                successes += int(row.get("successes") or 0)
                failures = row.get("failures") or {}
                rejected += int(failures.get("number_rejected") or 0)
                timed_out += int(failures.get("code_timeout") or 0)
            if attempts < minimum:
                provider_scores[provider] = 0.5
                continue
            score = (successes + 1.0) / (attempts + 2.0)
            score -= 0.25 * rejected / attempts
            score -= 0.12 * timed_out / attempts
            provider_scores[provider] = score
        return sorted(values, key=lambda provider: provider_scores.get(provider, 0.5), reverse=True)


def _quality_summary() -> dict:
    with _QUALITY_LOCK:
        data = _quality_data_locked() or {}
        rows = []
        for bucket in data.get("buckets", {}).values():
            attempts = int(bucket.get("attempts") or 0)
            rows.append({
                "provider": bucket.get("provider"), "service": bucket.get("service"),
                "country": bucket.get("country"), "attempts": attempts,
                "successes": int(bucket.get("successes") or 0),
                "success_rate": round(int(bucket.get("successes") or 0) / attempts, 4) if attempts else 0.0,
                "failures": dict(bucket.get("failures") or {}),
                "last_outcome": bucket.get("last_outcome"), "last_at": bucket.get("last_at"),
            })
        rows.sort(key=lambda row: (row["attempts"], row["success_rate"]), reverse=True)
        return {"path": str(_quality_path() or ""), "buckets": rows[:100]}


def _prune_strategy_state() -> None:
    now = time.monotonic()
    with _STATE_LOCK:
        for phone, expires_at in list(_RECENT_REJECTED_NUMBERS.items()):
            if expires_at <= now:
                _RECENT_REJECTED_NUMBERS.pop(phone, None)
        for key, expires_at in list(_RECENT_REJECTED_COUNTRIES.items()):
            if expires_at <= now:
                _RECENT_REJECTED_COUNTRIES.pop(key, None)
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


def _country_rejection_key(state: dict) -> tuple[str, str, str] | None:
    provider = str(state.get("provider") or _provider()).strip().lower()
    service = str(state.get("service") or getattr(_cfg, "SMS_SERVICE", "") or "").strip()
    country = str(state.get("country") or "").strip()
    if not provider or not service or not country:
        return None
    return provider, service, country


def _record_country_rejected(state: dict, reason: object) -> None:
    """兼容旧调用；VoIP 现在只按具体号码记录，不再按国家冷却。"""
    logger.info(
        "[SMS] VoIP 失败仅记录具体号码，不排除国家：provider=%s country=%s reason=%s",
        state.get("provider") or _provider(), state.get("country") or "unknown", str(reason or "")[:120],
    )


def _country_is_recently_rejected(state: dict) -> bool:
    key = _country_rejection_key(state)
    if key is None:
        return False
    _prune_strategy_state()
    with _STATE_LOCK:
        return _RECENT_REJECTED_COUNTRIES.get(key, 0) > time.monotonic()


def _voip_rejection_enabled() -> bool:
    """是否启用主动跳过近期 VoIP 质量失败的候选。"""
    override = getattr(_VOIP_REJECTION_OVERRIDE, "enabled", None)
    if override is not None:
        return bool(override)
    return bool(getattr(_cfg, "SMS_REJECT_VOIP_NUMBERS", False))


def _voip_country_is_blocked(state: dict) -> bool:
    """兼容旧调用；国家永远不因单个 VoIP 号码而被排除。"""
    return False


def _activation_declares_voip(state: dict) -> bool:
    """读取供应商取号响应中可能提供的线路类型标记。"""
    if not _voip_rejection_enabled():
        return False
    for key in ("is_voip", "isVoip", "voip", "virtual", "is_virtual", "isVirtual"):
        value = state.get(key)
        if isinstance(value, bool) and value:
            return True
        if str(value or "").strip().lower() in {"1", "true", "yes", "voip", "virtual"}:
            return True
    for key in ("line_type", "lineType", "phone_type", "phoneType", "number_type", "numberType"):
        value = str(state.get(key) or "").strip().lower()
        if value in {"voip", "virtual", "virtual_mobile", "virtual-number"}:
            return True
    return False


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
        _quality_record_outcome(state, "success")
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
        "already used", "voip", "voip_phone_disallowed", "fraud_guard",
        "suspicious behavior from phone", "号码无效", "手机号无效", "手机号已被使用", "whatsapp_channel",
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
            if category == "number_rejected":
                _record_country_rejected(state, reason)
        if state:
            _record_tier_failure(state, category)
            quality_state = dict(state)
            quality_state["last_failure_reason"] = str(reason or category)
            _quality_record_outcome(quality_state, category)
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


def report_failure(
    activation_id: str,
    reason: object = "",
    category: str | None = None,
    state: dict | None = None,
) -> str:
    """接收注册层反馈，只更新短信调度状态，不改变页面流程。"""
    return _report_failure_with_state(
        activation_id, reason=reason, category=category, state=state
    )


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
            "rejected_countries": len(_RECENT_REJECTED_COUNTRIES),
            "cooled_tiers": len(_TIER_COOLDOWNS),
            "quality": _quality_summary(),
        }


def _reset_runtime_state_for_tests() -> None:
    global _FX_RATE_CACHE, _QUALITY_CACHE, _QUALITY_CACHE_PATH
    with _STATE_LOCK:
        _ACTIVATION_STATE.clear()
        _CODE_HISTORY.clear()
        _RECENT_REJECTED_NUMBERS.clear()
        _RECENT_REJECTED_COUNTRIES.clear()
        _TIER_FAILURES.clear()
        _TIER_COOLDOWNS.clear()
        _METRICS.clear()
        _FX_RATE_CACHE = None
        _QUALITY_CACHE = None
        _QUALITY_CACHE_PATH = None


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


def _request_herosms(http: CurlSession, params: dict) -> str:
    """发 HeroSMS 的 SMS-Activate 兼容 handler_api 请求。"""
    api_key = str(getattr(_cfg, "HEROSMS_API_KEY", "") or "").strip()
    if not api_key:
        raise SmsProviderConfigurationError("HeroSMS API Key 不能为空")
    base = str(getattr(_cfg, "HEROSMS_API_BASE", "") or "").strip()
    if not base:
        raise SmsProviderConfigurationError("HEROSMS_API_BASE 不能为空")
    resp = http.get(base, params={"api_key": api_key, **params})
    text = (resp.text or "").strip()
    if resp.status_code in (401, 403) or text == "BAD_KEY":
        raise SmsProviderConfigurationError("HeroSMS API key 无效（BAD_KEY）")
    if resp.status_code != 200:
        raise SmsProviderError(f"HeroSMS HTTP {resp.status_code}: {text[:200]}")
    if text in {"NO_BALANCE", "NO_MONEY"}:
        raise SmsNoBalanceError(f"HeroSMS 余额不足（{text}），请充值")
    if text == "NO_NUMBERS":
        raise SmsNoNumbersError("HeroSMS 暂无可用号码（NO_NUMBERS）")
    if text in {"BAD_ACTION", "BAD_SERVICE", "WRONG_SERVICE", "BAD_STATUS", "NO_ACTIVATION"}:
        if text in {"BAD_SERVICE", "WRONG_SERVICE"}:
            raise SmsProviderConfigurationError(
                f"HeroSMS 服务代码无效（{text}），OpenAI/ChatGPT 请填写 dr"
            )
        if text == "NO_ACTIVATION":
            raise SmsProviderError("HeroSMS 激活 ID 不存在（NO_ACTIVATION）")
        raise SmsProviderError(f"HeroSMS 请求失败（{text}）")
    return text


def _request_vaksms(http: CurlSession, params: dict) -> str:
    """发 Vak SMS 的 SMS-Activate 兼容 handler_api 请求。"""
    api_key = str(getattr(_cfg, "VAKSMS_API_KEY", "") or "").strip()
    if not api_key:
        raise SmsProviderConfigurationError("Vak SMS API Key 不能为空")
    base = str(getattr(_cfg, "VAKSMS_API_BASE", "") or "").strip()
    if not base:
        raise SmsProviderConfigurationError("VAKSMS_API_BASE 不能为空")
    resp = http.get(base, params={"api_key": api_key, **params})
    text = (resp.text or "").strip()
    if resp.status_code in (401, 403) or text == "BAD_KEY":
        raise SmsProviderConfigurationError("Vak SMS API key 无效（BAD_KEY）")
    if resp.status_code != 200:
        raise SmsProviderError(f"Vak SMS HTTP {resp.status_code}: {text[:200]}")
    if text in {"NO_BALANCE", "NO_MONEY"}:
        raise SmsNoBalanceError(f"Vak SMS 余额不足（{text}），请充值")
    if text == "NO_NUMBERS":
        raise SmsNoNumbersError("Vak SMS 暂无可用号码（NO_NUMBERS）")
    if text in {"BAD_SERVICE", "WRONG_SERVICE"}:
        raise SmsProviderConfigurationError(
            f"Vak SMS 服务代码无效（{text}），OpenAI/ChatGPT 请填写 dr"
        )
    if text in {"BAD_ACTION", "BAD_STATUS", "NO_ACTIVATION"}:
        raise SmsProviderError(f"Vak SMS 请求失败（{text}）")
    return text


def _smspool_payload(resp) -> object:
    try:
        return resp.json()
    except Exception:
        text = (getattr(resp, "text", "") or "").strip()
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text


def _smspool_message(payload: object) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("message", "msg", "error", "title", "status", "statusText"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).strip()
    return str(payload or "").strip()


def _smspool_success(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    value = payload.get("success")
    return value is True or str(value).strip().lower() in {"1", "true", "ok", "success"}


def _smspool_error_code(payload: object) -> str:
    text = _smspool_message(payload).upper()
    for code in ("BAD_KEY", "NO_BALANCE", "NO_NUMBERS", "BAD_SERVICE", "BAD_COUNTRY", "NO_ACTIVATION", "BAD_STATUS"):
        if code in text:
            return code
    return ""


def _request_smspool(http: CurlSession, path: str, params: dict) -> object:
    """发 SMSPool 原生表单 API 请求；key 只放请求体，不写日志。"""
    api_key = str(getattr(_cfg, "SMSPOOL_API_KEY", "") or "").strip()
    if not api_key:
        raise SmsProviderConfigurationError("SMSPool API Key 不能为空")
    base = str(getattr(_cfg, "SMSPOOL_API_BASE", "") or "").strip().rstrip("/")
    if not base:
        raise SmsProviderConfigurationError("SMSPOOL_API_BASE 不能为空")
    payload = {"key": api_key, **{key: value for key, value in params.items() if value not in (None, "")}}
    resp = http.post(f"{base}{path}", data=payload, headers={"Accept": "application/json, text/plain, */*"})
    data = _smspool_payload(resp)
    code = _smspool_error_code(data)
    if resp.status_code in (401, 403) or code == "BAD_KEY":
        raise SmsProviderConfigurationError("SMSPool API key 无效（BAD_KEY）")
    if resp.status_code == 402 or code == "NO_BALANCE":
        raise SmsNoBalanceError("SMSPool 余额不足（NO_BALANCE），请充值")
    if resp.status_code != 200:
        raise SmsProviderError(f"SMSPool HTTP {resp.status_code}: {_smspool_message(data)[:200]}")
    if code in {"BAD_SERVICE", "BAD_COUNTRY"}:
        raise SmsProviderConfigurationError(f"SMSPool 请求参数错误（{code}）")
    if code in {"NO_NUMBERS"}:
        raise SmsNoNumbersError(f"SMSPool 当前没有可用号码（{code}）")
    if code in {"NO_ACTIVATION", "BAD_STATUS"}:
        raise SmsProviderError(f"SMSPool 请求失败（{code}）")
    return data


def _smspool_find_value(payload: object, keys: tuple[str, ...]) -> object:
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key) not in (None, ""):
                return payload.get(key)
        for key in ("data", "result", "order", "activation", "item", "sms", "call"):
            nested = payload.get(key)
            value = _smspool_find_value(nested, keys)
            if value not in (None, ""):
                return value
    elif isinstance(payload, list):
        for item in payload:
            value = _smspool_find_value(item, keys)
            if value not in (None, ""):
                return value
    return None


def _smspool_code(payload: object) -> str:
    value = _smspool_find_value(
        payload,
        ("code", "full_code", "sms_code", "otp", "verification_code", "message", "text", "sms"),
    )
    match = re.search(r"\b(\d{4,8})\b", str(value or ""))
    return match.group(1) if match else ""


def _herosms_code(text: str) -> str:
    """从 HeroSMS V1 文本或 V2 JSON 状态中提取验证码。"""
    text = str(text or "").strip()
    if text.upper().startswith("STATUS_OK:"):
        match = re.search(r"\b(\d{4,8})\b", text.split(":", 1)[1])
        return match.group(1) if match else ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return ""
    return _smspool_code(payload)


def _herosms_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(str(text or "").strip()), dict)
    except (TypeError, ValueError):
        return False


def _smspool_terminal_payload(payload: object) -> bool:
    """识别 SMSPool 已取消、过期或不存在的订单，避免无意义地轮询到超时。"""
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        status = str(payload.get("status") or payload.get("state") or "").strip().lower()
        if status in {"completed", "closed", "cancelled", "canceled", "expired", "timeout", "timed_out"}:
            return True
    else:
        text = str(payload or "")
    return bool(re.search(
        r"cancel|expired|timeout|closed|order\s+not\s+found|invalid\s+order|"
        r"invalid\s+number|does\s+not\s+exist",
        text,
        re.IGNORECASE,
    ))


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


# 号码池等独立流程可以用线程级价格上限覆盖通用 SMS_MAX_PRICE。
# threading.local 保证只影响当前线程，不改变普通注册链的全局配置。
_PRICE_LIMIT_OVERRIDE = threading.local()


def _price_limit_override() -> tuple[Decimal | None, Decimal | None] | None:
    return getattr(_PRICE_LIMIT_OVERRIDE, "bounds", None)


@contextlib.contextmanager
def price_limit_override(max_cny: Decimal | None, min_cny: Decimal | None = None):
    """在当前线程内临时替换人民币价格上限，退出时自动恢复。"""
    previous = _price_limit_override()
    _PRICE_LIMIT_OVERRIDE.bounds = (min_cny, max_cny)
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_PRICE_LIMIT_OVERRIDE, "bounds")
            except AttributeError:
                pass
        else:
            _PRICE_LIMIT_OVERRIDE.bounds = previous


# 号码池等独立流程可以接管一个"已经采购过"的激活，避免同一号码被二次计费。
# 同样使用 threading.local，只影响当前线程，默认路径完全不感知。
_ACTIVATION_OVERRIDE = threading.local()

# 号码池可在当前任务线程内覆盖 VoIP 主动过滤策略，不改变普通接码链配置。
_VOIP_REJECTION_OVERRIDE = threading.local()


def _overridden_activation() -> tuple[str, str] | None:
    """返回当前线程内被接管的激活；没有接管时返回 None。"""
    value = getattr(_ACTIVATION_OVERRIDE, "activation", None)
    if not isinstance(value, dict):
        return None
    activation_id = str(value.get("activation_id") or "").strip()
    if not activation_id:
        return None
    return activation_id, str(value.get("phone_number") or "").strip()


@contextlib.contextmanager
def activation_override(activation_id: str, phone_number: str = ""):
    """在当前线程内复用已采购的激活；acquire_number() 不再向供应商取号。"""
    previous = getattr(_ACTIVATION_OVERRIDE, "activation", None)
    _ACTIVATION_OVERRIDE.activation = {
        "activation_id": str(activation_id or "").strip(),
        "phone_number": str(phone_number or "").strip(),
    }
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_ACTIVATION_OVERRIDE, "activation")
            except AttributeError:
                pass
        else:
            _ACTIVATION_OVERRIDE.activation = previous


@contextlib.contextmanager
def voip_rejection_override(enabled: bool | None):
    """在当前线程内临时覆盖 VoIP 候选过滤策略。"""
    previous = getattr(_VOIP_REJECTION_OVERRIDE, "enabled", None)
    if enabled is None:
        try:
            delattr(_VOIP_REJECTION_OVERRIDE, "enabled")
        except AttributeError:
            pass
    else:
        _VOIP_REJECTION_OVERRIDE.enabled = bool(enabled)
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_VOIP_REJECTION_OVERRIDE, "enabled")
            except AttributeError:
                pass
        else:
            _VOIP_REJECTION_OVERRIDE.enabled = previous


def _configured_price_bounds_cny(provider: str) -> tuple[Decimal | None, Decimal | None]:
    provider = str(provider or "").strip().lower()
    override = _price_limit_override()
    if override is not None:
        # 独立流程的最高价覆盖通用最高价，但不能绕过 SMSBower
        # 自己配置的最低价；否则号码池会重新买回被最低价排除的号码。
        override_min, override_max = override
        if provider == "smsbower":
            configured_min = _smsbower_price_bound("SMSBOWER_MIN_PRICE")
            configured_max = _smsbower_price_bound("SMS_MAX_PRICE")
            min_cny = configured_min if override_min is None else max(override_min, configured_min or override_min)
            max_cny = override_max
            if configured_max is not None:
                max_cny = configured_max if max_cny is None else min(max_cny, configured_max)
            return min_cny, max_cny
        return override
    if provider == "smsbower":
        return _smsbower_price_bound("SMSBOWER_MIN_PRICE"), _smsbower_price_bound("SMS_MAX_PRICE")
    if provider in {"tiger", "herosms", "smspool", "vaksms"}:
        return None, _smsbower_price_bound("SMS_MAX_PRICE")
    return None, None


def _provider_price_bounds_usd(
    provider: str,
    *,
    cny_to_usd_rate: Decimal | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    provider = str(provider or "").strip().lower()
    if provider not in {"smsbower", "tiger", "herosms", "smspool", "vaksms"}:
        return None, None
    min_cny, max_cny = _configured_price_bounds_cny(provider)
    if (min_cny is not None or max_cny is not None) and cny_to_usd_rate is None:
        cny_to_usd_rate, _ = _cny_to_usd_rate()
    min_usd = min_cny * cny_to_usd_rate if min_cny is not None else None
    max_usd = max_cny * cny_to_usd_rate if max_cny is not None else None

    # Provider-specific USD caps are additional restrictions. They can only
    # lower the common RMB ceiling, never bypass it.
    specific_max_name = {
        "herosms": "HEROSMS_MAX_PRICE",
        "smspool": "SMSPOOL_MAX_PRICE",
        "vaksms": "VAKSMS_MAX_PRICE",
    }.get(provider)
    if specific_max_name:
        specific_max = _smsbower_price_bound(specific_max_name)
        if specific_max is not None:
            max_usd = specific_max if max_usd is None else min(max_usd, specific_max)
    if provider == "smspool":
        specific_min = _smsbower_price_bound("SMSPOOL_MIN_PRICE")
        min_usd = specific_min if min_usd is None else max(min_usd, specific_min)
    if provider == "vaksms":
        specific_min = _smsbower_price_bound("VAKSMS_MIN_PRICE")
        min_usd = specific_min if min_usd is None else max(min_usd, specific_min)
    if min_usd is None and max_usd is None:
        return None, None
    return min_usd, max_usd


def _smspool_price_bounds() -> tuple[Decimal | None, Decimal | None]:
    """返回 SMSPool 的有效 USD 范围（包含主页人民币最高价）。"""
    return _provider_price_bounds_usd("smspool")


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


def _smsbower_country(country: str | None = None) -> str:
    """返回显式或配置的国家；为空时交给动态库存策略选择。"""
    return str(country or getattr(_cfg, "SMS_COUNTRY", "") or "").strip()


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
        if currency is None and provider in {"smsbower", "tiger", "herosms", "smspool", "vaksms"}:
            currency = "USD"
        for key in ("price", "cost", "activationCost", "activation_cost"):
            value = source.get(key)
            if value is not None:
                result = make_price(value, currency, f"{provider.upper()} 激活响应")
                if result:
                    return result

    if provider in {"smsbower", "tiger", "herosms", "smspool", "vaksms"} and isinstance(price_quote, dict):
        label = {
            "smsbower": "SMSBower getPrices 报价",
            "tiger": "Tiger SMS getPrices 报价",
            "herosms": "HeroSMS 激活报价",
            "smspool": "SMSPool purchase 报价",
            "vaksms": "Vak SMS 激活报价",
        }[provider]
        result = make_price(price_quote.get("cost"), "USD", label)
        if result:
            return result
    return {}


def _activation_line_metadata(data: dict | None) -> dict:
    """提取供应商明确返回的号码线路类型，不按国家或号段推断。"""
    if not isinstance(data, dict):
        return {}
    sources = [data]
    for key in ("item", "data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            sources.append({**data, **nested})
    result: dict = {}
    for source in sources:
        for key in ("is_voip", "isVoip", "voip", "virtual", "is_virtual", "isVirtual", "line_type", "lineType", "phone_type", "phoneType", "number_type", "numberType"):
            if key in source and source.get(key) not in (None, ""):
                result[key] = source.get(key)
    return result


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
    elif provider == "herosms":
        min_limit, max_limit = _provider_price_bounds_usd("herosms")
        if currency not in ("", "USD"):
            comparable_amount = None
    elif provider == "smspool":
        min_limit, max_limit = _smspool_price_bounds()
        if currency not in ("", "USD"):
            comparable_amount = None
    elif provider == "vaksms":
        min_limit, max_limit = _provider_price_bounds_usd("vaksms")
        if currency not in ("", "USD"):
            comparable_amount = None

    if min_limit is None and max_limit is None:
        _update_activation_state(activation_id, {"price_validated": True})
        return state
    if comparable_amount is None or not comparable_amount.is_finite():
        if provider in {"smsbower", "tiger", "herosms", "smspool", "vaksms"} and str(state.get("request_max_price_usd") or "").strip():
            _update_activation_state(activation_id, {
                "price_validated": True,
                "price_validation_source": "provider_maxPrice",
            })
            return _activation_state(activation_id)
        if provider not in {"smsbower", "tiger", "herosms", "smspool", "vaksms"}:
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
            f"{provider} 取号响应缺少价格，且请求未携带最高价参数"
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
        "price_checked_currency": "USD" if provider in {"smsbower", "tiger", "herosms", "smspool", "vaksms"} else currency or "provider",
    })
    return _activation_state(activation_id)


def _smsbower_random_country_candidates(http: CurlSession, service: str) -> list[dict]:
    """返回有库存且价格在配置区间内的国家，并随机打乱顺序。"""
    if _smsbower_country() or not bool(getattr(_cfg, "SMSBOWER_RANDOM_COUNTRY", True)):
        return []
    # Use the effective bounds so pool logs/errors reflect its independent
    # ceiling while retaining SMSBower's configured price floor.
    min_price_cny, max_price_cny = _configured_price_bounds_cny("smsbower")
    cny_to_usd_rate, fx_source = _cny_to_usd_rate()
    min_price_usd, max_price_usd = _provider_price_bounds_usd(
        "smsbower", cny_to_usd_rate=cny_to_usd_rate
    )
    try:
        # 旧实现固定观察约 24 秒，库存恢复稍慢就会在仍有号可买时报“没有可用国家”。
        # 改为按总等待时长控制刷新轮数，避免过早放弃。
        refresh_seconds = max(0, _setting_int("SMSBOWER_STOCK_WAIT_SECONDS", 90, 0))
    except Exception:
        refresh_seconds = 90
    try:
        refresh_attempts = max(12, _setting_int("SMS_PREFLIGHT_RETRIES", 2, 1))
    except Exception:
        refresh_attempts = 12
    try:
        refresh_delay = max(0, _setting_int("SMS_PREFLIGHT_RETRY_DELAY", 2, 0))
    except Exception:
        refresh_delay = 2
    if refresh_delay > 0 and refresh_seconds > 0:
        # 覆盖总等待窗口所需的轮数；SMS_PREFLIGHT_RETRIES 仍作为下限。
        refresh_attempts = max(
            refresh_attempts, (refresh_seconds + refresh_delay - 1) // refresh_delay + 1
        )

    candidates: list[dict] = []
    stocked: list[dict] = []
    # 只在配置了刷新间隔时启用总时长上限；delay=0 时沿用固定轮数，避免改变既有语义。
    deadline = time.monotonic() + refresh_seconds if refresh_delay > 0 else None
    refresh_attempt = 0
    while True:
        refresh_attempt += 1
        rows = _smsbower_price_rows(http, service)
        stocked = [row for row in rows if row.get("count", 0) > 0]
        candidates = [
            row for row in stocked
            if (max_price_usd is None or row["cost"] <= max_price_usd)
            and (min_price_usd is None or row["cost"] >= min_price_usd)
        ]

        if (
            candidates
            or refresh_attempt >= refresh_attempts
            or (deadline is not None and time.monotonic() >= deadline)
        ):
            break
        cheapest_stocked = min(stocked, key=lambda row: row["cost"]) if stocked else None
        logger.warning(
            "[SMSBower] 当前价格快照没有符合人民币上限的国家，刷新库存：attempt=%s/%s delay=%ss "
            "在库最低价=%s 国家=%s 库存=%s",
            refresh_attempt, refresh_attempts, refresh_delay,
            cheapest_stocked["cost"] if cheapest_stocked else "-",
            cheapest_stocked["country"] if cheapest_stocked else "-",
            cheapest_stocked.get("count", 0) if cheapest_stocked else "-",
        )
        if refresh_delay:
            _stop_sleep(refresh_delay)

    candidates = _quality_rank_candidates(candidates, "smsbower", service)
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
    candidates = _quality_rank_candidates(candidates, "tiger", service)
    if not candidates:
        raise SmsNoNumbersError("Tiger SMS 当前人民币最高价格范围内没有可用国家")
    return candidates


def _vaksms_price_rows(http: CurlSession, service: str, country: str | None = None) -> list[dict]:
    """读取 Vak getPrices 的国家/服务价格库存表，不申请号码。"""
    params = {"action": "getPrices", "service": service}
    if country is not None and str(country).strip():
        params["country"] = str(country).strip()
    text = _request_vaksms(http, params)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SmsProviderError("Vak SMS getPrices 响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise SmsProviderError("Vak SMS getPrices 响应格式异常")
    rows: list[dict] = []
    for country_id, services in payload.items():
        if not isinstance(services, dict):
            continue
        item = services.get(service)
        if not isinstance(item, dict):
            continue
        try:
            cost = Decimal(str(item.get("cost") if item.get("cost") is not None else item.get("price")))
            count = int(item.get("count") or 0)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if cost.is_finite() and cost >= 0:
            rows.append({"country": str(country_id), "cost": cost, "count": count})
    return rows


def _vaksms_random_country_candidates(http: CurlSession, service: str) -> list[dict]:
    """Vak 未指定国家时，从 getPrices 结果中挑选价格范围内的有库存国家。"""
    min_price_usd, max_price_usd = _provider_price_bounds_usd("vaksms")
    rows = _vaksms_price_rows(http, service)
    stocked = [row for row in rows if row.get("count", 0) > 0]
    candidates = [
        row for row in stocked
        if (max_price_usd is None or row["cost"] <= max_price_usd)
        and (min_price_usd is None or row["cost"] >= min_price_usd)
    ]
    candidates = _quality_rank_candidates(candidates, "vaksms", service)
    rate, source = _cny_to_usd_rate()
    min_price_cny, max_price_cny = _configured_price_bounds_cny("vaksms")
    logger.info(
        "[Vak SMS] 随机国家候选：service=%s count=%s price_usd=%s..%s price_cny=%s..%s rate_usd_cny=%s fx_source=%s",
        service, len(candidates), min_price_usd if min_price_usd is not None else 0,
        max_price_usd if max_price_usd is not None else "unbounded",
        min_price_cny if min_price_cny is not None else 0,
        max_price_cny if max_price_cny is not None else "unbounded",
        _format_usd_cny_rate(rate), source,
    )
    if not candidates:
        if max_price_usd is None:
            raise SmsNoNumbersError("Vak SMS 当前价格/库存范围内没有可用国家")
        cheapest = min(stocked, key=lambda row: row["cost"]) if stocked else None
        detail = (
            "；当前在库最低价 USD {}，国家={}，库存={}"
            .format(cheapest["cost"], cheapest["country"], cheapest.get("count", 0))
            if cheapest else "；getPrices 当前没有在库国家"
        )
        raise SmsNoNumbersError(
            f"Vak SMS 在人民币最高价格 ¥{max_price_cny}（约 USD {max_price_usd:.6f}）内没有可用国家{detail}"
        )
    return candidates


def _acquire_vaksms_number(
    http: CurlSession,
    service: str | None = None,
    country: str | None = None,
    provider: str = "vaksms",
) -> tuple[str, str]:
    """Vak 未固定国家时按 getPrices 候选逐国尝试，直到拿到合格号码。"""
    service_code = _provider_service_code("VAKSMS_SERVICE", service)
    attempts = _setting_int("SMS_NUMBER_ACQUIRE_RETRIES", 3, 1)
    configured_country = str(
        country or getattr(_cfg, "VAKSMS_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
    ).strip()
    candidates: list[dict] = []
    if not configured_country:
        candidates = _vaksms_random_country_candidates(http, service_code)
        attempts = min(max(1, attempts), len(candidates))
    for attempt in range(1, attempts + 1):
        candidate = candidates[attempt - 1] if candidates else None
        selected_country = candidate["country"] if candidate else configured_country
        if not selected_country:
            raise SmsProviderConfigurationError("Vak SMS 需要 VAKSMS_COUNTRY、SMS_COUNTRY 或可用价格库存")
        try:
            activation_id, phone = _acquire_number_once(
                http,
                service=service_code,
                country=selected_country,
                price_quote=candidate,
                provider=provider,
            )
        except SmsNoNumbersError:
            if candidates and attempt < attempts:
                logger.info(
                    "[Vak SMS] 候选国家库存已变化，尝试下一个国家：country=%s attempt=%s/%s",
                    selected_country, attempt, attempts,
                )
                continue
            raise
        try:
            state = _validate_activation_price(activation_id)
        except SmsNumberRejectedError as exc:
            logger.warning(
                "[Vak SMS] 号码不符合价格策略，释放并换号：attempt=%s/%s phone=%s reason=%s",
                attempt, attempts, _mask_phone(phone), str(exc)[:180],
            )
            try:
                cancel_and_report_failure(activation_id, http=http, reason=exc)
            except Exception as release_exc:
                logger.warning("[Vak SMS] 超价号码释放失败，继续换号：id=%s error=%s", activation_id, release_exc)
            continue
        if (
            _number_is_recently_rejected(phone)
            or _activation_declares_voip(state)
            or _phone_is_virtual_number(phone)
            or _tier_is_cooled(state)
        ):
            _release_strategy_rejected(activation_id, http)
            continue
        return activation_id, phone
    raise SmsNumberRejectedError(f"Vak SMS 连续 {attempts} 次取到的号码或供应商层级均被策略排除")


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
            if (
                _number_is_recently_rejected(phone)
                or _activation_declares_voip(state)
                or _phone_is_virtual_number(phone)
                or _tier_is_cooled(state)
            ):
                _release_strategy_rejected(activation_id, http)
                continue
            return activation_id, phone
        raise SmsNumberRejectedError(f"Tiger SMS 连续 {attempts} 次取号候选均被策略排除")
    finally:
        pass


def _acquire_fixed_provider_number(
    provider: str,
    http: CurlSession,
    service: str | None = None,
    country: str | None = None,
) -> tuple[str, str]:
    """取号协议固定、库存由供应商决定的平台共用重试策略。"""
    provider = str(provider or "").strip().lower()
    label = _provider_label(provider)
    attempts = _setting_int("SMS_NUMBER_ACQUIRE_RETRIES", 3, 1)
    if provider == "herosms":
        service_code = _provider_service_code("HEROSMS_SERVICE", service)
        selected_country = str(
            country or getattr(_cfg, "HEROSMS_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
    elif provider == "smspool":
        service_code = _provider_service_code("SMSPOOL_SERVICE", service)
        selected_country = str(
            country or getattr(_cfg, "SMSPOOL_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
    elif provider == "vaksms":
        service_code = _provider_service_code("VAKSMS_SERVICE", service)
        selected_country = str(
            country or getattr(_cfg, "VAKSMS_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
        if not selected_country:
            return _acquire_vaksms_number(http, service=service_code, country=None, provider=provider)
    else:
        raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")
    if not selected_country:
        raise SmsProviderConfigurationError(f"{label} 需要配置国家代码")
    for attempt in range(1, attempts + 1):
        activation_id, phone = _acquire_number_once(
            http,
            service=service_code,
            country=selected_country,
            provider=provider,
        )
        try:
            state = _validate_activation_price(activation_id)
        except SmsNumberRejectedError as exc:
            logger.warning("[%s] 号码不符合价格策略，释放并换号：attempt=%s/%s phone=%s reason=%s", label, attempt, attempts, _mask_phone(phone), str(exc)[:180])
            try:
                cancel_and_report_failure(activation_id, http=http, reason=exc)
            except Exception as release_exc:
                logger.warning("[%s] 超价号码释放失败，继续换号：id=%s error=%s", label, activation_id, release_exc)
            continue
        if (
            _number_is_recently_rejected(phone)
            or _activation_declares_voip(state)
            or _phone_is_virtual_number(phone)
            or _tier_is_cooled(state)
        ):
            _release_strategy_rejected(activation_id, http)
            continue
        return activation_id, phone
    raise SmsNumberRejectedError(f"{label} 连续 {attempts} 次取到的号码或供应商层级均被策略排除")


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
    provider = str(provider or "").strip().lower()
    if provider == "herosms":
        service_code = _provider_service_code("HEROSMS_SERVICE", service)
    elif provider == "smspool":
        service_code = _provider_service_code("SMSPOOL_SERVICE", service)
    elif provider == "vaksms":
        service_code = _provider_service_code("VAKSMS_SERVICE", service)
    else:
        service_code = _sms_service_code(service)
    country_code = str(country if country is not None else getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
    if provider == "herosms":
        target = str(country or getattr(_cfg, "HEROSMS_COUNTRY", "") or country_code).strip()
        if not target:
            raise SmsProviderConfigurationError("HeroSMS 需要 HEROSMS_COUNTRY 或 SMS_COUNTRY")
        return {"provider": provider, "service": service_code, "country": target, "probe": "deferred_to_get_number", "available": None, "deferred": True}
    if provider == "smspool":
        target = str(country or getattr(_cfg, "SMSPOOL_COUNTRY", "") or country_code).strip()
        if not target:
            raise SmsProviderConfigurationError("SMSPool 需要 SMSPOOL_COUNTRY 或 SMS_COUNTRY")
        return {"provider": provider, "service": service_code, "country": target, "probe": "deferred_to_get_number", "available": None, "deferred": True}
    if provider == "vaksms":
        target = str(country or getattr(_cfg, "VAKSMS_COUNTRY", "") or country_code).strip()
        if not target:
            candidates = _vaksms_random_country_candidates(http, service_code)
            return {
                "provider": "vaksms", "service": service_code, "country": None,
                "probe": "getPrices", "available": True,
                "candidate_count": len(candidates), "countries": [row["country"] for row in candidates],
            }
        return {"provider": provider, "service": service_code, "country": target, "probe": "deferred_to_get_number", "available": None, "deferred": True}
    if provider == "tiger":
        return _preflight_tiger(http, service_code, country)
    if provider != "smsbower":
        raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")
    min_price_usd, max_price_usd = _smsbower_price_bounds_usd()
    if country is None and not country_code and bool(getattr(_cfg, "SMSBOWER_RANDOM_COUNTRY", True)):
        try:
            retry_count = _setting_int("SMS_PREFLIGHT_RETRIES", 2, 1)
        except Exception:
            retry_count = 2
        try:
            retry_delay = max(0, _setting_int("SMS_PREFLIGHT_RETRY_DELAY", 2, 0))
        except Exception:
            retry_delay = 2
        candidates = []
        for preflight_attempt in range(1, retry_count + 1):
            rows = _smsbower_price_rows(http, service_code)
            candidates = [
                row for row in rows
                if (max_price_usd is None or row["cost"] <= max_price_usd)
                and (min_price_usd is None or row["cost"] >= min_price_usd)
            ]
            if candidates:
                break
            if preflight_attempt < retry_count:
                logger.warning(
                    "[SMSBower] 预检价格/库存范围内暂无候选，等待后刷新：attempt=%s/%s delay=%ss",
                    preflight_attempt, retry_count, retry_delay,
                )
                if retry_delay:
                    _stop_sleep(retry_delay)
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
    rows = _smsbower_price_rows(http, service_code)
    target = country_code.lstrip("+")
    candidates = [
        row for row in rows
        if row["country"].lstrip("+") == target
        and (max_price_usd is None or row["cost"] <= max_price_usd)
        and (min_price_usd is None or row["cost"] >= min_price_usd)
    ]
    if candidates:
        return {
            "provider": "smsbower",
            "service": service_code,
            "country": country_code,
            "probe": "getPrices",
            "available": True,
            "candidate_count": len(candidates),
            "countries": [country_code],
        }
    raise SmsNoNumbersError(f"SMSBower 国家 {country_code} 暂无符合价格/库存条件的号码")


def preflight_sms_dependency(
    http: CurlSession | None = None,
    service: str | None = None,
    country: str | None = None,
) -> dict:
    """Defer stock selection to the provider's number-acquisition API."""
    providers = _attempt_provider_chain()
    if not providers:
        raise SmsProviderConfigurationError("没有可用的短信平台配置")
    provider = providers[0]
    if provider == "herosms":
        service_code = _provider_service_code("HEROSMS_SERVICE", service)
        requested_country = str(
            country or getattr(_cfg, "HEROSMS_COUNTRY", "")
            or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
    elif provider == "smspool":
        service_code = _provider_service_code("SMSPOOL_SERVICE", service)
        requested_country = str(
            country or getattr(_cfg, "SMSPOOL_COUNTRY", "")
            or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
    elif provider == "vaksms":
        service_code = _provider_service_code("VAKSMS_SERVICE", service)
        requested_country = str(
            country or getattr(_cfg, "VAKSMS_COUNTRY", "")
            or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
    elif provider == "smsbower":
        service_code = _sms_service_code(service)
        requested_country = _smsbower_country(country)
    else:
        service_code = _sms_service_code(service)
        requested_country = str(
            country or getattr(_cfg, "SMS_COUNTRY", "") or ""
        ).strip()
    logger.info(
        "[SMS] 库存与价格范围交由供应商取号接口处理：provider=%s country=%s",
        provider, requested_country or "provider-default",
    )
    return {
        "provider": provider,
        "service": service_code,
        "country": requested_country or None,
        "probe": "deferred_to_get_number",
        "available": None,
        "deferred": True,
    }


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
    selected_country = _smsbower_country(country)
    params = {
        "action": "getNumberV2" if bool(getattr(_cfg, "SMSBOWER_USE_V2", True)) else "getNumber",
        "service": service_code,
    }
    if selected_country:
        params["country"] = selected_country
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
    dynamic_excluded = _cooldown_provider_ids(service_code, selected_country)
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


def _provider_service_code(config_name: str, service: str | None) -> str:
    configured = str(getattr(_cfg, config_name, "") or "").strip()
    return _sms_service_code(configured or service)


def _provider_max_price_param(provider: str) -> str:
    """Return the effective provider max price, including the common RMB cap."""
    provider = str(provider or "").strip().lower()
    _, maximum = _provider_price_bounds_usd(provider)
    if maximum is None:
        return ""
    specific_name = {
        "herosms": "HEROSMS_MAX_PRICE",
        "smspool": "SMSPOOL_MAX_PRICE",
        "vaksms": "VAKSMS_MAX_PRICE",
    }.get(provider)
    common = str(getattr(_cfg, "SMS_MAX_PRICE", "") or "").strip()
    specific = str(getattr(_cfg, specific_name, "") or "").strip() if specific_name else ""
    # Preserve an existing provider-only value when it is already the
    # effective ceiling; use the normalized six-decimal value when the
    # common RMB cap participates in the calculation.
    if specific and not common:
        return specific
    return _smsbower_api_price(maximum, ROUND_DOWN)


def _herosms_number_params(service: str | None, country: str | None) -> dict:
    selected_country = str(
        country or getattr(_cfg, "HEROSMS_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
    ).strip()
    if not selected_country:
        raise SmsProviderConfigurationError("HeroSMS 需要 HEROSMS_COUNTRY 或 SMS_COUNTRY")
    params = {
        "action": "getNumberV2" if bool(getattr(_cfg, "HEROSMS_USE_V2", False)) else "getNumber",
        "service": _provider_service_code("HEROSMS_SERVICE", service),
        "country": selected_country,
    }
    max_price = _provider_max_price_param("herosms")
    if max_price:
        params["maxPrice"] = max_price
    for key, value in (
        ("providerIds", getattr(_cfg, "HEROSMS_PROVIDER_IDS", "")),
        ("exceptProviderIds", getattr(_cfg, "HEROSMS_EXCEPT_PROVIDER_IDS", "")),
        ("phoneException", getattr(_cfg, "HEROSMS_PHONE_EXCEPTION", "")),
    ):
        value = str(value or "").strip()
        if value:
            params[key] = value
    return params


def _smspool_number_params(service: str | None, country: str | None) -> dict:
    selected_country = str(
        country or getattr(_cfg, "SMSPOOL_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
    ).strip()
    if not selected_country:
        raise SmsProviderConfigurationError("SMSPool 需要 SMSPOOL_COUNTRY 或 SMS_COUNTRY")
    service_code = _provider_service_code("SMSPOOL_SERVICE", service)
    params = {
        "country": selected_country,
        "service": service_code,
        "quantity": "1",
    }
    max_price = _provider_max_price_param("smspool")
    if max_price:
        params["max_price"] = max_price
    pricing_option = str(getattr(_cfg, "SMSPOOL_PRICING_OPTION", "") or "").strip()
    if pricing_option:
        params["pricing_option"] = pricing_option
    pool = str(getattr(_cfg, "SMSPOOL_POOL", "") or "").strip()
    if pool:
        params["pool"] = pool
    return params


def _vaksms_number_params(service: str | None, country: str | None) -> dict:
    selected_country = str(
        country or getattr(_cfg, "VAKSMS_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or ""
    ).strip()
    if not selected_country:
        raise SmsProviderConfigurationError("Vak SMS 需要 VAKSMS_COUNTRY 或 SMS_COUNTRY")
    params = {
        "action": "getNumberV2" if bool(getattr(_cfg, "VAKSMS_USE_V2", False)) else "getNumber",
        "service": _provider_service_code("VAKSMS_SERVICE", service),
        "country": selected_country,
    }
    max_price = _provider_max_price_param("vaksms")
    if max_price:
        params["maxPrice"] = max_price
    if bool(getattr(_cfg, "VAKSMS_FIXED_PRICE", False)):
        params["fixedPrice"] = "true"
    for key, value in (
        ("operator", getattr(_cfg, "VAKSMS_OPERATOR", "")),
        ("ref", getattr(_cfg, "VAKSMS_REF", "")),
    ):
        value = str(value or "").strip()
        if value:
            params[key] = value
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


def _request_herosms_number(http: CurlSession, params: dict) -> tuple[dict, str]:
    candidates = [dict(params)]
    if params.get("action") == "getNumberV2":
        candidates.append({**params, "action": "getNumber"})
    for index, candidate in enumerate(candidates):
        try:
            return candidate, _request_herosms(http, candidate)
        except SmsNoNumbersError:
            raise
        except SmsProviderError as exc:
            if candidate.get("action") == "getNumberV2" and "BAD_ACTION" in str(exc) and index + 1 < len(candidates):
                logger.warning("[HeroSMS] getNumberV2 不兼容，回退到 getNumber")
                continue
            raise
    raise SmsProviderError("HeroSMS 没有可用的取号请求方案")


def _request_vaksms_number(http: CurlSession, params: dict) -> tuple[dict, str]:
    candidates = [dict(params)]
    if params.get("action") == "getNumberV2":
        candidates.append({**params, "action": "getNumber"})
    for index, candidate in enumerate(candidates):
        try:
            return candidate, _request_vaksms(http, candidate)
        except SmsNoNumbersError:
            raise
        except SmsProviderError as exc:
            if candidate.get("action") == "getNumberV2" and "BAD_ACTION" in str(exc) and index + 1 < len(candidates):
                logger.warning("[Vak SMS] getNumberV2 不兼容，回退到 getNumber")
                continue
            raise
    raise SmsProviderError("Vak SMS 没有可用的取号请求方案")


def _request_smspool_number(http: CurlSession, params: dict) -> tuple[dict, object]:
    payload = _request_smspool(http, "/purchase/sms", params)
    activation_id = _smspool_find_value(payload, ("order_id", "orderid", "order_code", "activationId", "id"))
    phone = _smspool_find_value(payload, ("number", "phone", "phone_number", "phonenumber", "phoneNumber"))
    if not activation_id or not phone:
        if _smspool_message(payload).upper() in {"NO_NUMBERS", "NO_PROVIDERS"}:
            raise SmsNoNumbersError("SMSPool 当前没有可用号码")
        raise SmsProviderError(f"SMSPool purchase/sms 响应缺少订单 ID/号码：{_smspool_message(payload)[:200]}")
    return dict(params), payload


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
            error_text = str(exc).upper()
            v2_compatibility_error = any(
                code in error_text for code in ("BAD_ACTION", "BAD_SERVICE", "WRONG_SERVICE")
            )
            if (
                candidate.get("action") == "getNumberV2"
                and v2_compatibility_error
                and index + 1 < len(unique)
            ):
                logger.warning(
                    "[SMSBower] getNumberV2 服务兼容错误，回退到 getNumber 接口"
                )
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
        recently_rejected = _RECENT_REJECTED_NUMBERS.get(normalized, 0) > time.monotonic()
    return (
        recently_rejected
        or _quality_number_is_blocked(normalized)
        or (_voip_rejection_enabled() and _phone_is_virtual_number(normalized))
    )


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
        elif provider == "herosms":
            params, text = _request_herosms_number(http, _herosms_number_params(service_code, country))
        elif provider == "smspool":
            params, text = _request_smspool_number(http, _smspool_number_params(service_code, country))
        elif provider == "vaksms":
            params, text = _request_vaksms_number(http, _vaksms_number_params(service_code, country))
        else:
            raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")
        if provider == "smspool":
            activation_id = str(_smspool_find_value(text, ("order_id", "orderid", "order_code", "activationId", "id")) or "").strip()
            phone = str(_smspool_find_value(text, ("number", "phone", "phone_number", "phonenumber", "phoneNumber")) or "").strip()
            if not activation_id or not phone:
                raise SmsProviderError(f"SMSPool purchase/sms 响应缺少订单 ID/号码：{_smspool_message(text)[:200]}")
            actual_country = str(country or getattr(_cfg, "SMSPOOL_COUNTRY", "") or getattr(_cfg, "SMS_COUNTRY", "") or "").strip()
            metadata = {
                "provider": provider,
                "service": str(params.get("service") or service_code),
                "country": actual_country,
                "tier": str(_smspool_find_value(text, ("pool", "provider", "operator")) or "default").strip(),
                "acquire_mode": "purchase/sms",
            }
            request_max_price = str(params.get("max_price") or "").strip()
            if request_max_price:
                metadata["request_max_price_usd"] = request_max_price
            metadata.update(_activation_price_metadata(text if isinstance(text, dict) else None, provider, None))
            metadata.update(_activation_line_metadata(text if isinstance(text, dict) else None))
            return _remember_activation(activation_id, phone, metadata)
        if params["action"] == "getNumberV2":
            try:
                data = json.loads(text)
            except Exception as exc:
                raise SmsProviderError(f"{_provider_label(provider)} getNumberV2 响应不是 JSON：{text[:200]}") from exc
            if not isinstance(data, dict):
                raise SmsProviderError(f"{_provider_label(provider)} getNumberV2 响应格式异常：{text[:200]}")
            activation_id = str(data.get("activationId") or data.get("id") or "").strip()
            phone = str(data.get("phoneNumber") or data.get("phone") or "").strip()
            if not activation_id or not phone:
                raise SmsProviderError(f"{_provider_label(provider)} getNumberV2 响应缺少激活 ID/号码：{text[:200]}")
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
            configured_country = (
                getattr(_cfg, "HEROSMS_COUNTRY", "") if provider == "herosms"
                else getattr(_cfg, "VAKSMS_COUNTRY", "") if provider == "vaksms"
                else getattr(_cfg, "SMS_COUNTRY", "")
            )
            actual_country = str(
                data.get("countryCode") or data.get("country_code") or data.get("country")
                or country or configured_country or ""
            ).strip()
            metadata = {
                "provider": provider,
                "service": service_code,
                "country": actual_country,
                "tier": tier,
                "acquire_mode": "getNumberV2",
            }
            if provider in {"herosms", "vaksms"} and params.get("action") == "getNumberV2":
                metadata["status_action"] = "getStatusV2"
            if params.get("maxPrice"):
                metadata["request_max_price_usd"] = str(params["maxPrice"])
            if params.get("minPrice"):
                metadata["request_min_price_usd"] = str(params["minPrice"])
            if provider_id:
                metadata["provider_id"] = provider_id
            metadata.update(_activation_price_metadata(data, provider, price_quote))
            metadata.update(_activation_line_metadata(data))
            return _remember_activation(activation_id, phone, metadata)
        if not text.startswith("ACCESS_NUMBER:"):
            raise SmsProviderError(f"{_provider_label(provider)} getNumber 非预期响应：{text[:200]}")
        parts = text.split(":", 2)
        if len(parts) < 3:
            raise SmsProviderError(f"{_provider_label(provider)} getNumber 响应格式异常：{text[:200]}")
        activation_id, phone = parts[1].strip(), parts[2].strip()
        if not activation_id or not phone:
            raise SmsProviderError(f"{_provider_label(provider)} getNumber 响应缺少激活 ID/号码：{text[:200]}")
        configured_country = (
            getattr(_cfg, "HEROSMS_COUNTRY", "") if provider == "herosms"
            else getattr(_cfg, "VAKSMS_COUNTRY", "") if provider == "vaksms"
            else getattr(_cfg, "SMS_COUNTRY", "")
        )
        metadata = {
            "provider": provider,
            "service": service_code,
            "country": str(country or configured_country or "").strip(),
            "tier": "default",
            "acquire_mode": "getNumber",
        }
        if params.get("maxPrice"):
            metadata["request_max_price_usd"] = str(params["maxPrice"])
        if params.get("minPrice"):
            metadata["request_min_price_usd"] = str(params["minPrice"])
        metadata.update(_activation_price_metadata(None, provider, price_quote))
        return _remember_activation(activation_id, phone, metadata)
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
    if provider in {"tiger", "herosms", "smspool", "vaksms"}:
        attempts = _setting_int("SMS_NUMBER_ACQUIRE_RETRIES", 3, 1)
        for attempt in range(1, attempts + 1):
            if provider == "tiger":
                activation_id, phone = _acquire_tiger_number(
                    http, service=service, country=country, provider=provider
                )
            else:
                activation_id, phone = _acquire_fixed_provider_number(
                    provider, http, service=service, country=country
                )
            state = _activation_state(activation_id)
            excluded_region = _excluded_region(state, provider)
            if not excluded_region:
                clear_region_blocked_country(provider, state.get("country"))
                return activation_id, phone
            _metric("candidate_skipped", state)
            logger.info(
                "[%s] 按地区策略释放候选并换号：attempt=%s/%s region=%s phone=%s",
                _provider_label(provider), attempt, attempts, excluded_region, _mask_phone(phone),
            )
            _record_region_blocked_country(provider, state.get("country"), excluded_region)
            _release_strategy_rejected(activation_id, http)
        raise SmsNumberRejectedError(
            f"{_provider_label(provider)} 连续 {attempts} 次号码地区命中排除规则"
        )
    if provider != "smsbower":
        raise SmsProviderConfigurationError(f"不支持的短信平台：{provider}")

    attempts = _setting_int("SMS_NUMBER_ACQUIRE_RETRIES", 3, 1)
    service_code = _sms_service_code(service)
    configured_country = _smsbower_country(country)
    random_country_mode = not configured_country
    if random_country_mode and not bool(getattr(_cfg, "SMSBOWER_RANDOM_COUNTRY", True)):
        raise SmsProviderConfigurationError("SMS_COUNTRY 为空且未启用 SMSBOWER_RANDOM_COUNTRY")
    candidates: list[dict] = []
    if random_country_mode:
        candidates = _smsbower_random_country_candidates(http, service_code)
        attempts = min(
            max(attempts, _setting_int("SMSBOWER_RANDOM_COUNTRY_ATTEMPTS", 12, 1)),
            len(candidates),
        )
    last_no_numbers: SmsNoNumbersError | None = None
    for attempt in range(1, attempts + 1):
        candidate = candidates[attempt - 1] if candidates else None
        selected_country = candidate["country"] if candidate else configured_country
        try:
            activation_id, phone = _acquire_number_once(
                http,
                service=service_code,
                country=selected_country,
                price_quote=candidate,
                provider=provider,
            )
        except SmsNoNumbersError as exc:
            last_no_numbers = exc
            if random_country_mode and attempt < attempts:
                logger.info(
                    "[SMSBower] 候选国家暂无号码，尝试下一个国家：country=%s attempt=%s/%s",
                    selected_country, attempt, attempts,
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
        excluded_region = _excluded_region(state, provider)
        if excluded_region:
            _metric("candidate_skipped", state)
            logger.info(
                "[SMSBower] 按地区策略释放候选并换号：attempt=%s/%s region=%s phone=%s",
                attempt, attempts, excluded_region, _mask_phone(phone),
            )
            _record_region_blocked_country(provider, state.get("country"), excluded_region)
            _release_strategy_rejected(activation_id, http)
            continue
        recently_rejected = _number_is_recently_rejected(phone)
        cooled = _tier_is_cooled(state)
        declared_voip = _activation_declares_voip(state) or _phone_is_virtual_number(phone)
        if recently_rejected or declared_voip or cooled:
            if recently_rejected:
                reason = "recently_rejected_number"
            elif declared_voip:
                reason = "voip_number_type"
            else:
                reason = "cooled_tier"
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
            f"供应商随机取号重试 {attempts} 次后仍无可用号码{detail}"
        ) from last_no_numbers
    raise SmsNumberRejectedError(f"连续 {attempts} 次取到的号码或供应商层级均被策略排除")


def acquire_number_unused(
    http: CurlSession | None = None,
    service: str | None = None,
    country: str | None = None,
    preferred_provider: str | None = None,
    max_tries: int = 3,
) -> tuple[str, str]:
    """取一个"没用过"的号码（OpenAI 一个号码只能验一个账号）。

    平台会重复分配旧号码，这类号码在 OpenAI 侧必然被拒。
    命中已用记录时立刻取消并换号，而不是把配额和次数浪费在上面。
    """
    try:
        from core.phone_registry import is_used
    except Exception:
        return acquire_number(http=http, service=service, country=country,
                             preferred_provider=preferred_provider)
    tries = max(1, int(max_tries or 1))
    for attempt in range(1, tries + 1):
        activation_id, phone = acquire_number(
            http=http, service=service, country=country,
            preferred_provider=preferred_provider,
        )
        if not is_used(phone):
            return activation_id, phone
        logger.warning('[SMS] 分配到已用过的号码，取消并换号（%s/%s）', attempt, tries)
        try:
            cancel_and_report_failure(activation_id, http,
                                      RuntimeError('number_already_used'))
        except Exception:
            pass
    raise RuntimeError('phone_numbers_exhausted: 连续 %s 次分配到已用号码' % tries)


def acquire_number(
    http: CurlSession | None = None,
    service: str | None = None,
    country: str | None = None,
    preferred_provider: str | None = None,
) -> tuple[str, str]:
    """按 provider 链顺序采购；可从指定平台开始，单次只采购一个激活。"""
    overridden = _overridden_activation()
    if overridden is not None:
        # 号码池等流程已提前采购并锁定号码，这里只复用，不产生新的取号与计费。
        logger.info("[SMS] 复用已锁定号码：activation_id=%s", overridden[0])
        return overridden
    own_http = http is None
    http = http or _http()
    errors: list[SmsProviderError] = []
    try:
        providers = _attempt_provider_chain()
        preferred = str(preferred_provider or "").strip().lower()
        if preferred in providers:
            start = providers.index(preferred)
            providers = providers[start:] + providers[:start]
        else:
            providers = _quality_rank_providers(providers, service)
        for index, provider in enumerate(providers):
            try:
                logger.info("[SMS] 开始 provider 尝试：%s (%s/%s, preferred=%s)", provider, index + 1, len(providers), preferred or "-")
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
        provider = _provider_for_activation(activation_id)
        provider_name = _provider_label(provider)
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
            if provider == "smspool":
                payload = _request_smspool(http, "/sms/check", {"orderid": activation_id})
                text = _smspool_message(payload)
                code = _smspool_code(payload)
                if code:
                    _update_activation_state(activation_id, {"code_received": True})
                    if _remember_code(activation_id, code):
                        logger.info("[%s] 第 %s 轮收到验证码：length=%s", provider_name, round_no, len(code))
                        return code
                if _smspool_error_code(payload) in {"NO_ACTIVATION", "BAD_STATUS"}:
                    raise SmsProviderError(f"{provider_name} 订单状态异常（{text}）")
                if _smspool_terminal_payload(payload):
                    raise SmsProviderError(f"{provider_name} 订单已结束（{text}）")
            else:
                request = {
                    "tiger": _request_tiger,
                    "smsbower": _request_smsbower,
                    "herosms": _request_herosms,
                    "vaksms": _request_vaksms,
                }[provider]
                state = _activation_state(activation_id)
                status_action = (
                    str(state.get("status_action") or "getStatus").strip()
                    if provider in {"herosms", "vaksms"} else "getStatus"
                )
                text = request(http, {"action": status_action, "id": activation_id})
                if provider in {"herosms", "vaksms"}:
                    code = _herosms_code(text)
                    if code:
                        _update_activation_state(activation_id, {"code_received": True})
                        if _remember_code(activation_id, code):
                            logger.info("[%s] 第 %s 轮收到验证码：length=%s", provider_name, round_no, len(code))
                            return code
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
            elif (
                provider in {"herosms", "vaksms"}
                and status_action == "getStatusV2"
                and _herosms_json_object(text)
            ):
                # V2 在无验证码时返回 JSON pending 对象，保留轮询语义。
                pass
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
        if provider == "smspool":
            if int(status) == 6:
                return "SMSPool complete skipped"
            if int(status) == 8:
                payload = _request_smspool(http, "/sms/cancel", {"orderid": activation_id})
                if not _smspool_success(payload):
                    raise SmsProviderError(f"SMSPool 取消订单失败：{_smspool_message(payload)[:200]}")
                return _smspool_message(payload) or "success"
            return "SMSPool status skipped"
        request = {
            "tiger": _request_tiger,
            "smsbower": _request_smsbower,
            "herosms": _request_herosms,
            "vaksms": _request_vaksms,
        }[provider]
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
