import ast
import unittest
from pathlib import Path
ROOT=Path(__file__).parents[1]
TEXT=(ROOT/'core/roxy_codex_oauth.py').read_text(encoding='utf-8')
def src(name):
 t=ast.parse(TEXT); n=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name==name); return ast.get_source_segment(TEXT,n) or ''
class CodexOtpRoutingTests(unittest.TestCase):
 def test_reference_email_flow_uses_expected_helpers(self):
  b=src('_fill_email_and_otp'); self.assertIn('_submit_email_step(driver)',b); self.assertIn('_fill_login_password_if_present',b); self.assertIn('_maybe_click_passwordless_after_email',b); self.assertIn('_wait_for_fresh_email_otp',b)
 def test_mfa_uses_account_totp(self):
  b=src('_fill_mfa_challenge_if_present'); self.assertIn('_account_totp_code_for_email(email)',b); self.assertIn('codex_mfa_submit',b)
if __name__=='__main__': unittest.main()
