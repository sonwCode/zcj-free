"""Safe HTTP response metadata for diagnosing upstream and Cloudflare failures."""
import re


def is_cloudflare_challenge(response) -> bool:
    status = int(getattr(response, "status_code", 0) or 0)
    headers = {
        str(key).lower(): str(value)
        for key, value in (getattr(response, "headers", {}) or {}).items()
    }
    return status == 403 and headers.get("cf-mitigated", "").strip().lower() == "challenge"


def response_summary(response) -> str:
    status = int(getattr(response, "status_code", 0) or 0)
    headers = {
        str(key).lower(): str(value)
        for key, value in (getattr(response, "headers", {}) or {}).items()
    }
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", content_type):
        content_type = "unknown"
    ray = headers.get("cf-ray", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", ray):
        ray = "unknown"
    challenge = headers.get("cf-mitigated", "").strip().lower() == "challenge"
    return f"status={status} content_type={content_type} cf_ray={ray} challenge={challenge}"
