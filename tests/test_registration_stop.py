import ast
import contextlib
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core import codex_retry_service, manual_otp, registration_service
from core.stop_control import StopRequested


ROXY_SOURCE = Path(__file__).parents[1] / "core" / "roxy_registration.py"


def _load_roxy_submit():
    tree = ast.parse(ROXY_SOURCE.read_text(encoding="utf-8"), filename=str(ROXY_SOURCE))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_click_if_enabled_submit"
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROXY_SOURCE), "exec"), namespace)
    return namespace


class _NonTty:
    def isatty(self):
        return False


class RegistrationStopTests(unittest.TestCase):
    _job_counter = 930000
    _job_counter_lock = threading.Lock()

    @classmethod
    def _next_job_id(cls):
        with cls._job_counter_lock:
            cls._job_counter += 1
            return cls._job_counter

    @staticmethod
    def _set_stop(job_id):
        with registration_service._STOP_LOCK:
            event = registration_service._STOP_EVENTS.get(job_id)
            if event is not None:
                event.set()

    def test_persist_codex_phone_activation_from_nested_result(self):
        result = {
            "email": "phone-meta@example.test",
            "account_id": 42,
            "codex": {
                "status": "failed",
                "message": "phone verification failed",
                "phone_activation": {
                    "phone_number": "+15550001111",
                    "provider": "smsbower",
                    "price_amount": "0.02",
                    "price_currency": "USD",
                },
            },
        }
        with patch.object(registration_service.db, "update_account_codex_status") as update:
            registration_service._persist_codex_phone_activation(
                result, result["email"], result["account_id"]
            )

        update.assert_called_once_with(
            "phone-meta@example.test",
            "failed",
            "phone verification failed",
            phone_activation=result["codex"]["phone_activation"],
        )

    def test_stop_aware_sleep_raises_promptly_after_signal(self):
        job_id = self._next_job_id()
        started = threading.Event()
        entered_sleep = threading.Event()
        errors = []

        def worker():
            registration_service._activate_job(job_id)
            started.set()
            try:
                entered_sleep.set()
                registration_service.stop_aware_sleep(30, quantum=0.05)
            except BaseException as exc:
                errors.append(exc)
            finally:
                registration_service._deactivate_job(job_id)

        thread = threading.Thread(target=worker)
        started_at = time.monotonic()
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertTrue(entered_sleep.wait(1))
        self._set_stop(job_id)
        thread.join(1)
        elapsed = time.monotonic() - started_at

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StopRequested)
        self.assertLess(elapsed, 1.5)

    def test_manual_otp_polling_exits_at_stop_checkpoint(self):
        job_id = self._next_job_id()
        email = f"stop-{job_id}@example.test"
        started = threading.Event()
        errors = []

        def worker():
            registration_service._activate_job(job_id)
            started.set()
            try:
                manual_otp.wait_for_manual_otp(email, timeout=60, job_id=job_id)
            except BaseException as exc:
                errors.append(exc)
            finally:
                manual_otp.clear_waiting(email)
                registration_service._deactivate_job(job_id)

        with patch.object(sys, "stdin", _NonTty()):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(started.wait(1))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if any(item.get("email") == email for item in manual_otp.list_waiting()):
                    break
                time.sleep(0.01)
            self._set_stop(job_id)
            thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StopRequested)
        self.assertFalse(any(item.get("email") == email for item in manual_otp.list_waiting()))

    def test_worker_persists_stopped_and_releases_email(self):
        job_id = self._next_job_id()
        email = f"worker-{job_id}@example.test"
        log_file = tempfile.NamedTemporaryFile(delete=False).name
        job = {
            "id": job_id,
            "status": "pending",
            "email_source_mode": "auto",
            "email_source": "outlook",
            "log_file": log_file,
        }
        updates = []
        fake_main = types.ModuleType("main")

        def run_registration(**kwargs):
            self._set_stop(job_id)
            raise StopRequested("fixture stop")

        fake_main.run_registration = run_registration

        def update_job(_job_id, **kwargs):
            updates.append(kwargs)

        with patch.object(registration_service.db, "get_job", return_value=job),                 patch.object(registration_service.db, "update_job", side_effect=update_job),                 patch.object(registration_service, "_prepare_registration_args", return_value=(email, "Test User", "1990-01-01")),                 patch.object(registration_service, "_release_unconsumed_job_email", side_effect=lambda *_args: self.assertTrue(any(item.get("status") == "stopped" for item in updates))) as release,                 patch.object(registration_service.email_provider, "email_source_context", return_value=contextlib.nullcontext()),                 patch.dict(sys.modules, {"main": fake_main}):
            registration_service._run_one_job(job_id, log_file)

        try:
            self.assertIn("running", [item.get("status") for item in updates])
            stopped = [item for item in updates if item.get("status") == "stopped"]
            self.assertEqual(len(stopped), 1)
            self.assertEqual(stopped[0].get("error"), "用户手动停止")
            self.assertEqual(stopped[0].get("phase"), "stopped")
            self.assertIs(stopped[0].get("retryable"), False)
            self.assertEqual(stopped[0].get("account_status"), "stopped")
            self.assertEqual(stopped[0].get("codex_status"), "not_started")
            self.assertEqual(stopped[0].get("error_code"), "stopped")
            release.assert_called_once_with(email, "fixture stop")
            self.assertNotIn(job_id, registration_service._ACTIVE_JOBS)
        finally:
            Path(log_file).unlink(missing_ok=True)

    def test_worker_persists_stopped_state_after_browser_error(self):
        job_id = self._next_job_id()
        email = f"browser-error-{job_id}@example.test"
        log_file = tempfile.NamedTemporaryFile(delete=False).name
        job = {
            "id": job_id,
            "status": "pending",
            "email_source_mode": "auto",
            "email_source": "outlook",
            "log_file": log_file,
        }
        updates = []
        fake_main = types.ModuleType("main")

        def run_registration(**kwargs):
            self._set_stop(job_id)
            raise RuntimeError("browser command timed out")

        fake_main.run_registration = run_registration

        with patch.object(registration_service.db, "get_job", return_value=job), \
                patch.object(registration_service.db, "update_job", side_effect=lambda _job_id, **kwargs: updates.append(kwargs)), \
                patch.object(registration_service, "_prepare_registration_args", return_value=(email, "Test User", "1990-01-01")), \
                patch.object(registration_service, "_release_unconsumed_job_email", side_effect=lambda *_args: self.assertTrue(any(item.get("status") == "stopped" for item in updates))), \
                patch.object(registration_service.email_provider, "email_source_context", return_value=contextlib.nullcontext()), \
                patch.dict(sys.modules, {"main": fake_main}):
            registration_service._run_one_job(job_id, log_file)

        try:
            stopped = [item for item in updates if item.get("status") == "stopped"]
            self.assertEqual(len(stopped), 1)
            self.assertEqual(stopped[0].get("phase"), "stopped")
            self.assertIs(stopped[0].get("retryable"), False)
            self.assertEqual(stopped[0].get("error"), "用户手动停止")
            self.assertEqual(stopped[0].get("account_status"), "stopped")
            self.assertEqual(stopped[0].get("codex_status"), "not_started")
            self.assertEqual(stopped[0].get("error_code"), "stopped")
            self.assertNotIn(job_id, registration_service._ACTIVE_JOBS)
        finally:
            Path(log_file).unlink(missing_ok=True)

    def test_terminal_partial_success_and_blocked_jobs_are_not_stoppable(self):
        for status in ("partial_success", "blocked"):
            with self.subTest(status=status), patch.object(
                registration_service.db,
                "get_job",
                return_value={"id": 1, "status": status},
            ), patch.object(registration_service.db, "update_job") as update_job:
                result = registration_service.request_stop_job(1)
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], status)
            update_job.assert_not_called()

    def test_cancel_pending_codex_retry_releases_reservation_and_account_state(self):
        job = {
            "id": 77,
            "status": "pending",
            "job_type": "codex_retry",
            "email": "queued-codex@example.test",
        }
        with patch.object(registration_service.db, "list_jobs", return_value=[job]), \
                patch.object(registration_service.db, "update_job") as update_job, \
                patch.object(registration_service.db, "update_account_codex_status") as update_account, \
                patch.object(codex_retry_service, "release") as release:
            self.assertEqual(registration_service.cancel_pending_jobs(), 1)

        release.assert_called_once_with(job["email"])
        update_account.assert_called_once_with(
            job["email"], "stopped", "用户手动取消 Codex 补跑排队"
        )
        update_job.assert_called_once()
        self.assertEqual(update_job.call_args.kwargs["status"], "cancelled")
        self.assertEqual(update_job.call_args.kwargs["account_status"], "success")
        self.assertEqual(update_job.call_args.kwargs["codex_status"], "stopped")
        self.assertTrue(update_job.call_args.kwargs["retryable"])

    def test_worker_persists_failed_terminal_status_fields(self):
        job_id = self._next_job_id()
        email = f"worker-failed-{job_id}@example.test"
        log_file = tempfile.NamedTemporaryFile(delete=False).name
        job = {
            "id": job_id,
            "status": "pending",
            "email_source_mode": "auto",
            "email_source": "outlook",
            "log_file": log_file,
        }
        updates = []
        fake_main = types.ModuleType("main")

        def run_registration(**kwargs):
            raise RuntimeError("fixture registration failure")

        fake_main.run_registration = run_registration

        with patch.object(registration_service.db, "get_job", return_value=job), \
                patch.object(registration_service.db, "update_job", side_effect=lambda _job_id, **kwargs: updates.append(kwargs)), \
                patch.object(registration_service, "_prepare_registration_args", return_value=(email, "Test User", "1990-01-01")), \
                patch.object(registration_service, "_release_unconsumed_job_email"), \
                patch.object(registration_service.email_provider, "email_source_context", return_value=contextlib.nullcontext()), \
                patch.dict(sys.modules, {"main": fake_main}):
            registration_service._run_one_job(job_id, log_file)

        try:
            failed = [item for item in updates if item.get("status") == "failed"][-1]
            self.assertEqual(failed["phase"], "registration")
            self.assertEqual(failed["account_status"], "failed")
            self.assertEqual(failed["codex_status"], "not_started")
            self.assertEqual(failed["error_code"], "runtimeerror")
            self.assertTrue(failed["retryable"])
        finally:
            Path(log_file).unlink(missing_ok=True)

    def test_stopped_state_is_persisted_while_email_release_is_blocked(self):
        job_id = self._next_job_id()
        email = f"blocked-release-{job_id}@example.test"
        log_file = tempfile.NamedTemporaryFile(delete=False).name
        job = {
            "id": job_id,
            "status": "pending",
            "email_source_mode": "auto",
            "email_source": "outlook",
            "log_file": log_file,
        }
        updates = []
        fake_main = types.ModuleType("main")
        release_started = threading.Event()
        allow_release = threading.Event()

        def run_registration(**kwargs):
            self._set_stop(job_id)
            raise StopRequested("fixture stop")

        def release_email(*_args):
            release_started.set()
            allow_release.wait(timeout=2)

        fake_main.run_registration = run_registration
        thread = threading.Thread(target=registration_service._run_one_job, args=(job_id, log_file))

        try:
            with patch.object(registration_service.db, "get_job", return_value=job), \
                    patch.object(registration_service.db, "update_job", side_effect=lambda _job_id, **kwargs: updates.append(kwargs)), \
                    patch.object(registration_service, "_prepare_registration_args", return_value=(email, "Test User", "1990-01-01")), \
                    patch.object(registration_service, "_release_unconsumed_job_email", side_effect=release_email), \
                    patch.object(registration_service.email_provider, "email_source_context", return_value=contextlib.nullcontext()), \
                    patch.dict(sys.modules, {"main": fake_main}):
                thread.start()
                self.assertTrue(release_started.wait(1))
                stopped = [item for item in updates if item.get("status") == "stopped"]
                self.assertEqual(len(stopped), 1)
                self.assertEqual(stopped[0].get("phase"), "stopped")
                self.assertIs(stopped[0].get("retryable"), False)
                self.assertTrue(thread.is_alive())
                allow_release.set()
                thread.join(1)
        finally:
            allow_release.set()
            if thread.ident is not None:
                thread.join(1)
            Path(log_file).unlink(missing_ok=True)

        self.assertFalse(thread.is_alive())

    def test_worker_persists_stopped_before_release_after_runner_returns(self):
        job_id = self._next_job_id()
        email = f"return-stop-{job_id}@example.test"
        log_file = tempfile.NamedTemporaryFile(delete=False).name
        job = {
            "id": job_id,
            "status": "pending",
            "email_source_mode": "auto",
            "email_source": "outlook",
            "log_file": log_file,
        }
        updates = []
        fake_main = types.ModuleType("main")

        def run_registration(**kwargs):
            self._set_stop(job_id)
            return {"success": True, "task_status": "success", "email": email}

        fake_main.run_registration = run_registration

        with patch.object(registration_service.db, "get_job", return_value=job), \
                patch.object(registration_service.db, "update_job", side_effect=lambda _job_id, **kwargs: updates.append(kwargs)), \
                patch.object(registration_service, "_prepare_registration_args", return_value=(email, "Test User", "1990-01-01")), \
                patch.object(registration_service, "_release_unconsumed_job_email", side_effect=lambda *_args: self.assertTrue(any(item.get("status") == "stopped" for item in updates))) as release, \
                patch.object(registration_service.email_provider, "email_source_context", return_value=contextlib.nullcontext()), \
                patch.dict(sys.modules, {"main": fake_main}):
            registration_service._run_one_job(job_id, log_file)

        try:
            stopped = [item for item in updates if item.get("status") == "stopped"]
            self.assertEqual(len(stopped), 1)
            self.assertEqual(stopped[0].get("phase"), "stopped")
            release.assert_called_once_with(email, "用户手动停止")
            self.assertNotIn(job_id, registration_service._ACTIVE_JOBS)
        finally:
            Path(log_file).unlink(missing_ok=True)

    def test_worker_keeps_saved_account_when_stop_arrives_after_registration(self):
        job_id = self._next_job_id()
        email = f"saved-stop-{job_id}@example.test"
        log_file = tempfile.NamedTemporaryFile(delete=False).name
        job = {
            "id": job_id,
            "status": "pending",
            "email_source_mode": "auto",
            "email_source": "outlook",
            "log_file": log_file,
        }
        updates = []
        fake_main = types.ModuleType("main")

        def run_registration(**kwargs):
            self._set_stop(job_id)
            return {
                "success": True,
                "task_status": "partial_success",
                "account_status": "success",
                "codex_status": "failed",
                "account_id": 88,
                "email": email,
                "error": "Codex failed after account save",
            }

        fake_main.run_registration = run_registration

        with patch.object(registration_service.db, "get_job", return_value=job), \
                patch.object(registration_service.db, "update_job", side_effect=lambda _id, **kwargs: updates.append(kwargs)), \
                patch.object(registration_service, "_prepare_registration_args", return_value=(email, "Test User", "1990-01-01")), \
                patch.object(registration_service, "_release_unconsumed_job_email") as release, \
                patch.object(registration_service.email_provider, "email_source_context", return_value=contextlib.nullcontext()), \
                patch.dict(sys.modules, {"main": fake_main}):
            registration_service._run_one_job(job_id, log_file)

        try:
            terminal = [item for item in updates if item.get("status") == "partial_success"][-1]
            self.assertEqual(terminal["account_status"], "success")
            self.assertEqual(terminal["codex_status"], "failed")
            self.assertEqual(terminal["account_id"], 88)
            self.assertTrue(terminal["retryable"])
            self.assertEqual(terminal["error"], "用户手动停止，账号已保存，Codex 可补跑")
            release.assert_not_called()
        finally:
            Path(log_file).unlink(missing_ok=True)

    def test_recover_interrupted_jobs_releases_only_unconsumed_email(self):
        recovered = [
            {"id": 1, "email": "unconsumed@example.test", "had_account": False},
            {"id": 2, "email": "saved@example.test", "had_account": True},
        ]
        released = []
        with patch.object(
            registration_service.db,
            "recover_interrupted_registration_jobs",
            return_value=recovered,
        ), patch.object(
            registration_service,
            "_release_unconsumed_job_email",
            side_effect=lambda email, reason: released.append((email, reason)),
        ):
            self.assertEqual(registration_service.recover_interrupted_jobs(), 2)
        self.assertEqual([item[0] for item in released], ["unconsumed@example.test"])

    def test_roxy_broad_exception_does_not_swallow_stop(self):
        class Driver:
            def execute_script(self, script):
                return object()

        namespace = _load_roxy_submit()
        namespace["_human_click"] = lambda *args, **kwargs: (_ for _ in ()).throw(StopRequested("fixture stop"))
        with self.assertRaises(StopRequested):
            namespace["_click_if_enabled_submit"](Driver())

    def test_codex_retry_converts_shared_stop_and_releases_reservation(self):
        email = "retry-stop@example.test"
        key = email.casefold()
        with codex_retry_service._RETRYING_LOCK:
            codex_retry_service._RETRYING.add(key)
            codex_retry_service._RESERVED_AT[key] = time.time()

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(codex_retry_service, "check_stop_requested", side_effect=StopRequested("fixture stop")),                     patch.object(codex_retry_service.db, "update_account_codex_status") as update_status:
                result = codex_retry_service.run_worker(
                    email,
                    clear_log=False,
                    target_log_path=Path(tmp) / "retry.log",
                )

        self.assertEqual(result["status"], "stopped")
        self.assertFalse(codex_retry_service.is_retrying(email))
        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.args[1], "stopped")

    def test_codex_retry_converts_ordinary_exception_to_failed_result(self):
        email = "retry-error@example.test"
        key = email.casefold()
        with codex_retry_service._RETRYING_LOCK:
            codex_retry_service._RETRYING.add(key)
            codex_retry_service._RESERVED_AT[key] = time.time()

        fake_codex = types.ModuleType("core.codex_oauth")

        def fail(_email, force=False):
            raise RuntimeError("fixture oauth failure")

        fake_codex.run_codex_oauth = fail
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(codex_retry_service, "check_stop_requested", return_value=None), \
                    patch.object(codex_retry_service.db, "update_account_codex_status") as update_status, \
                    patch.dict(sys.modules, {"core.codex_oauth": fake_codex}):
                result = codex_retry_service.run_worker(
                    email,
                    clear_log=False,
                    target_log_path=Path(tmp) / "retry.log",
                )

        self.assertEqual(result["status"], "failed")
        self.assertIn("fixture oauth failure", result["message"])
        self.assertFalse(codex_retry_service.is_retrying(email))
        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.args[1], "failed")

    def test_codex_retry_job_persists_account_and_codex_terminal_states(self):
        job_id = self._next_job_id()
        email = "retry-job@example.test"
        job = {"id": job_id, "status": "pending"}
        updates = []
        with patch.object(registration_service.db, "get_job", return_value=job), \
                patch.object(registration_service.db, "update_job", side_effect=lambda _id, **kwargs: updates.append(kwargs)), \
                patch.object(codex_retry_service, "run_worker", return_value={
                    "status": "failed", "ok": False, "message": "oauth failed",
                }), \
                patch.object(codex_retry_service, "release"):
            registration_service._run_codex_retry_job(job_id, "retry.log", email, 42)

        terminal = [item for item in updates if item.get("status") == "failed"][-1]
        self.assertEqual(terminal["phase"], "codex")
        self.assertEqual(terminal["account_status"], "success")
        self.assertEqual(terminal["codex_status"], "failed")
        self.assertEqual(terminal["error_code"], "codex_failed")
        self.assertTrue(terminal["retryable"])
        self.assertEqual(terminal["account_id"], 42)


if __name__ == "__main__":
    unittest.main()
