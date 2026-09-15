# -*- coding: utf-8 -*-
"""FewType Text Normalization（轻量规则层，无网络、无 LLM）。

在 Whisper 直出 / LLM 输出后统一执行，只做格式规范化，不改变语义：

1. 中文数字 → 阿拉伯数字
   百分之五十 → 50%    一百亿 → 100亿    一千万 → 1000万    二零二五年 → 2025年
   防误伤：单字数字不转（一个人/第一句/十年不碰）；"万一/万万" 等连词不转；
   "三十而立/十四五" 等固定搭配不转。
2. 机构/技术简称大写化（白名单 + 词边界检查，不在表内不碰 → 防误伤）
   who → WHO；ai → AI；品牌名首字母大写（groq → Groq）；i love you 不碰（单字母）。
3. 排版规范化
   - 字母与中文之间补空格（AI模型 → AI 模型）；数字与中文不补（100亿、2025年 保持紧凑）
   - 中文语境下半角逗号/句号/问号/感叹号 → 全角（你好,世界 → 你好，世界）
"""
import re

# ---------------- 中文数字 → 阿拉伯数字 ----------------
_CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3,
              '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_CN_UNITS = {'十', '百', '千', '万', '亿'}
_CN_NUM_RE = re.compile(
    r'[零〇一二三四五六七八九十百千万亿两]+(?:点[零〇一二三四五六七八九]+)?'
)
_PERCENT_RE = re.compile(
    r'百分之([零〇一二三四五六七八九十百千万亿两]+(?:点[零〇一二三四五六七八九]+)?)'
)
# 固定搭配黑名单：整串不转（十四五规划）与 数词+后缀 不转（成语）
_FIXED_WORDS = ('十四五',)
_FIXED_SUFFIX = ('而立', '不惑', '知天命', '花甲', '古稀', '耄耋')


def cn2num(cn: str) -> float:
    """中文数字串 → 数值（支持 十百千万亿 位权、点 小数、逐位念法）。"""
    if '点' in cn:
        whole, _, frac = cn.partition('点')
        result = cn2num(whole) if whole else 0.0
        for i, ch in enumerate(frac):
            result += _CN_DIGITS.get(ch, 0) * (10 ** -(i + 1))
        return result
    # 无位权单位：逐位念法（二零二五 → 2025，一二三 → 123）
    if not any(u in cn for u in _CN_UNITS):
        return float(int(''.join(str(_CN_DIGITS[c]) for c in cn)))
    result = 0.0
    section = 0.0
    number = 0.0
    for ch in cn:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in ('十', '百', '千'):
            number = number or 1
            section += number * {'十': 10, '百': 100, '千': 1000}[ch]
            number = 0.0
        elif ch == '万':
            section = (section + number) * 10000
            result += section
            section = number = 0.0
        elif ch == '亿':
            section = (section + number) * 100000000
            result += section
            section = number = 0.0
    return result + section + number


def format_big(num: float) -> str:
    """数值 → 紧凑字符串：大数保留 万/亿 汉字单位（100亿、1000万、1亿5000万），小数保留。"""
    if num != int(num):
        s = f"{num:.10f}".rstrip('0').rstrip('.')
        return s
    num = int(num)
    if num >= 100000000:
        yi, rest = divmod(num, 100000000)
        return f"{yi}亿{format_big(rest) if rest else ''}"
    if num >= 10000:
        wan, rest = divmod(num, 10000)
        return f"{wan}万{format_big(rest) if rest else ''}"
    return str(num)


def _should_convert(s: str) -> bool:
    """防误伤：只有完整数词才转（单字、万一/万万/亿万 等连词不转）。"""
    if '点' in s:
        return True
    if len(s) >= 2:
        # "万一/万幸/亿万"：以 万/亿 开头且第二位不是 十百千 → 连词/副词，不转
        if s[0] in '万亿' and s[1] not in '十百千':
            return False
        return True
    return False


