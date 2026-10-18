#!/usr/bin/env python3
"""Disable InstaBox auto-suspend and machine timeouts for the account.

The API key is read interactively so it is never placed in shell history or
sent through this repository.  The script is safe to re-run; PATCH requests
are idempotent for the two settings it changes.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://api.instabox.sh"


def api_request(api_key: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = str(uuid.uuid4())
        body = json.dumps(payload).encode("utf-8")

    request = Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("API returned non-JSON data") from exc


def list_machines(api_key: str) -> list[dict[str, Any]]:
    machines: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = f"?{urlencode({'cursor': cursor})}" if cursor else ""
        result = api_request(api_key, "GET", f"/v1/machines{query}")
        page = result.get("machines", [])
        if not isinstance(page, list):
            raise RuntimeError("API returned an invalid machines list")
        machines.extend(item for item in page if isinstance(item, dict))
        cursor = result.get("nextCursor") or result.get("next_cursor")
        if not cursor:
            return machines


def apply_keepalive(api_key: str, machine_id: str) -> dict[str, Any]:
    # Keep the required setting in its own request: some API versions reject
    # the optional timeout field even though auto-suspend is supported.
    result = api_request(
        api_key,
        "PATCH",
        f"/v1/machines/{machine_id}",
        {"autoSuspendAfterSeconds": 0},
    )
    try:
        api_request(api_key, "PATCH", f"/v1/machines/{machine_id}", {"timeoutSeconds": 0})
    except RuntimeError as exc:
        print(f"  timeout 设置未接受（不影响 auto-suspend=0）：{exc}")
    return result


def verified(machine: dict[str, Any]) -> bool:
    idle = machine.get("autoSuspendAfterSeconds")
    timeout = machine.get("timeoutAt")
    return idle in (None, 0) and timeout in (None, 0, "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Disable InstaBox auto-suspend for all machines")
    parser.add_argument("--machine", help="only update a machine id or exact machine name")
    args = parser.parse_args()

    api_key = getpass.getpass("请输入 InstaBox API Key: ").strip()
    if not api_key:
        print("未输入 API Key", file=sys.stderr)
        return 2

    try:
        machines = list_machines(api_key)
        if args.machine:
            machines = [
                machine
                for machine in machines
                if machine.get("id") == args.machine or machine.get("name") == args.machine
            ]
        print(f"\n检测到 {len(machines)} 台机器\n")

        success = 0
        for machine in machines:
            name = machine.get("name", "(unnamed)")
            machine_id = machine.get("id")
            print(f"正在设置：{name}")
            if not machine_id:
                print("  设置失败：缺少 machine id")
                continue
            try:
                apply_keepalive(api_key, str(machine_id))
                current = api_request(api_key, "GET", f"/v1/machines/{machine_id}")
                idle = current.get("autoSuspendAfterSeconds")
                timeout = current.get("timeoutAt")
                state = current.get("state", "unknown")
                if verified(current):
                    success += 1
                    print(f"  成功 | {state} | auto-suspend={idle} | timeout={timeout}")
                else:
                    print(f"  验证未通过 | {state} | auto-suspend={idle} | timeout={timeout}")
            except Exception as exc:  # one machine must not prevent the rest
                print(f"  设置失败：{exc}")

        print(f"\n完成：{success}/{len(machines)} 台设置成功")
        return 0 if success == len(machines) else 1
    except Exception as exc:
        print(f"\n请求失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
