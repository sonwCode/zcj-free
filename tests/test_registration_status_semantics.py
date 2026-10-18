import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parents[1]
SOURCES = (
    ROOT / "main.py",
    ROOT / "core" / "roxy_registration.py",
    ROOT / "core" / "cloakbrowser_registration.py",
    ROOT / "core" / "browser_use_registration.py",
)
SERVICE_SOURCE = ROOT / "core" / "registration_service.py"
EXPECTED_KEYS = (
    "task_status",
    "account_status",
    "codex_status",
    "phase",
    "error_code",
    "retryable",
)


def _load_retry_info(account):
    tree = ast.parse(SERVICE_SOURCE.read_text(encoding="utf-8"), filename=str(SERVICE_SOURCE))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_retry_info"
    )
    namespace = {
        "db": SimpleNamespace(get_successful_retry_for_job=lambda _job_id: None),
        "_account_for_job": lambda _job: account,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SERVICE_SOURCE), "exec"), namespace)
    return namespace["get_retry_info"]


def _load_status_builder(source_path):
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    assignments = {}
    result_return = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {"codex_status", "codex_ok", "task_status"}:
                assignments[target.id] = node
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = {
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            contains_codex_ok = any(
                isinstance(child, ast.Name) and child.id == "codex_ok"
                for value in node.value.values
                for child in ast.walk(value)
            )
            if "task_status" in keys and contains_codex_ok:
                result_return = node

    missing = {"codex_status", "codex_ok"} - assignments.keys()
    if missing or result_return is None:
        raise AssertionError(f"status contract not found in {source_path}: {missing}")

    result_items = [
        (key, value)
        for key, value in zip(result_return.value.keys, result_return.value.values)
        if isinstance(key, ast.Constant) and key.value in EXPECTED_KEYS
    ]
    body = [assignments[name] for name in ("codex_status", "codex_ok", "task_status") if name in assignments]
    body.append(ast.Return(value=ast.Dict(
        keys=[key for key, _ in result_items],
        values=[value for _, value in result_items],
    )))
    function = ast.FunctionDef(
        name="build_status",
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="codex_result")],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=body,
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace["build_status"]


class RegistrationStatusSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builders = {path: _load_status_builder(path) for path in SOURCES}

    def test_explicit_codex_skip_is_partial_success_and_retryable(self):
        expected = {
            "task_status": "partial_success",
            "account_status": "success",
            "codex_status": "skipped",
            "phase": "codex",
            "error_code": "codex_skipped",
            "retryable": True,
        }
        for path, build_status in self.builders.items():
            with self.subTest(path=path):
                self.assertEqual(
                    build_status({"status": "skipped", "ok": False, "message": "disabled"}),
                    expected,
                )

    def test_failed_codex_remains_partial_success_and_retryable(self):
        expected = {
            "task_status": "partial_success",
            "account_status": "success",
            "codex_status": "failed",
            "phase": "codex",
            "error_code": "codex_failed",
            "retryable": True,
        }
        for path, build_status in self.builders.items():
            with self.subTest(path=path):
                self.assertEqual(
                    build_status({"status": "failed", "ok": False, "message": "transient"}),
                    expected,
                )

    def test_explicit_terminal_codex_failure_stays_non_retryable(self):
        for path, build_status in self.builders.items():
            with self.subTest(path=path):
                result = build_status({
                    "status": "failed",
                    "ok": False,
                    "error_code": "browser_session_circuit_open",
                    "retryable": False,
                    "message": "browser_session_circuit_open",
                })
                self.assertEqual(result["error_code"], "browser_session_circuit_open")
                self.assertFalse(result["retryable"])

    def test_retry_info_blocks_terminal_partial_accounts(self):
        get_retry_info = _load_retry_info({"id": 42, "codex_status": "failed"})
        cases = (
            {"error_code": "phone_fraud_guard", "error": "phone_fraud_guard"},
            {"error_code": "codex_failed", "error": "RuntimeError: 当前 BrowserSession 已熔断冷却"},
            {"error_code": "cloudflare_challenge", "error": "Cloudflare challenge: Just a moment"},
        )
        for failure in cases:
            job = {"id": 7, "status": "partial_success", "account_id": 42, "retryable": True, **failure}
            with self.subTest(error=job["error_code"]):
                info = get_retry_info(job)
                self.assertFalse(info["retryable"])
                self.assertIsNone(info["retry_action"])

    def test_retry_info_honors_persisted_non_retryable_flag(self):
        get_retry_info = _load_retry_info({"id": 42, "codex_status": "failed"})
        info = get_retry_info({
            "id": 7, "status": "partial_success", "account_id": 42,
            "retryable": False, "error_code": "codex_failed",
        })
        self.assertFalse(info["retryable"])

    def test_deactivated_codex_is_partial_success_without_retry(self):
        for path, build_status in self.builders.items():
            with self.subTest(path=path):
                result = build_status({"status": "deactivated", "ok": False})
                self.assertEqual(result["task_status"], "partial_success")
                self.assertFalse(result["retryable"])


if __name__ == "__main__":
    unittest.main()
