# -*- coding: utf-8 -*-
"""OAuth code 提取的回归测试（覆盖转义与多层编码）。"""
import sys
sys.path.insert(0, '/opt/turb-gpt-register')

from core.oauth_code import code_from_text, code_from_url, extract

CALLBACK = 'http://localhost:1455/auth/callback'


def test_plain_url():
    url = CALLBACK + '?code=abc123XYZ&state=s1'
    assert code_from_url(url) == 'abc123XYZ'


def test_html_escaped_amp():
    url = CALLBACK + '?code=abc123XYZ&amp;state=s1'
    assert code_from_url(url) == 'abc123XYZ'


def test_js_escaped_unicode():
    raw = CALLBACK + '?code=abc123XYZ\\u0026state=s1'
    assert code_from_url(raw) == 'abc123XYZ'


def test_url_encoded_code():
    # 回调里的 code 可能被 URL 编码过一次（如连字符 %2D、下划线原样）
    raw = CALLBACK + '?code=abc%2D123_XyZ&state=s1'
    assert code_from_url(raw) == 'abc-123_XyZ'


def test_from_html_text():
    html = '<a href="' + CALLBACK + '?code=fromHtml99&state=x">继续</a>'
    assert code_from_text(html) == 'fromHtml99'


def test_from_json_data():
    data = {'continue_url': CALLBACK + '?code=fromJson77&state=y'}
    assert extract('', '', data) == 'fromJson77'


def test_missing_returns_empty():
    assert extract('https://auth.openai.com/log-in', '<html>没有回调</html>', {}) == ''
