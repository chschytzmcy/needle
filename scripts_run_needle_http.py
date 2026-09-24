"""needle HTTP 服务 —— 纯 tool-call 提取后端。

契约:
    GET  /health   → 200 {"status":"ok","model_loaded":true,"version":"3.0.1"}
                     503 暖机未完成 (probe 视为 down → 调用侧 circuit breaker 保持)
    POST /extract  → body {"query": str, "tools": [ToolSchema...],
                           "system": str?, "max_new_tokens": int?}
                     200 {"type","function_calls","confidence","reasoning",
                          "validation","latency_ms"}
                     422 body/tool schema 非法
                     503 engine 忙 (锁超时)

设计:
  - needle 只 complete() 提取, 不 run() 执行 —— 工具执行留在调用方
    (realm check / PII re-check / audit 不绕过)
  - 中文策略 = **仅 grounding 安全网 + P1 数字归一化**:
      P1  cn_grounding.normalize_cn_numbers 规则归一化 ("三十"→"30", 单字/量词跳过)
      cn_grounding.install() 让中文数字/日期/相对时间参与 grounding,
      引擎捏造的值一律进 validation.ungrounded, 不会静默放行。
    needle3 引擎以英文训练, 纯中文 query 的工具选择不做保证 ——
    **翻译不属于本服务职责**, 由调用方在进入 /extract 前自行决定。
  - engine 全局单例 + threading.Lock 串行化 (libneedle C 库非线程安全)
  - agent 按 (tools_json, system) 缓存, tools 不变时复用, 避免每请求 re-init
  - 不记 query 内容 (隐私优先; 只 log 方法/路径/状态/延迟)

本地环境小修小补 (同 scripts_run_playground.py):
  1. ENGINE_VERSIONS[3] 3.0.2 → 3.0.1 (HF 上 3.0.2 wheel 不存在)
  2. cn_grounding.install() (中文数字/日期/相对时间 grounding)
  3. NEEDLE_TELEMETRY=0 + DO_NOT_TRACK=1 (匿名遥测关闭, 隐私项目必须)

依赖: pip install "cactus-needle[http]"  (fastapi + uvicorn)

启动:
  python3 scripts_run_needle_http.py
  python3 scripts_run_needle_http.py --port 8081 --host 127.0.0.1
"""
from __future__ import annotations

import os

# 遥测必须在 import needle 之前关 (module 加载时读 env)
os.environ.setdefault("NEEDLE_TELEMETRY", "0")
os.environ.setdefault("DO_NOT_TRACK", "1")

import argparse
import json
import logging
import re
import sys
import threading
import time

# ── 1. 修引擎版本号 (HF 只 ship 3.0.0/3.0.1 wheel) ──────────────────
from needle.agent import fetch as _fetch

if _fetch.ENGINE_VERSIONS[3] == "3.0.2":
    print("[boot] patching fetch.ENGINE_VERSIONS[3]: 3.0.2 -> 3.0.1 "
          "(HF Cactus-Compute/needle3 only ships 3.0.0/3.0.1 wheels)",
          file=sys.stderr)
    _fetch.ENGINE_VERSIONS[3] = "3.0.1"

# ── 2. 中文 grounding 补丁 + P1 归一化 ───────────────────────────────
import cn_grounding

cn_grounding.install()
print(f"[boot] cn_grounding installed: {cn_grounding.is_installed()}",
      file=sys.stderr)

import needle
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MAX_AGENTS_CACHED = 4          # LRU-ish: 超过即清, 防 tools 组合爆炸占内存
LOCK_TIMEOUT_S = 10.0          # 单请求最长排队; 超时 → 503 (客户端自己也有超时)

_engine_lock = threading.Lock()
_agents: dict[tuple[str, str], "needle.Needle"] = {}
_agents_order: list[tuple[str, str]] = []
_ready = threading.Event()
_extract_count = 0
_last_error: str | None = None

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")

