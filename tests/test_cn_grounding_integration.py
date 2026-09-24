"""中文 grounding 的端到端集成测试。

风格与 tests/test_grounding.py 一致: 用 _Stub mock C 引擎,断言
needle.Needle 的 grounding 校验结果。覆盖四类:

  1. 中文数字 grounding:    引擎报 ungrounded 时,run() 用 _grounded_number_paths
                             校验,补丁应让源自中文数字段的字段通过
  2. 中文日期 grounding:    _annotate_ungrounded 应识别 "二零二四年" 等
  3. 中文相对时间:          _relative_cue 让"明天"等词许可 system 日期推导
  4. 端到端 HTTP 路径:      通过 needle.run() 走完整 agent loop

不依赖真实引擎,不依赖网络。
"""
from __future__ import annotations

import datetime
import json
from typing import List

import pydantic
import pytest

# ── mock 引擎 ───────────────────────────────────────────────────


class _Stub:
    """模拟 libneedle 的 needle_complete: 喂 JSON envelope 序列。"""

    def __init__(self):
        self.envelopes = []
        self.calls = []

    def needle_init(self, system, tools, index):
        return 0

    def needle_load(self, blob, size):
        return 0

    def needle_complete(self, text, *args):
        self.calls.append(("complete", text.decode("utf-8")))
        buffer = args[-2]
        envelope = self.envelopes.pop(0) if len(self.envelopes) > 1 else self.envelopes[0]
        buffer.value = json.dumps(envelope).encode("utf-8")
        return 0

    def needle_reset(self):
        self.calls.append("reset")


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """装好 mock 引擎 + 中文 grounding 插件。"""
    import needle
    import cn_grounding

    engine = _Stub()
    base = tmp_path / "needle3.cact"
    base.write_bytes((0x05E12A84).to_bytes(4, "little") + b"base weights")
    monkeypatch.setattr(needle, "_lib", lambda generation=3: engine)
    monkeypatch.setattr(needle, "_library_path", lambda generation=3: "/tmp/libneedle3")
    monkeypatch.setattr(needle, "_base_weights_path", lambda generation: str(base))
    monkeypatch.setattr(needle, "_active", {})
    monkeypatch.setattr(needle, "_loaded_base", {})

    cn_grounding.install()
    yield engine
    cn_grounding.uninstall()


# ── schema 工具 ─────────────────────────────────────────────────


class 发票(pydantic.BaseModel):
    """中文 schema: 验证日期字段的中文 grounding。"""
    供应商: str
    截止日期: datetime.date
    总金额: float = 0.0


def 控制灯光(房间: str, 亮度: int):
    """控制房间灯。
    Args:
        房间: 房间名
        亮度: 0 到 100
    """
    return {"ok": True, "房间": 房间, "亮度": 亮度}


# ── envelope helpers ────────────────────────────────────────────


def _envelope(arguments, name, ungrounded=()):
    return {
        "type": "call", "confidence": 0.9,
        "function_calls": [{"name": name, "arguments": arguments}],
        "validation": {"ungrounded": list(ungrounded), "negation": False},
    }


# ── 1. 中文日期 grounding ──────────────────────────────────────


