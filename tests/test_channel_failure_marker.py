import ast
import unittest
from pathlib import Path
ROOT=Path(__file__).parents[1]; CODEX=ROOT/'core/roxy_codex_oauth.py'
def body(name):
 text=CODEX.read_text(encoding='utf-8'); tree=ast.parse(text); node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name); return ast.get_source_segment(text,node) or ''
class ChannelSelectFailureMarkerTests(unittest.TestCase):
 def test_reference_selector_has_only_whatsapp_guard(self):
  b=body('_select_sms_channel_or_raise'); self.assertIn('whatsapp_channel:',b); self.assertIn('sms.click()',b); self.assertNotIn('sms_channel_select_failed',b)
if __name__=='__main__': unittest.main()
