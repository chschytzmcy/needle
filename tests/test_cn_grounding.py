"""中文 grounding 扩展的单元测试。

测试两个层面:
  1. 独立纯函数 (_cn_section_to_int / _parse_cn_number / _cn_extract_years)
  2. install() 后 needle 模块函数的实际行为

不依赖 C 引擎,纯 Python 测试。
"""
from __future__ import annotations

import decimal

import pytest


# ── 独立解析函数(不依赖 monkey-patch) ─────────────────────────


class TestCnSectionToInt:
    def test_zero(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("零") == 0

    def test_single_digit(self):
        from cn_grounding import _cn_section_to_int
        for ch, val in [("一", 1), ("二", 2), ("九", 9)]:
            assert _cn_section_to_int(ch) == val

    def test_ten(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("十") == 10

    def test_tens(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("三十") == 30
        assert _cn_section_to_int("十五") == 15
        assert _cn_section_to_int("二十一") == 21

    def test_hundreds(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("三百") == 300
        assert _cn_section_to_int("三百零五") == 305
        assert _cn_section_to_int("一百二十三") == 123

    def test_thousands(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("三千五百") == 3500
        assert _cn_section_to_int("四千") == 4000

    def test_wan(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("三万") == 30000
        assert _cn_section_to_int("三万五千") == 35000
        assert _cn_section_to_int("三万五千二百零五") == 35205
        assert _cn_section_to_int("三万零五百") == 30500

    def test_yi(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("一亿") == 100000000
        assert _cn_section_to_int("两亿") == 200000000

    def test_invalid(self):
        from cn_grounding import _cn_section_to_int
        assert _cn_section_to_int("") is None
        assert _cn_section_to_int("abc") is None


class TestParseCnNumber:
    def test_simple_int(self):
        from cn_grounding import _parse_cn_number
        assert _parse_cn_number("三十") == decimal.Decimal(30)
        assert _parse_cn_number("一百二十三") == decimal.Decimal(123)

    def test_decimal(self):
        from cn_grounding import _parse_cn_number
        assert _parse_cn_number("三点五") == decimal.Decimal("3.5")
        assert _parse_cn_number("三十点五") == decimal.Decimal("30.5")
        assert _parse_cn_number("点五") == decimal.Decimal("0.5")

    def test_half(self):
        from cn_grounding import _parse_cn_number
        assert _parse_cn_number("半") == decimal.Decimal("0.5")

    def test_percent(self):
        from cn_grounding import _parse_cn_number
        assert _parse_cn_number("百分之三十") == decimal.Decimal("0.30")
        assert _parse_cn_number("千分之五") == decimal.Decimal("0.005")

    def test_invalid_returns_none(self):
        from cn_grounding import _parse_cn_number
        assert _parse_cn_number("") is None
        assert _parse_cn_number("混入了英文abc") is None

    def test_zero(self):
        from cn_grounding import _parse_cn_number
        assert _parse_cn_number("零") == decimal.Decimal(0)


class TestCnExtractYears:
    def test_arabic_full_date(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("2024年3月5日") == {2024}
        assert _cn_extract_years("2024年3月5号") == {2024}

    def test_iso_date(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("2024-03-05") == {2024}
        assert _cn_extract_years("2024/3/5") == {2024}

    def test_compact_date(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("log 20240305 entry") == {2024}

    def test_digit_year(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("二零二四年合同") == {2024}
        assert _cn_extract_years("二零二三年的报告") == {2023}

    def test_multiple_years(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("2024年3月5日和2025年4月") == {2024, 2025}

    def test_no_year(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("明天去公园") == set()

    def test_empty(self):
        from cn_grounding import _cn_extract_years
        assert _cn_extract_years("") == set()
        assert _cn_extract_years(None) == set()


# ── install / uninstall ─────────────────────────────────────────


class TestInstall:
    def test_install_uninstall_roundtrip(self):
        import needle
        from cn_grounding import install, uninstall, is_installed

        assert not is_installed()
        install()
        assert is_installed()
        uninstall()
        assert not is_installed()

    def test_double_install_safe(self):
        import needle
        from cn_grounding import install, uninstall, is_installed

        install()
        install()                                 # 第二次应该先卸载再装
        assert is_installed()
        uninstall()


# ── 安装后的实际行为 ─────────────────────────────────────────────


@pytest.fixture
def patched():
    """每个测试前后安装/卸载补丁。"""
    import needle
    from cn_grounding import install, uninstall

    install()
    yield needle
    uninstall()


class TestPatchedSourceYears:
    def test_chinese_year(self, patched):
        assert patched._source_years("二零二四年三月五日") == {2024}

    def test_arabic_year_unchanged(self, patched):
        """原英文/阿拉伯年份路径不能被破坏。"""
        assert patched._source_years("March 5, 2024") == {2024}
        assert patched._source_years("2024-03-15") == {2024}

    def test_combined(self, patched):
        assert patched._source_years("March 5, 2024 and 二零二四年三月五日") == {2024}

    def test_no_year(self, patched):
        assert patched._source_years("明天去公园") == set()


class TestPatchedSourceNumbers:
    def test_chinese_int(self, patched):
        assert patched._source_numbers("给我三十个苹果") == {decimal.Decimal(30)}

    def test_chinese_and_arabic(self, patched):
        result = patched._source_numbers("100块钱买三本书")
        assert decimal.Decimal(100) in result
        assert decimal.Decimal(3) in result

    def test_chinese_decimal(self, patched):
        assert patched._source_numbers("3.5折") == {decimal.Decimal("3.5")}

    def test_chinese_percent(self, patched):
        result = patched._source_numbers("百分之三十")
        assert decimal.Decimal("0.30") in result

    def test_pure_arabic_unchanged(self, patched):
        """原阿拉伯数字路径不能被破坏。"""
        assert patched._source_numbers("the price is 42 dollars") == {decimal.Decimal(42)}

    def test_empty(self, patched):
        assert patched._source_numbers("") == set()
        assert patched._source_numbers(None) == set()


class TestPatchedRelativeCue:
    def test_chinese_relative(self, patched):
        assert patched._relative_cue("明天") is True
        assert patched._relative_cue("上周五开了一个会") is True
        assert patched._relative_cue("三天后") is True
        assert patched._relative_cue("下周三") is True

    def test_english_relative_still_works(self, patched):
        assert patched._relative_cue("tomorrow at 3pm") is True
        assert patched._relative_cue("next week") is True

    def test_no_relative(self, patched):
        assert patched._relative_cue("把客厅灯调暗到30") is False
        assert patched._relative_cue("") is False


# ── 不破坏现有 grounding 测试套 ─────────────────────────────────


class TestRegressionEnglishGrounding:
    """确保补丁不会破坏 needle 原有英文 grounding 行为。

    不读 needle 私有 API,而是直接调模块级函数,模拟补丁未装时的等价逻辑。
    """

    def test_arabic_only_date(self, patched):
        # 已有 test_grounding.py 里大量这种 case,这里只抽样
        assert decimal.Decimal(42) in patched._source_numbers("price is 42")

    def test_negative_number(self, patched):
        # 负数通过原 _NUMBER_TOKEN 解析
        result = patched._source_numbers("temp is -5 degrees")
        assert decimal.Decimal(-5) in result

    def test_thousands_separator(self, patched):
        result = patched._source_numbers("total is 1,200.50")
        assert decimal.Decimal("1200.50") in result

    def test_today_english(self, patched):
        assert patched._relative_cue("today") is True