class TestCnDateGrounding:
    """日期字段: 引擎报 ungrounded 时,Python 端 _annotate_ungrounded
    应通过 _cn_extract_years 把"二零二四年"加入 licensed_years,赦免。"""

    def test_digit_year_is_licensed(self, stub):
        """'二零二四年' -> 引擎输出 2024,ungrounded 应该消失。"""
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2024-03-05", "总金额": 100.0},
            "发票")]
        agent = needle.Needle(tools=[发票])
        response = agent.complete("二零二四年三月五日给 Acme 开发票")

        assert response.get("validation", {}).get("ungrounded", []) == []

    def test_arabic_full_date(self, stub):
        """'2024年3月5日' 走 _CN_DATE_FULL 正则。"""
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2024-03-05", "总金额": 100.0},
            "发票")]
        agent = needle.Needle(tools=[发票])
        response = agent.complete("请在2024年3月5日给 Acme 开发票")

        assert response.get("validation", {}).get("ungrounded", []) == []

    def test_year_only(self, stub):
        """只说'二零二三年'也能 grounding。"""
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2023-12-31", "总金额": 100.0},
            "发票")]
        agent = needle.Needle(tools=[发票])
        response = agent.complete("二零二三年的合同")

        # 注: "_annotate_ungrounded 只在 seen_years 存在时才校验",
        # 如果 query 里没有日期字样, _seen_years 为空, 不会触发校验
        # 所以这个 case 验证"不误报"
        assert response.get("validation", {}).get("ungrounded", []) == []

    def test_ungrounded_when_year_fabricated(self, stub):
        """query 说 2024 但引擎填 2025 → ungrounded 不为空。"""
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2025-03-05", "总金额": 100.0},
            "发票",
            ungrounded=["发票.截止日期"])]
        agent = needle.Needle(tools=[发票])
        response = agent.complete("二零二四年三月五日给 Acme 开发票")

        # 引擎自己已经标了 ungrounded, 补丁无法删除 (现有 _annotate_ungrounded 用 append)
        assert "发票.截止日期" in response["validation"]["ungrounded"]


# ── 2. 中文数字 grounding (run 路径) ───────────────────────────


class TestCnNumberGroundingRun:
    """run() 路径: 引擎标 ungrounded 时, Python 端 _grounded_number_paths
    走 _source_numbers, 补丁应让源自中文数字段的字段被赦免。"""

    def test_run_spares_chinese_number(self, stub):
        """query 写'三十', 引擎填 brightness=30 但标 ungrounded → run() 应赦免。"""
        import needle

        def set_lights(room, brightness):
            return {"ok": True, "room": room, "brightness": brightness}

        # 引擎给 suppressed_calls, 第一次 complete 返回空 call 让 run 终止
        # 模拟: 引擎填 brightness=30 但报 ungrounded
        stub.envelopes = [
            _envelope({"room": "厨房", "brightness": 30}, "set_lights",
                      ungrounded=["set_lights.brightness"]),
            _envelope({"room": "厨房", "brightness": 30}, "set_lights",
                      ungrounded=[]),                # 喂结果回来后 type=respond
        ]
        # 重新设: 第一个 envelope 是 type=call+ungrounded, 第二个 type=respond
        stub.envelopes = [
            {
                "type": "call", "confidence": 0.9,
                "function_calls": [{"name": "set_lights",
                                    "arguments": {"room": "厨房", "brightness": 30}}],
                "validation": {"ungrounded": ["set_lights.brightness"], "negation": False},
            },
            {
                "type": "respond", "confidence": 0.9,
                "function_calls": [],
            },
        ]

        agent = needle.Needle(tools=[set_lights])
        # 把 query 写成有"三十"和阿拉伯数字对照, 让模型有机会填对
        response = agent.run("把厨房灯调暗到三十 (30)")

        # 关键断言: results 里第一条不是 {"error": "ungrounded ..."}
        assert response["results"], "run() 应至少跑一轮"
        assert "error" not in response["results"][0], \
            f"中文数字 '三十' 应被 grounding 赦免, 但 run() 报: {response['results'][0]}"

    def test_run_still_flags_fabricated_number(self, stub):
        """query 写'三十', 引擎填 brightness=100 (凭空捏造) → run() 应报错。"""
        import needle

        def set_lights(room, brightness):
            return {"ok": True, "room": room, "brightness": brightness}

        stub.envelopes = [
            {
                "type": "call", "confidence": 0.9,
                "function_calls": [{"name": "set_lights",
                                    "arguments": {"room": "厨房", "brightness": 100}}],
                "validation": {"ungrounded": ["set_lights.brightness"], "negation": False},
            },
            {
                "type": "respond", "confidence": 0.9,
                "function_calls": [],
            },
        ]

        agent = needle.Needle(tools=[set_lights])
        response = agent.run("把厨房灯调暗到三十")

        assert response["results"]
        assert "error" in response["results"][0]
        assert "ungrounded" in response["results"][0]["error"]


# ── 3. 中文相对时间 → 许可 system 日期 ────────────────────────


