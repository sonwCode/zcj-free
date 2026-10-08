import ast
import unittest
from pathlib import Path
SOURCE=Path(__file__).parents[1]/'core/roxy_codex_oauth.py'; TEXT=SOURCE.read_text(encoding='utf-8')
def body(name):
 t=ast.parse(TEXT); n=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name==name); return ast.get_source_segment(TEXT,n) or ''
class SmsChannelSelectionTests(unittest.TestCase):
 def test_channel_selection_uses_real_click_and_rechecks_state(self):
  b=body('_select_sms_channel_or_raise'); self.assertIn('_human_click(driver, target',b); self.assertIn('_phone_page_state(driver)',b); self.assertIn('_assert_sms_channel_or_raise(driver)',b); self.assertIn('return',b)
 def test_phone_submission_helpers_remain(self):
  self.assertIn('_select_sms_channel_or_raise(driver)',TEXT); self.assertIn('_click_add_phone_continue_button(driver',TEXT)
if __name__=='__main__': unittest.main()