def _convert_cn_numbers(text: str) -> str:
    # 1) 百分之X → X%（允许单字：百分之百 → 100%）
    text = _PERCENT_RE.sub(
        lambda m: f"{format_big(cn2num(m.group(1)))}%", text)

    # 2) 普通数词（防误伤过滤）
    def _sub(m: re.Match) -> str:
        s = m.group(0)
        if s in _FIXED_WORDS:
            return s
        if not _should_convert(s):
            return s
        # 固定搭配（三十而立/十四五规划）不转
        nxt = text[m.end():m.end() + 3]
        for fx in _FIXED_SUFFIX:
            if nxt.startswith(fx):
                return s
        try:
            return format_big(cn2num(s))
        except Exception:
            return s
    return _CN_NUM_RE.sub(_sub, text)


# ---------------- 缩写大写化（白名单防误伤）----------------
# 全大写：机构组织 + 高频技术缩写。品牌名走首字母大写表。
_ABBR_WORDS = {
    'who', 'un', 'nato', 'nasa', 'imf', 'wto', 'unesco', 'fbi', 'cia',
    'unicef', 'eu', 'opec', 'oecd', 'asean',
    'ai', 'api', 'url', 'http', 'https', 'html', 'css', 'js', 'json', 'sql',
    'usb', 'gpu', 'cpu', 'ram', 'rom', 'ssd', 'hdd', 'pdf', 'tts', 'asr',
    'llm', 'stt', 'nlp', 'ocr', 'gps', 'rfid', 'id', 'ip', 'ui', 'ux',
    'cli', 'gui', 'os', 'pc', 'app', 'vpn', 'dns', 'tcp', 'udp',
    'gpt',
}
_ABBR_TITLE = {
    'groq': 'Groq', 'openai': 'OpenAI', 'chatgpt': 'ChatGPT',
    'deepseek': 'DeepSeek', 'qwen': 'Qwen', 'claude': 'Claude', 'gemini': 'Gemini',
}
_ABBR_RE = re.compile(r'(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])')
# 口头拼字母：H-O-A → HOA（每段仅 1 个字母、至少 3 段；不碰 e-mail / T-shirt 等连字符英文词）
_SPELLED_ABBR_RE = re.compile(r'(?<![A-Za-z])[A-Za-z](?:-[A-Za-z]){2,}(?![A-Za-z])')


def _uppercase_abbr(text: str) -> str:
    def _sub(m: re.Match) -> str:
        w = m.group(0)
        low = w.lower()
        if low in _ABBR_TITLE:
            return _ABBR_TITLE[low]
        return w.upper() if low in _ABBR_WORDS else w
    text = _ABBR_RE.sub(_sub, text)
    # 拼字母形式先去连字符再进白名单流程（H-O-A → HOA；若 HOA 不在表内仍大写连写）
    text = _SPELLED_ABBR_RE.sub(lambda m: m.group(0).replace("-", "").upper(), text)
    return text


# ---------------- 排版规范化 ----------------
_CJK = r'\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff\uac00-\ud7af'
_CJK_PUNCT = r'，。！？；：、""''（）【】《》'

# 去掉中文之间的多余空格（Whisper 识别断断续续语音时会在停顿处加空格）
# 规则：两个 CJK 之间、CJK 与中文标点之间、中文标点与 CJK 之间的空格都去掉
# 注意：中英文之间的空格保留（由 _SPACE_CJK_ALPHA 补全）
_REMOVE_CJK_SPACES = [
    # 用正向先行断言 (?=...)：第二个字符不被消耗，re.sub 一次扫描即可去掉所有连续空格
    # 否则 "我 是 中 国 人" 只会匹配 "我 是" 和 "中 国"，留下 "是 中" 之间的空格
    (re.compile(f'([{_CJK}])\s+(?=[{_CJK}])'), r'\1'),
    (re.compile(f'([{_CJK}])\s+(?=[{_CJK_PUNCT}])'), r'\1'),
    (re.compile(f'([{_CJK_PUNCT}])\s+(?=[{_CJK}])'), r'\1'),
    (re.compile(f'([{_CJK_PUNCT}])\s+(?=[{_CJK_PUNCT}])'), r'\1'),
    # 中文与半角标点之间的空格也去掉（后续 _FULLWIDTH_PUNCT 会把半角转全角）
    (re.compile(f'([{_CJK}])\s+(?=[,\.!?;:])'), r'\1'),
    (re.compile(f'([,\.!?;:])\s+(?=[{_CJK}])'), r'\1'),
]

