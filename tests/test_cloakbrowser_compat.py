import unittest
from unittest.mock import patch

from core.cloakbrowser_driver import CloakElement
from core.stop_control import StopRequested


class _Locator:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def evaluate(self, expression, arg=None, timeout=None):
        self.calls.append((expression, arg, timeout))
        return self.value

    def click(self, timeout=None):
        return None

    def type(self, text, delay=None, timeout=None):
        self.value += text
        self.calls.append(("type", text, delay, timeout))

    def fill(self, text, timeout=None):
        self.value = text
        self.calls.append(("fill", text, timeout))


class _Keyboard:
    def __init__(self):
        self.presses = []

    def press(self, key):
        self.presses.append(key)


class _Page:
    def __init__(self):
        self.keyboard = _Keyboard()


class CloakElementCompatibilityTests(unittest.TestCase):
    def test_text_reads_inner_text_from_locator(self):
        locator = _Locator(" Resend code ")
        element = CloakElement(page=object(), locator=locator)

        self.assertEqual(element.text, " Resend code ")
        self.assertEqual(locator.calls[0][2], 3000)

    def test_text_reads_value_from_handle(self):
        class _Handle:
            def evaluate(self, expression, arg=None):
                return "Submit"

        element = CloakElement(page=object(), handle=_Handle())
        self.assertEqual(element.text, "Submit")

    def test_send_keys_appends_text_instead_of_replacing_value(self):
        locator = _Locator("")
        element = CloakElement(page=_Page(), locator=locator)

        for char in "mail@example.test":
            element.send_keys(char)

        self.assertEqual(locator.value, "mail@example.test")

    def test_fill_writes_complete_value_atomically(self):
        locator = _Locator("partial")
        element = CloakElement(page=_Page(), locator=locator)

        element.fill("mail@example.test")

        self.assertEqual(locator.value, "mail@example.test")
        self.assertIn(("fill", "mail@example.test", 10000), locator.calls)

    def test_send_keys_dispatches_backspace_as_a_key(self):
        page = _Page()
        locator = _Locator("m")
        element = CloakElement(page=page, locator=locator)

        element.send_keys("\ue003")

        self.assertEqual(page.keyboard.presses, ["Backspace"])
        self.assertEqual(locator.value, "m")

    def test_control_a_keeps_the_control_modifier(self):
        page = _Page()
        element = CloakElement(page=page, locator=_Locator("old"))

        element.send_keys("\ue009", "a")

        self.assertEqual(page.keyboard.presses, ["Control+A"])

    def test_send_keys_checks_stop_after_each_browser_command(self):
        locator = _Locator("")
        element = CloakElement(page=_Page(), locator=locator)

        with patch(
            "core.cloakbrowser_driver._check_stop_requested",
            side_effect=[None, None, StopRequested("fixture stop")],
        ):
            with self.assertRaises(StopRequested):
                element.send_keys("m")

        self.assertEqual(locator.value, "m")


if __name__ == "__main__":
    unittest.main()
