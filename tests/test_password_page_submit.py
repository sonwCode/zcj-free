import ast
import unittest
from pathlib import Path
SOURCE=Path(__file__).parents[1]/'core/roxy_registration.py'; TEXT=SOURCE.read_text(encoding='utf-8')
def body(name):
 t=ast.parse(TEXT); n=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name==name); return ast.get_source_segment(TEXT,n) or ''
class PasswordPageSubmitTests(unittest.TestCase):
 def test_reference_password_page_has_scoped_input_and_submit(self):
  b=body('_fill_password_page_if_present'); self.assertIn('input[type=',b); self.assertIn('password',b); self.assertIn('submit',b); self.assertIn('_human_type_text',b); self.assertIn('_human_click',b)
 def test_registration_password_and_profile_flow_remain(self):
  self.assertIn('def _registration_password',TEXT); self.assertIn('def _complete_profile_page',TEXT)
if __name__=='__main__': unittest.main()
