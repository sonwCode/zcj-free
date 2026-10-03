import unittest
from unittest.mock import patch

from core import roxy_registration as roxy


class CloakSeleniumDriver:
    pass


class _Element:
    def __init__(self):
        self.value = ""
        self.fill_calls = []

    def fill(self, value):
        self.fill_calls.append(value)
        self.value = value


class _PartialElement(_Element):
    def fill(self, value):
        self.fill_calls.append(value)
        self.value = "m"


class RoxyCloakEmailInputTests(unittest.TestCase):
    def test_write_email_value_uses_atomic_fill_and_verifies_dom(self):
        driver = CloakSeleniumDriver()
        element = _Element()
        email = "thomasjackson383119@outlook.com"

        with patch.object(
            roxy,
            "_email_input_value_state",
            side_effect=lambda _driver: {"inputs": [{"value": element.value}]},
        ), patch.object(roxy, "_stop_aware_sleep"):
            self.assertTrue(roxy._write_email_value(driver, element, email))

        self.assertEqual(element.fill_calls, [email])
        self.assertEqual(element.value, email)

    def test_write_email_value_reacquires_locator_after_react_replacement(self):
        driver = CloakSeleniumDriver()
        stale = _PartialElement()
        live = _Element()
        current = [stale]
        email = "thomasjackson383119@outlook.com"

        def read_state(_driver):
            return {"inputs": [{"value": current[0].value}]}

        def reacquire(_driver):
            current[0] = live
            return live

        with patch.object(roxy, "_email_input_value_state", side_effect=read_state), patch.object(
            roxy, "_find_visible_email_input", side_effect=reacquire
        ), patch.object(roxy, "_stop_aware_sleep"):
            self.assertTrue(roxy._write_email_value(driver, stale, email))

        self.assertEqual(stale.fill_calls, [email])
        self.assertEqual(live.fill_calls, [email])
        self.assertEqual(live.value, email)


if __name__ == "__main__":
    unittest.main()
