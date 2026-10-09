import ast
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "core" / "roxy_registration.py"


def _load_wait_helper():
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SOURCE))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "_wait_after_email_otp_submit"
    )

    class Clock:
        def __init__(self):
            self.now = 0.0

        def time(self):
            return self.now

    clock = Clock()

    class Driver:
        def __init__(self, states):
            self.states = list(states)
            self.index = 0

    def is_email_verification_page(driver):
        return driver.index < len(driver.states)

    def email_otp_page_state(driver):
        state = driver.states[min(driver.index, len(driver.states) - 1)]
        driver.index += 1
        return state

    def stop_sleep(seconds):
        clock.now += seconds

    namespace = {
        "time": clock,
        "logger": Mock(),
        "_EMAIL_OTP_INVALID_STABILITY_SECONDS": 2.0,
        "_stop_aware_sleep": stop_sleep,
        "_check_manual_stop": Mock(),
        "_is_email_verification_page": is_email_verification_page,
        "_email_otp_page_state": email_otp_page_state,
        "_log_prefix": lambda driver: "[test]",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace["_wait_after_email_otp_submit"], Driver


class EmailOtpSubmitWaitTests(unittest.TestCase):
    def test_transient_error_marker_does_not_trigger_resend(self):
        wait, Driver = _load_wait_helper()
        result = wait(
            Driver([
                {"errors": ["temporary render marker"], "inputs": [{"name": "otp", "ariaInvalid": "true"}]},
                {"errors": [], "inputs": [{"name": "otp", "ariaInvalid": ""}]},
                {"errors": [], "inputs": [{"name": "otp", "ariaInvalid": ""}]},
            ]),
            timeout=5,
        )
        self.assertEqual(result, "accepted")

    def test_stable_error_marker_is_invalid(self):
        wait, Driver = _load_wait_helper()
        result = wait(
            Driver([
                {"errors": ["incorrect code"], "inputs": [{"name": "otp", "ariaInvalid": "true"}]}
                for _ in range(8)
            ]),
            timeout=5,
        )
        self.assertEqual(result, "invalid")


if __name__ == "__main__":
    unittest.main()