log = logging.getLogger("needle-http")
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s %(message)s")


def _get_agent(tools_json: str, system: str) -> "needle.Needle":
    """按 (tools_json, system) 取/建 agent。调用方必须持锁。"""
    key = (tools_json, system)
    agent = _agents.get(key)
    if agent is None:
        agent = needle.Needle(tools=tools_json, system=system or None,
                              stateless=True)
        _agents[key] = agent
        _agents_order.append(key)
        while len(_agents_order) > MAX_AGENTS_CACHED:
            old = _agents_order.pop(0)
            stale = _agents.pop(old, None)
            if stale is not None:
                stale.close()
    return agent


app = FastAPI(title="needle-http", version="1.1", docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    if not _ready.is_set():
        return JSONResponse(status_code=503, content={
            "status": "loading", "model_loaded": False,
            "version": needle.__version__})
    return {"status": "ok", "model_loaded": True,
            "version": needle.__version__,
            "cn_grounding": cn_grounding.is_installed(),
            "extract_count": _extract_count,
            "last_error": _last_error}


@app.post("/extract")
async def extract(request: Request):
    global _extract_count, _last_error
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(status_code=422,
                            content={"error": "body must be valid JSON"})
    if not isinstance(body, dict):
        # body 是合法 JSON 但不是对象 (如 `null` / `[...]` / `42`)
        return JSONResponse(status_code=422,
                            content={"error": "body must be a JSON object"})
    query = body.get("query")
    tools = body.get("tools")
    if not isinstance(query, str) or not query:
        return JSONResponse(status_code=422,
                            content={"error": "'query' (non-empty str) required"})
    if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
        return JSONResponse(status_code=422,
                            content={"error": "'tools' must be a list of schema objects"})
    system = body.get("system") or ""
    max_new_tokens = int(body.get("max_new_tokens") or 512)

    # ── P1: 中文数字规则归一化 ("三十"→"30"), 零模型零网络 ──
    # 仅含 CJK 时跑; 函数本身保守, 单字量词/人名/年份逐字链不动。
    if _CJK_RE.search(query):
        query = cn_grounding.normalize_cn_numbers(query)

    tools_json = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))

    acquired = _engine_lock.acquire(timeout=LOCK_TIMEOUT_S)
    if not acquired:
        return JSONResponse(status_code=503,
                            content={"error": "engine busy", "retry_after_s": 1})
    try:
        start = time.monotonic()
        try:
            agent = _get_agent(tools_json, system)
            response = agent.complete(query, max_new_tokens)
        except Exception as exc:
            _last_error = str(exc)[:200]
            log.warning("extract failed: %s", exc)
            return JSONResponse(status_code=502,
                                content={"error": f"engine error: {exc}"})
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        _extract_count += 1
        # 不 log query 内容, 只 log 规模/结果形状
        log.info("extract ok calls=%d conf=%s latency_ms=%s",
                 len(response.get("function_calls") or []),
                 response.get("confidence"), latency_ms)
        response["latency_ms"] = latency_ms
        return response
    finally:
        _engine_lock.release()


def _warmup():
    """后台线程加载 engine + 一次 dummy complete, 让首个真请求即热。"""
    global _last_error
    try:
        start = time.monotonic()
        with _engine_lock:
            agent = _get_agent("[]", "")
            agent.complete("hello")   # 触发 engine load + needle_init + 一次推理
            agent.reset()
        log.info("warmup done in %.1fs", time.monotonic() - start)
        _ready.set()
    except Exception as exc:
        _last_error = str(exc)[:200]
        log.error("warmup FAILED: %s", exc)
        # /health 保持 503 → 调用方 boot probe 判 down, breaker 正确打开


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    import uvicorn
    thread = threading.Thread(target=_warmup, daemon=True, name="needle-warmup")
    thread.start()
    print(f"[boot] needle-http http://{args.host}:{args.port}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
