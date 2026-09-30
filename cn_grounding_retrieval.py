"""工具检索预筛层 (方向 1 + 2)。

**为什么需要它**: needle3 是 14M 参数、4096 上下文的英文原生模型。47 个工具
直出时 prefill 就要 2,925(英)/3,907(中) token, 选择准确率实测只有 33%(英)/
3%(中)——模型在"47 选 1"上不可用, 且中文描述走 byte-fallback 无语义。

**本层做什么**: 在把工具集交给模型之前, 先用 BM25 做词法预筛, 三条出口:

    1. **拒答** (top1 < MIN_SCORE)          → 不调模型, 返回空调用
       解决"模型从不说'不'"的问题(负例过度触发 100%)
    2. **直出** (top1 ≥ DIRECT_SCORE 且 gap ≥ DIRECT_GAP 且无必填参数)
       → 不调模型, 直接返回该工具。~0ms, 准确率高于调模型
    3. **降级** (其余)                        → 只把 top-K 交给模型选
       把"47 选 1"降级为"K 选 1", 缓解上下文占满

**标定数据** (42 例真实 etscli 语料, 47 工具全库):

    英文: 负例 top1 最高 3.65, 正例最低 4.35     → 阈值 4.0 处负例 3/3 拦截
    中文: 负例 top1 全为 0.00, 正例最低 4.56     → 同阈值, 3/3 拦截
    gap≥5  时 BM25 top-1 正确率 100%(英)/93%(中)
    gap<2  时塌到 27%(英)/50%(中)                → 这部分交模型

**不是万能**: 词法检索抓不住纯语义改述("把照片存起来" ↔ file_upload)。
BM25 top-5 召回 92%(英)/97%(中), 未召回的会走"全量兜底"(见 RETRIEVAL_FALLBACK)。
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter

# ── 配置 (env 可调) ────────────────────────────────────────────────────────
ENABLED = os.environ.get("NEEDLE_RETRIEVAL", "1") != "0"
# 低于此分 → 拒答。标定: EN 负例最高 3.65 / 正例最低 4.35
MIN_SCORE = float(os.environ.get("NEEDLE_RETRIEVAL_MIN_SCORE", "4.0"))
# 高于此分且 gap 达标 → 直出(不调模型)
DIRECT_SCORE = float(os.environ.get("NEEDLE_RETRIEVAL_DIRECT_SCORE", "10.0"))
DIRECT_GAP = float(os.environ.get("NEEDLE_RETRIEVAL_DIRECT_GAP", "5.0"))
# 直出仅限无必填参数的工具(否则 arguments 为空, 调用方拿不到可用调用)
DIRECT_MAX_REQUIRED = int(os.environ.get("NEEDLE_RETRIEVAL_DIRECT_MAX_REQUIRED", "0"))
# 直出时是否允许带必填参数的工具(会产出 arguments={} 的调用)。
# 这是**产品决策**, 不是技术开关: 调用方能否接受"名字对、参数空"再去追问用户?
#   0 (默认, 安全): 只直出无必填参数的工具, 其余交模型抽参
#   1 (激进)      : 任何高置信匹配都直出; 名字正确率显著提升, 但参数全空
DIRECT_ALLOW_REQUIRED = os.environ.get("NEEDLE_RETRIEVAL_DIRECT_ALLOW_ARGS", "0") == "1"
# 送模型的候选数。BM25 top-5 召回 92%(英)/97%(中)
TOP_K = int(os.environ.get("NEEDLE_RETRIEVAL_TOP_K", "5"))
# 检索无有效结果时是否回退到全量工具集(False = 宁可拒答也不乱选)
FALLBACK_FULL = os.environ.get("NEEDLE_RETRIEVAL_FALLBACK", "0") == "1"

# ── 分词: 英文按词, 中文按字符 bigram (无分词器时的 IR 惯例) ───────────────
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    t = (text or "").lower()
    out = _WORD.findall(t)
    cjk = _CJK.findall(t)
    out += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return out


def tool_text(tool: dict) -> str:
    """工具的可检索文本: 描述 + 名称拆词 + 参数名。"""
    name = tool.get("name", "")
    desc = tool.get("description", "") or ""
    props = ((tool.get("parameters") or {}).get("properties") or {})
    return " ".join([
        desc,
        name.replace("etscli_", "").replace("_", " "),
        " ".join(props.keys()),
    ])


def required_params(tool: dict) -> list[str]:
    return list(((tool.get("parameters") or {}).get("required") or []))


class _BM25:
    __slots__ = ("k1", "b", "N", "tfs", "lens", "avgdl", "idf")

    def __init__(self, docs: dict[str, list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.N = len(docs) or 1
        self.tfs = {n: Counter(d) for n, d in docs.items()}
        self.lens = {n: len(d) for n, d in docs.items()}
        self.avgdl = (sum(self.lens.values()) / self.N) or 1.0
        df = Counter()
        for d in docs.values():
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.N - v + 0.5) / (v + 0.5)) for t, v in df.items()}

    def score(self, query_tokens: set[str], name: str) -> float:
        d = self.tfs.get(name)
        if not d:
            return 0.0
        L = self.lens[name]
        s = 0.0
        for t in query_tokens:
            f = d.get(t, 0)
            if f:
                s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / \
                     (f + self.k1 * (1 - self.b + self.b * L / self.avgdl))
        return s


def _dominant_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def _script_matches(query: str, tools: list[dict]) -> bool:
    """query 与工具描述是否同文种(中/西)。

    跨文种时 BM25 几乎零重叠 —— 但那不代表"没有匹配的工具", 只代表
    **检索器读不懂这个 query**。实测 `--desc-lang cn` 下 24 个英文 case 全部
    被误拒。跨文种必须放弃拒答权, 交回模型。
    """
    q_cjk = _dominant_cjk(query)
    t_cjk = any(_dominant_cjk(t.get("description", "")) for t in tools)
    return q_cjk == t_cjk


# ── 主入口 ────────────────────────────────────────────────────────────────

def preselect(query: str, tools: list[dict]) -> dict:
    """对工具集做词法预筛。

    返回::

        {
          "action": "abstain" | "direct" | "select",
          "tools":  [候选工具],        # action=select 时送给模型
          "tool":   {直出的那个工具} or None,
          "score":  float,             # top1 得分
          "gap":    float,             # top1 - top2
          "ranked": [(name, score), ...],
        }
    """
    empty = {"action": "select", "tools": tools, "tool": None,
             "score": 0.0, "gap": 0.0, "ranked": []}
    if not ENABLED or not tools or not query:
        return empty

    by_name = {t.get("name"): t for t in tools if t.get("name")}
    if len(by_name) < 2:
        return empty                                  # 单工具无须预筛

    bm = _BM25({n: tokenize(tool_text(t)) for n, t in by_name.items()})
    q = set(tokenize(query))
    scored = sorted(((bm.score(q, n), n) for n in by_name), key=lambda x: -x[0])

    top, top_name = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    gap = top - second
    ranked = [(n, round(s, 3)) for s, n in scored[:TOP_K]]

    # 跨文种: 检索器读不懂 query → 放弃拒答/直出权, 全量交回模型。
    # (仅 top-K 收窄仍保留, 词法无重叠时排序不可信, 故用全量)
    if not _script_matches(query, tools):
        return {"action": "select", "tools": tools, "tool": None,
                "score": round(top, 3), "gap": round(gap, 3),
                "ranked": ranked, "note": "script mismatch, retrieval bypassed"}

    # 1. 拒答
    if top < MIN_SCORE:
        return {"action": "abstain", "tools": [], "tool": None,
                "score": round(top, 3), "gap": round(gap, 3), "ranked": ranked}

    # 2. 直出 (高置信 + 参数策略允许)
    if top >= DIRECT_SCORE and gap >= DIRECT_GAP:
        cand = by_name[top_name]
        if DIRECT_ALLOW_REQUIRED or len(required_params(cand)) <= DIRECT_MAX_REQUIRED:
            return {"action": "direct", "tools": [cand], "tool": cand,
                    "score": round(top, 3), "gap": round(gap, 3), "ranked": ranked,
                    "note": ("empty-args" if required_params(cand) else None)}
        # 有必填参数 → 仍要模型抽参, 但候选集可以更小
        return {"action": "select", "tools": [cand], "tool": None,
                "score": round(top, 3), "gap": round(gap, 3), "ranked": ranked}

    # 3. 降级: top-K 送模型
    picked = [by_name[n] for _, n in scored[:TOP_K]]
    if not picked and FALLBACK_FULL:
        picked = tools
    return {"action": "select", "tools": picked, "tool": None,
            "score": round(top, 3), "gap": round(gap, 3), "ranked": ranked}