# 去掉中文之间孤立的单个拉丁字母（Whisper 幻觉：停顿处呼吸声被识别为 b/d/g 等）
# 只匹配：中文 + 可选空格 + 单个拉丁字母 + 可选空格 + 中文/中文标点
# 不碰：中英文混排中的合法单字母（如 B超、维生素B、A轮）——这些有语义上下文
_REMOVE_ISOLATED_LETTER = re.compile(
    # 匹配中文语境中的孤立单个拉丁字母（Whisper 幻觉：停顿处呼吸声被识别为 b/d/g）
    # 规则1：前面是中文/中文标点，后面是标点（全角/半角）—— 最常见的幻觉位置
    # 规则2：前后都是中文，且字母前后有空格包围 —— Whisper 输出中文时拉丁字母前后会加空格
    # 为什么不匹配紧密相连的 B+中文：避免误伤 B超、A型 等合法缩写（它们没有空格）
    # 为什么不匹配结尾的 B：避免误伤"维生素B"等合法用法
    f'(?<=[{_CJK}{_CJK_PUNCT}])\s*([A-Za-z])\s*(?=[{_CJK_PUNCT},.!?;:])'
    f'|(?<=[{_CJK}])\s+([A-Za-z])\s+(?=[{_CJK}])'
)

_SPACE_CJK_ALPHA = [
    (re.compile(f'([{_CJK}])([A-Za-z])'), r'\1 \2'),
    (re.compile(f'([A-Za-z])([{_CJK}])'), r'\1 \2'),
]
_FULLWIDTH_PUNCT = [
    (re.compile(f'([{_CJK}]),(?=[{_CJK}])'), r'\1，'),
    (re.compile(f'([{_CJK}])\\.(?=[{_CJK}])'), r'\1。'),
    (re.compile(f'([{_CJK}])!(?=[{_CJK}])'), r'\1！'),
    (re.compile(f'([{_CJK}])\\?(?=[{_CJK}])'), r'\1？'),
]


def normalize_text(text: str) -> str:
    """统一入口：Unicode空白清理 → 数字 → 缩写大写 → 去中文间多余空格 → 中英补空格 → 中文语境全角标点。"""
    if not text:
        return text
    # 先清理各类 Unicode 空白字符：全角空格　、不间断空格 、零宽空格​‌‍﻿
    # Whisper 中文识别时经常输出全角空格，Python \s 默认不匹配这些
    t = text
    for _ch in ("\u3000", "\xa0", "\u200b", "\u200c", "\u200d", "\ufeff", "\u2009", "\u200a", "\u202f"):
        t = t.replace(_ch, " ")
    # 合并连续多个半角空格为一个
    t = re.sub(r" {2,}", " ", t)
    t = _convert_cn_numbers(t)
    t = _uppercase_abbr(t)
    # 先去掉中文之间的多余空格（Whisper 断断续续识别时会加）
    for pat, rep in _REMOVE_CJK_SPACES:
        t = pat.sub(rep, t)
    # 先把半角标点转全角（确保后面去孤立字母时，半角标点也能被 lookahead 匹配到）
    for pat, rep in _FULLWIDTH_PUNCT:
        t = pat.sub(rep, t)
    # 去掉中文之间孤立的单个拉丁字母（Whisper 幻觉：停顿处呼吸声被识别为 b/d/g）
    t = _REMOVE_ISOLATED_LETTER.sub(r'', t)
    # 再给中英文之间补空格（规范排版）
    for pat, rep in _SPACE_CJK_ALPHA:
        t = pat.sub(rep, t)
    return t