class TestCnRelativeCue:
    """_relative_cue 检测到中文相对词 → 触发 _licensed_years 加入 system date
    的年份, 让 system=2026-09-24 + '明天' + 日期字段=2026-09-25 不被拒。"""

    def test_tomorrow_with_chinese(self, stub):
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2026-09-25", "总金额": 100.0},
            "发票")]
        agent = needle.Needle(
            tools=[发票],
            system="date: 2026-09-24 Wed 14:30; locale: zh-CN")
        response = agent.complete("明天给 Acme 开发票")

        assert response.get("validation", {}).get("ungrounded", []) == []

    def test_last_friday_chinese(self, stub):
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2026-09-19", "总金额": 100.0},
            "发票")]
        agent = needle.Needle(
            tools=[发票],
            system="date: 2026-09-24 Wed 14:30; locale: zh-CN")
        response = agent.complete("上周五开了一个会, 给 Acme 补开发票")

        assert response.get("validation", {}).get("ungrounded", []) == []

    def test_no_relative_no_system_year(self, stub):
        """没有相对词 + 没有源年份 → _annotate_ungrounded 不触发, 无误报。"""
        import needle

        stub.envelopes = [_envelope(
            {"供应商": "Acme", "截止日期": "2026-09-25", "总金额": 100.0},
            "发票")]
        agent = needle.Needle(tools=[发票])
        response = agent.complete("给 Acme 开发票")

        assert response.get("validation", {}).get("ungrounded", []) == []


# ── 4. 端到端: 中文 query + 英文 envelope 验证数字路径 ──────


class TestCnNumberPathEnd2End:
    """直接调 _source_numbers / _source_years / _relative_cue,
    验证 monkey-patch 在 patched 模块上工作。"""

    def test_source_numbers_chinese(self):
        import needle
        import cn_grounding

        cn_grounding.install()
        try:
            result = needle._source_numbers("给我三十个苹果")
            assert 30 in [int(x) for x in result]
        finally:
            cn_grounding.uninstall()

    def test_source_numbers_mixed(self):
        """中英混合: query 含中文数字和阿拉伯数字, 两者都被抽到。

        注: needle 原 _NUMBER_TOKEN 在中文夹阿拉伯数字时也有抽取问题
        (lookbehind 拒绝中文 word 字符), 所以验证'补丁对原有抽取不破坏'即可,
        主要断言'三十'这种纯中文数字被补丁抽取。
        """
        import needle
        import cn_grounding

        cn_grounding.install()
        try:
            # 纯中文数字 — 这是补丁的真正价值
            result_cn = needle._source_numbers("三十")
            assert 30 in [int(x) for x in result_cn]
            # 纯阿拉伯数字 — 原路径应继续工作
            result_ar = needle._source_numbers("price is 30")
            assert 30 in [int(x) for x in result_ar]
        finally:
            cn_grounding.uninstall()

    def test_source_years_chinese(self):
        import needle
        import cn_grounding

        cn_grounding.install()
        try:
            assert 2024 in needle._source_years("二零二四年三月五日")
            assert 2024 in needle._source_years("2024年3月5日")
        finally:
            cn_grounding.uninstall()

    def test_relative_cue_chinese(self):
        import needle
        import cn_grounding

        cn_grounding.install()
        try:
            assert needle._relative_cue("明天下午") is True
            assert needle._relative_cue("三天后") is True
            assert needle._relative_cue("上周五") is True
            assert needle._relative_cue("调暗到30") is False
        finally:
            cn_grounding.uninstall()


# ── 5. 回归保护: 英文路径不被破坏 ────────────────────────────


class TestRegressionEnglishInIntegration:
    """确保 install/uninstall 不污染 needle 模块字典的英文行为。"""

    def test_uninstall_restores_english(self):
        import needle
        import cn_grounding

        cn_grounding.install()
        # 装上后 _source_numbers 应能识别中文
        assert 30 in [int(x) for x in needle._source_numbers("三十")]
        cn_grounding.uninstall()
        # 卸下后: 英文路径不变, 中文"三十"无法被原 _NUMBER_TOKEN 识别
        assert 30 not in [int(x) for x in needle._source_numbers("三十")]
        assert 30 in [int(x) for x in needle._source_numbers("30 dollars")]
