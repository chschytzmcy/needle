"""中文 grounding 扩展。

解决三个问题：
  1. 中文数字:  "三十" / "一百二十三" / "百分之三十" / "三点五"
  2. 中文日期:  "二零二四年三月五日" / "2024年3月5日" / "2024-03-05"
  3. 中文相对时间: "明天" / "上周五" / "三天后" / "下周三"

设计为 monkey-patch 插件:
  - 不修改 needle 源码
  - install() 后立即生效,uninstall() 还原
  - 保留原英文 grounding 行为,只在结果上"叠加"中文

用法::

    import needle
    import cn_grounding
    cn_grounding.install()
    agent = needle.Needle(tools=[my_tools])
    agent.run("明天下午三点把客厅灯调暗到三十")   # 不再误判 ungrounded

Worker 子进程同样需要在入口调用 install()::

    # needle/_worker.py 的 _child() 开头
    import cn_grounding; cn_grounding.install()
"""
from __future__ import annotations

import decimal
import re

# ──────────────────────────────────────────────────────────────
# 1. 中文数字
# ──────────────────────────────────────────────────────────────

_CN_DIGITS = {
    "零": 0, "〇": 0,
    "一": 1, "壹": 1, "幺": 1,
    "二": 2, "贰": 2, "两": 2, "兩": 2,
    "三": 3, "叁": 3,
    "四": 4, "肆": 4,
    "五": 5, "伍": 5,
    "六": 6, "陆": 6,
    "七": 7, "柒": 7,
    "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}

_CN_UNITS = {
    "十": 10, "拾": 10,
    "百": 100, "佰": 100,
    "千": 1000, "仟": 1000,
    "万": 10000, "萬": 10000,
    "亿": 100000000, "億": 100000000,
}

# 匹配一段连续的中文数字串(含百分比、小数点)
# alternation 顺序很重要: 先匹配 "百分之..." / "千分之..." 这类带前缀的,
# 否则字符类会先吞掉 "百" 字。
_CN_NUM_RE = re.compile(
    r"百分之[零一二三四五六七八九十百千万]{1,15}"
    r"|千分之[零一二三四五六七八九十百千万]{1,15}"
    r"|[零一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟]{1,15}"
    r"(?:点[零一二三四五六七八九]{1,10})?"
)


def _cn_section_to_int(text: str) -> int | None:
    """把 '三千五百' 这种段翻译成 int,失败返回 None。

    状态机: "十" 单独表示 10, "十五"=1*10+5, "三百"=3*100, "三万五"=(0+3)*1e4+5*1e3。
    """
    if not text:
        return None
    if text == "十":
        return 10
    total, current = 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit >= 10000:
                # 万/亿 段: 把"当前段"乘进去,然后清零
                total = (total + current) * unit if current else total * unit
                current = 0
            elif current == 0:
                # "十五" = 1*10 + 5: 单位前没数字,默认 1
                total += unit
            else:
                total += current * unit
                current = 0
        else:
            return None                        # 含非法字符
    return total + current


def _parse_cn_number(text: str) -> decimal.Decimal | None:
    """把中文数字段解析为 Decimal。支持 "三十" "三点五" "半" "百分之三十"。"""
    if not text:
        return None
    if text == "半":
        return decimal.Decimal("0.5")

    pct = re.match(r"(百分之|千分之)(.+)$", text)
    if pct:
        divisor = {"百分之": 100, "千分之": 1000}[pct.group(1)]
        inner = _parse_cn_number(pct.group(2))
        return inner / divisor if inner is not None else None

    if "点" in text:
        int_part, dec_part = text.split("点", 1)
        int_val = _cn_section_to_int(int_part) if int_part else None
        if int_part and int_val is None:
            return None
        dec_str = ""
        for ch in dec_part:
            if ch in _CN_DIGITS:
                dec_str += str(_CN_DIGITS[ch])
            elif ch == "〇" or ch == "零":
                dec_str += "0"
            else:
                return None
        if not dec_str:
            return None
        return decimal.Decimal(int_val or 0) + decimal.Decimal("0." + dec_str)

    result = _cn_section_to_int(text)
    return decimal.Decimal(result) if result is not None else None


# ──────────────────────────────────────────────────────────────
# 2. 中文日期(只负责抽"年份")
# ──────────────────────────────────────────────────────────────

# "2024年3月5日" / "2024年3月5号"
_CN_DATE_FULL = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
# "2024年3月" / "2024年3" -- 仅年+月, 也许可
_CN_DATE_YEAR_MONTH = re.compile(r"(?<!\d)(\d{4})\s*年\s*(?:\d{1,2}\s*月)?(?!\d)")
# 单独 "2024年" (后无月/日)
_CN_YEAR_ONLY = re.compile(r"(?<!\d)(\d{4})\s*年(?![年月日\d])")
# ISO / 斜线分隔的年-月-日
_CN_DATE_ISO = re.compile(r"(?<![0-9])(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?![0-9])")
# 紧凑数字日期 "20240305"
_CN_DATE_COMPACT = re.compile(r"(?<![0-9])(\d{4})(\d{2})(\d{2})(?![0-9])")
# "二零二四年" 这种全中文年份
_CN_YEAR_DIGITS = re.compile(r"([二三四五六七八九零]{4})\s*年")


def _cn_extract_years(text: str) -> set[int]:
    years: set[int] = set()
    if not text:
        return years
    for pat in (_CN_DATE_FULL, _CN_DATE_YEAR_MONTH, _CN_YEAR_ONLY,
                _CN_DATE_ISO, _CN_DATE_COMPACT):
        for m in pat.finditer(text):
            years.add(int(m.group(1)))
    for m in _CN_YEAR_DIGITS.finditer(text):
        digits = "".join(str(_CN_DIGITS.get(c, 0)) for c in m.group(1))
        if len(digits) == 4:
            years.add(int(digits))
    return years


# ──────────────────────────────────────────────────────────────
# 3. 中文相对时间
# ──────────────────────────────────────────────────────────────

_CN_RELATIVE_RE = re.compile(
    r"(今天|今晚|明天|明晚|后天|大后天|昨天|昨晚|前天|前晚|大前天"
    r"|上午|早上|中午|下午|晚上|夜里"
    r"|这周|本周|这月|本月|本年|今年"
    r"|下周|下月|下年|明年"
    r"|上周|上月|上年|去年"
    r"|现在|此刻|马上|立即)"
    r"|[0-9零一二三四五六七八九十百千万]+\s*个?\s*(?:天|日|周|星期|月|年)\s*(?:后|前)"
)


# ──────────────────────────────────────────────────────────────
# 4. 公开 API
# ──────────────────────────────────────────────────────────────

_ORIG_ATTRS = ("_source_years", "_source_numbers", "_relative_cue")


def _fmt_decimal(d: decimal.Decimal) -> str:
    """Decimal → 干净的数字串: '30' 而不是 '3E+1' / '30.00'。"""
    n = d.normalize()
    if n == n.to_integral_value():
        return str(n.quantize(decimal.Decimal(1)))
    return str(n)


def normalize_cn_numbers(text: str) -> str:
    """P1 输入预处理: 中文数字 → 阿拉伯数字(纯规则, ~0ms, 零模型)。

    needle 的英文/数字 grounding 是原生强项, 中文数字不是 —— 与其让
    翻译模型处理数字(小模型高发错误), 不如规则归一化后再进 needle:

      "把客厅灯调暗到三十"      → "把客厅灯调暗到30"
      "百分之三十"              → "30%"
      "三百五十"                → "350"
      "三点五折"                → "3.5折"
      "二零二四年"              → 不动(年份由 _cn_extract_years grounding 兜)

    只重写 _CN_NUM_RE 命中且可解析的片段; 解析失败原样保留。
    与 install() 互补: 归一化让 needle 更容易提取正确,
    grounding patch 让 needle 不误杀正确输出。
    """
    if not text:
        return text

    _UNIT_CHARS = set("十拾百佰千仟万萬亿億")

    def _repl(m: "re.Match[str]") -> str:
        s = m.group(0)
        # 保守归一化: 只重写"确定是数量"的片段 —— 含位值单位(三十/三百五十)、
        # 含小数点(三点五)、或百分比前缀(百分之三十)。
        # 跳过: digit-chain("二零二四"年份逐位念,按位值 parse 得 4 是错的)、
        # 单字数字("一条/一下/一点"是量词/程度,"张三"的"三"是名字成分)——
        # 这些交给 cn_grounding 的 grounding 补丁兜底,不在输入层重写。
        is_pct = s.startswith(("百分之", "千分之"))
        if not (is_pct or ("点" in s) or (set(s) & _UNIT_CHARS)):
            return s
        val = _parse_cn_number(s)
        if val is None:
            return s
        if is_pct:
            return _fmt_decimal(val * decimal.Decimal(100)) + "%"
        return _fmt_decimal(val)

    return _CN_NUM_RE.sub(_repl, text)


def install() -> None:
    """把 needle 的三个 grounding 函数替换为中文增强版本。

    必须在 import needle 之后、首次 needle.Needle() 调用之前执行(虽然实际上
    运行时查找模块字典,任何时机调用都生效)。

    安全可重复: 若已经装过,先 uninstall 再装。
    """
    import needle

    if hasattr(needle, "_orig_source_years"):
        uninstall()

    # 备份原函数,供 uninstall / 调试使用
    needle._orig_source_years = needle._source_years
    needle._orig_source_numbers = needle._source_numbers
    needle._orig_relative_cue = needle._relative_cue

    needle._source_years = _patched_source_years
    needle._source_numbers = _patched_source_numbers
    needle._relative_cue = _patched_relative_cue


def uninstall() -> None:
    """还原 needle 原始 grounding 函数。"""
    import needle
    for orig in _ORIG_ATTRS:
        backup = "_orig" + orig
        if hasattr(needle, backup):
            setattr(needle, orig, getattr(needle, backup))
            delattr(needle, backup)


def is_installed() -> bool:
    """检查补丁是否已安装。"""
    import needle
    return hasattr(needle, "_orig_source_years")


# ──────────────────────────────────────────────────────────────
# 5. 补丁函数(模块内私有,绑定到 needle 模块后变成 needle._source_years 等)
# ──────────────────────────────────────────────────────────────

def _patched_source_years(text: str) -> set[int]:
    import needle
    years = needle._orig_source_years(text)
    years |= _cn_extract_years(text or "")
    return years


def _patched_source_numbers(*sources) -> set[decimal.Decimal]:
    import needle
    result = needle._orig_source_numbers(*sources)
    for src in sources:
        if not src:
            continue
        # 去掉 ISO 日期串,避免中文数字解析器误读 "2024"
        cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", " ", src)
        for m in _CN_NUM_RE.finditer(cleaned):
            val = _parse_cn_number(m.group(0))
            if val is not None:
                result.add(val)
    return result


def _patched_relative_cue(text) -> bool:
    import needle
    return (needle._orig_relative_cue(text)
            or (bool(text) and _CN_RELATIVE_RE.search(text) is not None))


# ──────────────────────────────────────────────────────────────
# 6. 独立验证入口
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    install()
    import needle

    samples = [
        "明天下午三点把客厅灯调暗",
        "二零二四年三月五日提交报告",
        "给我三十个苹果",
        "100块钱买三本书",
        "上周五开了一个会",
        "二零二三年的合同",
        "明天",
        "3.5折",
    ]

    print(f"{'input':40s}  {'years':15s}  {'numbers':25s}  rel?")
    print("-" * 90)
    for s in samples:
        y = needle._source_years(s)
        n = needle._source_numbers(s)
        r = needle._relative_cue(s)
        print(f"{s!r:40s}  {str(sorted(y)):15s}  {str(sorted(n)):25s}  {r}")
