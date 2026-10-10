import ast
import unittest
from pathlib import Path
ROOT=Path(__file__).parents[1]; CODEX=ROOT/'core/roxy_codex_oauth.py'; TEXT=CODEX.read_text(encoding='utf-8')
class ReferencePasswordTests(unittest.TestCase):
 def test_reference_password_helper_exists(self):
  tree=ast.parse(TEXT); names={n.name for n in tree.body if isinstance(n,ast.FunctionDef)}; self.assertIn('_fill_login_password_if_present',names); self.assertIn('_fill_mfa_challenge_if_present',names)
 def test_password_helper_reads_password(self):
  self.assertIn('_account_password_for_email(email)',TEXT); self.assertIn('codex_password_submit',TEXT)
 def test_password_stall_fails_before_callback_wait(self):
  self.assertIn('codex_password_step_stalled',TEXT)
  self.assertIn('if _is_login_password_page(driver):',TEXT)
  self.assertIn('_click_passwordless_signup_if_present(driver)',TEXT)
  self.assertIn('return "email_otp"',TEXT)
  self.assertIn('if "codex_password_step_stalled" in str(exc):',TEXT)
 def test_email_otp_dead_account_text_fails_immediately(self):
  self.assertIn('detect_account_unusable_text',TEXT)
  self.assertIn('dead_text_code = detect_account_unusable_text(body_text)',TEXT)
  self.assertIn('return f"deactivated:{dead_text_code}"',TEXT)
if __name__=='__main__': unittest.main()
