"""启动 needle 自带 playground。

这个项目自带的 HTTP 服务叫 playground (`needle/playground/server.py`),
单文件 Flask,带浏览器 UI + 多轮 + 模型热加载 + finetune pipeline。

本地环境小修小补:
  1. needle/agent/fetch.py 里 ENGINE_VERSIONS[3] = "3.0.2" 在 HF 上不存在
     (3.0.2 是上游 bug,HF 上只有 3.0.0 和 3.0.1 wheel)。这里降到 3.0.1。
  2. 装中文 grounding 插件,让 playground 里的中文 query 也工作。

启动:
  python scripts_run_playground.py
  PORT=8080 python scripts_run_playground.py   # 自定义端口
"""
from __future__ import annotations

import sys

# ── 1. 修版本号不匹配 bug (HF 上 3.0.2 wheel 不存在,降到 3.0.1) ──
from needle.agent import fetch as _fetch
if _fetch.ENGINE_VERSIONS[3] == "3.0.2":
    print("[boot] patching fetch.ENGINE_VERSIONS[3]: 3.0.2 -> 3.0.1 "
          "(HF Cactus-Compute/needle3 only ships 3.0.0/3.0.1 wheels)", file=sys.stderr)
    _fetch.ENGINE_VERSIONS[3] = "3.0.1"

# ── 2. 装中文 grounding 插件 ──
import needle
import cn_grounding
cn_grounding.install()
print(f"[boot] cn_grounding installed: {cn_grounding.is_installed()}", file=sys.stderr)

# ── 2b. P1 中文数字归一化接进推理入口 (三十→30, 纯规则 ~0ms) ──
# Needle._complete 是 complete()/run() 的公共漏斗, patch 这一层两条路径都覆盖;
# grounding 补丁管"不放行捏造", P1 管"让引擎本来就能填对"。
import re as _re
_CJK_RE = _re.compile(r"[㐀-鿿]")
_orig_complete = needle.Needle._complete
def _p1_complete(self, text, max_new_tokens=512, **kw):
    if text and _CJK_RE.search(text):
        text = cn_grounding.normalize_cn_numbers(text)
    return _orig_complete(self, text, max_new_tokens, **kw)
needle.Needle._complete = _p1_complete
print("[boot] P1 normalize_cn_numbers wired into Needle._complete", file=sys.stderr)

# ── 3. 调项目自带的 playground CLI (它会拉引擎 + 启 Flask) ──
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=7860)
parser.add_argument("--host", type=str, default="127.0.0.1")
parser.add_argument("--weights", type=str, default=None,
                    help="tuned .cact path; default 是基础 needle3.cact")
args = parser.parse_args()

# 直接调 playground server 而不是走 argparse,免得和 needle CLI 重复解析
from needle.playground.server import main as playground_main
ns = argparse.Namespace(weights=args.weights, port=args.port, host=args.host)
print(f"[boot] needle playground http://{args.host}:{args.port}", file=sys.stderr)
playground_main(ns)
