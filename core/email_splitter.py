# -*- coding: utf-8 -*-
"""邮箱分裂：把 user@domain 派生成 user+<6位随机>@domain（子地址）。

来源：getlink.agiapi.top/split 的前端算法（本地处理）：
  var alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  email = parsed.local + "+" + secureSuffix() + "@" + parsed.domain;
  results.push(email + "----" + parsed.url);   // 原取码地址原样保留

规则：后缀固定 6 位（小写字母+数字），同批去重；原始行必须是 email----url 两段。
子地址仍投递到同一收件箱，取码要靠 To 头区分；我们的 IMAP 客户端已按
to/deliveredTo/xOriginalTo 过滤，天然适配。

各池派生规则：
  imap              → 别名行 imap_username 必须填主邮箱，密码/服务器沿用
  generic_api       → 别名行沿用同一 code_url
  outlook           → 别名行沿用同一 refresh_token/client_id
  cloudflare_domain → 不派生（每个地址本身是独立临时邮箱）
"""
from __future__ import annotations

import re
import secrets

ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SUFFIX_LEN = 6


class SplitError(ValueError):
    """输入行无法解析。"""


def secure_suffix(length: int = SUFFIX_LEN) -> str:
    length = max(1, int(length or SUFFIX_LEN))
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def parse_line(raw: str) -> dict:
    line = str(raw or "").strip()
    if "----" not in line:
        raise SplitError("缺少 ---- 分隔: %s" % line[:40])
    email, payload = line.split("----", 1)
    email, payload = email.strip(), payload.strip()
    at = email.rfind("@")
    if at <= 0 or at == len(email) - 1 or not payload:
        raise SplitError("邮箱或载荷非法: %s" % line[:40])
    local, domain = email[:at], email[at + 1:]
    if re.search(r"\s", local) or re.search(r"\s", domain) or "." not in domain[1:]:
        raise SplitError("域名非法: %s" % domain[:40])
    return {"local": local, "domain": domain, "payload": payload, "email": email}


def split_email(email: str, count: int = 1, suffix_len: int = SUFFIX_LEN) -> list:
    at = str(email or "").rfind("@")
    if at <= 0:
        raise SplitError("邮箱非法: %s" % email)
    local, domain = email[:at], email[at + 1:]
    out, seen, guard = [], set(), 0
    while len(out) < max(1, int(count or 1)):
        guard += 1
        if guard > 10000:
            raise SplitError("派生次数超限")
        candidate = "%s+%s@%s" % (local, secure_suffix(suffix_len), domain)
        if candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


def split_lines(text: str, count_per_line: int = 1):
    results, invalid, seen = [], 0, set()
    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue
        try:
            parsed = parse_line(raw)
        except SplitError:
            invalid += 1
            continue
        for _ in range(max(1, int(count_per_line or 1))):
            guard = 0
            while True:
                guard += 1
                if guard > 10000:
                    raise SplitError("派生次数超限")
                alias = "%s+%s@%s" % (parsed["local"], secure_suffix(), parsed["domain"])
                if alias not in seen:
                    seen.add(alias)
                    break
            results.append("%s----%s" % (alias, parsed["payload"]))
    return results, invalid