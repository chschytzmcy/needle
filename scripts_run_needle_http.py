"""needle HTTP 服务 —— 纯 tool-call 提取后端。

契约:
    GET  /health   → 200 {"status":"ok","model_loaded":true,"version":"3.0.1"}
                     503 暖机未完成 (probe 视为 down → 调用侧 circuit breaker 保持)
    POST /extract  → body {"query": str, "tools": [ToolSchema...],
                           "system": str?, "max_new_tokens": int? (default 64)}
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
  - engine 全局单例 + threading.Lock 串行化 (libneedle C 库非线程安全);
    请求排队上限 LOCK_TIMEOUT_S=3s, 超出 → 503 立即重试, 不撞客户端 30s 超时
  - 默认 max_new_tokens=64 (单调用响应通常几十 token 即可); 业务方传 0 等同缺省
  - **吞吐上限**: needle 引擎单进程串行, ~8–12 req/min/实例;
    高并发场景起多实例横向扩展 (Docker compose 复制 needle-http service 即可)
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
import asyncio
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

MAX_AGENTS_CACHED = 6          # LRU-ish: 超过即清, 防 tools 组合爆炸占内存
LOCK_TIMEOUT_S = 3.0           # lock.acquire 兜底超时; 实际串行化在 needle C 引擎内层
                                # 这个锁不等同于单线程互斥, 仅做排队观测 + 兜底 503
MAX_QUEUE_DEPTH = 6            # 队列深度上限: 同时活着的 extract 请求数 (含正在推理的)
                                # 超出 → 503 `queue full`, 调用方立刻重试到其他实例

_engine_lock = threading.Lock()
_agents: dict[tuple[str, str], "needle.Needle"] = {}
_agents_order: list[tuple[str, str]] = []
_ready = threading.Event()
_extract_count = 0
_last_error: str | None = None
_queue_depth = 0                # 当前活的 extract 请求数 (含排队+推理中)
_peak_queue = 0                 # 累计峰值, 观测用

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


app = FastAPI(title="needle-http", version="1.2", docs_url=None, redoc_url=None)


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
            "queue_depth": _queue_depth,
            "peak_queue": _peak_queue,
            "queue_limit": MAX_QUEUE_DEPTH,
            "last_error": _last_error}


@app.post("/extract")
async def extract(request: Request):  # async + asyncio.to_thread 让阻塞 C 调用走线程池
    global _extract_count, _last_error, _queue_depth, _peak_queue
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
    max_new_tokens = int(body.get("max_new_tokens") or 64)

    # ── P1: 中文数字规则归一化 ("三十"→"30"), 零模型零网络 ──
    # 仅含 CJK 时跑; 函数本身保守, 单字量词/人名/年份逐字链不动。
    if _CJK_RE.search(query):
        query = cn_grounding.normalize_cn_numbers(query)

    tools_json = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))

    # ── 队列深度快失败 (避开客户端 30s 撞死) ──
    # _engine_lock 在 fastapi/uvicorn 下不能保证触发 acquire(timeout),
    # 显式计数更可靠; C 引擎仍单线程串行, 这里只是观测+兜底
    global _queue_depth, _peak_queue
    _queue_depth += 1
    if _queue_depth > _peak_queue:
        _peak_queue = _queue_depth
    try:
        if _queue_depth > MAX_QUEUE_DEPTH:
            log.warning("queue full depth=%d limit=%d", _queue_depth, MAX_QUEUE_DEPTH)
            return JSONResponse(status_code=503,
                                content={"error": "queue full",
                                         "depth": _queue_depth,
                                         "limit": MAX_QUEUE_DEPTH,
                                         "retry_after_s": 1})

        # 把锁+推理整体跑在线程池, 避免同步阻塞 event loop (否则 peak_queue ≤ 2)
        try:
            response, err_code, err_body = await asyncio.to_thread(
                _engine_call_blocking, tools_json, system, query, max_new_tokens)
        except Exception as exc:
            _last_error = str(exc)[:200]
            log.warning("extract dispatch failed: %s", exc)
            return JSONResponse(status_code=502,
                                content={"error": f"engine dispatch error: {exc}"})
        if err_code is not None:
            return JSONResponse(status_code=err_code, content=err_body)
        latency_ms = response.pop("_latency_ms", 0)
        _extract_count += 1
        log.info("extract ok calls=%d conf=%s latency_ms=%s",
                 len(response.get("function_calls") or []),
                 response.get("confidence"), latency_ms)
        response["latency_ms"] = latency_ms
        return response
    finally:
        _queue_depth -= 1


def _engine_call_blocking(tools_json: str, system: str,
                          query: str, max_new_tokens: int):
    """线程池内执行: 加锁 + 推理, 返回 (response|None, err_code|None, err_body|None)."""
    acquired = _engine_lock.acquire(timeout=LOCK_TIMEOUT_S)
    if not acquired:
        log.warning("engine busy depth=%d", _queue_depth)
        return None, 503, {"error": "engine busy", "retry_after_s": 1}
    try:
        start = time.monotonic()
        try:
            agent = _get_agent(tools_json, system)
            response = agent.complete(query, max_new_tokens)
        except Exception as exc:
            _last_error = str(exc)[:200]
            log.warning("extract failed: %s", exc)
            return None, 502, {"error": f"engine error: {exc}"}
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        response["_latency_ms"] = latency_ms
        return response, None, None
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
