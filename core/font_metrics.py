# -*- coding: utf-8 -*-
"""浏览器字体度量签名（防字体指纹异常）。

借鉴 sheying2013/OpenBrowser：它对字体子集做**六维严格对齐校验**
（容器/身份、字符覆盖、水平度量、垂直度量、字形轮廓、排版表），
原因很直接——指纹脚本会用 `canvas.measureText` 量文本宽度，
字体一旦被替换或子集化后度量有偏差，测出来的宽度就和真机不同，直接暴露。

我们用的是 CloakBrowser（真 Chromium + 可配置字体），风险点不同但同类：
  1. 容器里装的字体集与目标平台不一致 → 某些字符回退到别的字体；
  2. 不同 profile 之间字体集漂移 → 同一个人设前后测出来的宽度不同。

本模块只做两件事：
  * `measure(driver)`：在页面里量一组固定文本的宽度，得到签名；
  * `compare(baseline, current)`：算最大相对偏差，超过阈值即视为漂移。
"""
from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

#: 采样文本：覆盖拉丁、数字、标点、CJK、emoji 回退
SAMPLES = (
    'The quick brown fox jumps over the lazy dog',
    '0123456789',
    'WAVE-av',
    'iiiiiiiiii',
    'WWWWWWWWWW',
    '中文测试字体宽度',
    'AAAA BBBB CCCC',
)

FONTS = ('system-ui', 'Arial', 'Times New Roman', 'Courier New', 'Microsoft YaHei')

_MEASURE_JS = '''
const samples = arguments[0];
const fonts = arguments[1];
const canvas = document.createElement('canvas');
const ctx = canvas.getContext('2d');
const out = {};
fonts.forEach(function (font) {
  samples.forEach(function (text, index) {
    ctx.font = '16px "' + font + '"';
    out[font + '|' + index] = Math.round(ctx.measureText(text).width * 100) / 100;
  });
});
return out;
'''


def measure(driver) -> dict:
    """在浏览器里量取字体度量，返回 {font|index: width}。"""
    try:
        result = driver.execute_script(_MEASURE_JS, list(SAMPLES), list(FONTS))
    except Exception as exc:
        return {'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}
    return result if isinstance(result, dict) else {'error': 'unexpected result'}


def signature(values: dict) -> str:
    """把度量字典压成短哈希，便于入库比对。"""
    body = json.dumps(values or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def compare(baseline: dict, current: dict, threshold: float = 0.02) -> dict:
    """比较两次度量；返回最大相对偏差与超阈值项。"""
    base = baseline or {}
    cur = current or {}
    if not base or not cur:
        return {'ok': False, 'error': '缺少基准或当前度量'}
    worst = 0.0
    worst_key = ''
    drift = []
    for key, base_value in base.items():
        if key not in cur:
            continue
        try:
            b = float(base_value)
            c = float(cur[key])
        except Exception:
            continue
        if b == 0:
            continue
        ratio = abs(c - b) / abs(b)
        if ratio > worst:
            worst = ratio
            worst_key = key
        if ratio > float(threshold):
            drift.append({'key': key, 'baseline': b, 'current': c,
                          'ratio': round(ratio, 4)})
    return {
        'ok': True,
        'checked': len(base),
        'worst_ratio': round(worst, 4),
        'worst_key': worst_key,
        'drifted': drift[:10],
        'drift_count': len(drift),
        'threshold': float(threshold),
        'verdict': 'drift' if drift else 'match',
    }


def store(path, values: dict) -> bool:
    """把度量写入基准文件。"""
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(values, handle, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def load(path) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle) or {}
    except Exception:
        return {}
