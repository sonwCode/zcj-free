import ast
import unittest
from pathlib import Path
SOURCE=Path(__file__).parents[1]/'core/roxy_registration.py'; TEXT=SOURCE.read_text(encoding='utf-8')
def body(name):
 t=ast.parse(TEXT); n=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name==name); return ast.get_source_segment(TEXT,n) or ''
class PasswordStateTests(unittest.TestCase):
 def test_password_page_state_contract_remains(self):
  self.assertIn('def _password_page_state',TEXT); self.assertIn('def _is_signup_password_page',TEXT); self.assertIn('def _is_login_password_page',TEXT)
 def test_reference_flow_does_not_require_local_reload_patch(self):
  b=body('_fill_password_page_if_present'); self.assertIn('passwordless',b); self.assertIn('create-account/password',b); self.assertNotIn('_recovery_round: int = 0',b)
if __name__=='__main__': unittest.main()
