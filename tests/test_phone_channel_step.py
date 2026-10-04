import ast
import unittest
from pathlib import Path
SOURCE=Path(__file__).parents[1]/'core/roxy_codex_oauth.py'; TEXT=SOURCE.read_text(encoding='utf-8')
def body(name):
 t=ast.parse(TEXT); n=next(n for n in t.body if isinstance(n,ast.FunctionDef) and n.name==name); return ast.get_source_segment(TEXT,n) or ''
class SmsChannelSelectionTests(unittest.TestCase):
 def test_reference_channel_selection(self):
  b=body('_select_sms_channel_or_raise'); self.assertIn('sms.click()',b); self.assertIn("dispatchEvent(new Event(\'input\'",b); self.assertIn("dispatchEvent(new Event(\'change\'",b); self.assertIn('return true',b)
 def test_phone_submission_helpers_remain(self):
  self.assertIn('_select_sms_channel_or_raise(driver)',TEXT); self.assertIn('_click_add_phone_continue_button(driver',TEXT)
if __name__=='__main__': unittest.main()
