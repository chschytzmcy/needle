# 中文 grounding 扩展 — 业务集成 API 文档

> 目标读者：业务后端 / 应用开发者
> 适用范围：`cn_grounding.py`（插件）+ `scripts_run_needle_http.py`（HTTP 提取服务）+ Docker 部署
> 版本：v1.1 (2026-09-24)

---

## 1. 两种集成方式

**方式一：进程内嵌（Python 库）**

```python
import cn_grounding
cn_grounding.install()
```

装上后，所有 `needle.Needle` 实例自动获得中文 grounding 能力，无需其他改动。

**方式二：HTTP 服务（推荐生产）**

```bash
docker compose up -d          # :8081 POST /extract — 中文栈(grounding+P1)服务端内置
```

调用方 POST `{query, tools}` 拿结构化 `function_calls`，契约详见 §4.5。
两种方式的中文能力完全一致；范围决策：**运行时翻译与微调不在本项目职责**，纯中文长句的语言侧处理由调用方负责。

---

## 2. 安装

### 2.1 作为项目依赖

把 `cn_grounding.py` 放到你的项目根目录（或 site-packages 下），确保能 `import cn_grounding`。

### 2.2 调用时机

必须在 `import needle` 之后、首次创建 `Needle` 实例之前调用：

```python
import needle                       # 1. 先 import
import cn_grounding
cn_grounding.install()              # 2. 装补丁
agent = needle.Needle(tools=[...])  # 3. 创建实例
```

虽然在 `Needle()` 之后再 install 也能生效（运行时查表），但**强烈建议**按顺序调用，便于代码审计。

### 2.3 验证

```python
from cn_grounding import is_installed
assert is_installed()  # True
```

---

## 3. 公开 API

### 3.1 `install()`

| 项 | 说明 |
|---|---|
| 签名 | `install() -> None` |
| 副作用 | 替换 `needle._source_years` / `needle._source_numbers` / `needle._relative_cue` 三个函数 |
| 幂等性 | 是。重复调用会先 `uninstall()` 再装 |
| 线程安全 | 否。若多线程并发 install/uninstall，需自行加锁 |

### 3.2 `uninstall()`

| 项 | 说明 |
|---|---|
| 签名 | `uninstall() -> None` |
| 副作用 | 还原 `needle` 模块上的三个原始函数 |
| 幂等性 | 是。未安装时调用是 no-op |
| 用途 | 测试间清理、临时禁用、灰度回滚 |

### 3.3 `is_installed()`

| 项 | 说明 |
|---|---|
| 签名 | `is_installed() -> bool` |
| 返回 | `True` 表示补丁已生效 |

### 3.4 独立函数（不依赖 install）

下列函数在 `import cn_grounding` 后可直接使用，不依赖 monkey-patch。**业务侧首先应该用 `normalize_cn_numbers`（P1）**——它是公开的输入归一化：

| 函数 | 签名 | 返回 | 用途 |
|---|---|---|---|
| `normalize_cn_numbers(text)` | `str \| None -> str \| None` | 中文数字已转阿拉伯的文本 | **P1：进 needle/引擎前归一化，让引擎本来就能填对** |
| `_parse_cn_number(text)` | `str -> Decimal \| None` | 解析后的十进制数 / `None` | 业务里抽取中文数字（被 P1 复用） |
| `_cn_extract_years(text)` | `str -> set[int]` | 年份集合 | 业务里抽取中文日期年份 |
| `_cn_section_to_int(text)` | `str -> int \| None` | 整数值 / `None` | 业务里解析纯中文数字段 |

`normalize_cn_numbers` 的保守规则（详见 TEST_REPORT §3.3 的 17 条用例）：

```python
from cn_grounding import normalize_cn_numbers

normalize_cn_numbers("把客厅灯调暗到三十")   # '把客厅灯调暗到30'   ← 位值数量, 改写
normalize_cn_numbers("百分之三十")           # '30%'
normalize_cn_numbers("三点五折")             # '3.5折'             ← 保留后缀
normalize_cn_numbers("二零二四年三月五日")   # 原样                 ← 逐字年份链, 不动(交 grounding)
normalize_cn_numbers("给张三发一条消息")     # 原样                 ← 单字量词/人名, 不动
normalize_cn_numbers("调暗到30")             # 原样                 ← 阿拉伯直通
```

> P1 只解决"数值对不对"，日期年份类刻意不动（`二零二四` 按位值 parse 会错成 `4`），它们由 grounding 补丁（`install()`）在输出侧兜底。

底层解析函数示例：

```python
from cn_grounding import _parse_cn_number, _cn_extract_years

_parse_cn_number("三十")        # Decimal('30')
_parse_cn_number("三点五")      # Decimal('3.5')
_parse_cn_number("百分之三十")  # Decimal('0.30')
_parse_cn_number("abc")         # None

_cn_extract_years("二零二四年三月五日")  # {2024}
_cn_extract_years("2024年和2025年")      # {2024, 2025}
```

---

## 4. 业务集成模式

### 4.1 Web 服务（同步请求）

```python
from fastapi import FastAPI
import needle
import cn_grounding

cn_grounding.install()  # 模块加载时执行一次

app = FastAPI()
agent = needle.Needle(
    tools=[set_brightness, set_color, get_status],
    system="date: 2026-09-24 Wed 14:30; locale: zh-CN",
)

@app.post("/api/lights")
def control_lights(payload: dict):
    result = agent.run(payload["query"])
    return result
```

### 4.2 Playground 启动（生产环境）

参考 `scripts_run_playground.py`，它封装了三件事：
1. 修引擎版本号 (`3.0.2 → 3.0.1`，HF 上 3.0.2 wheel 不存在)
2. 装 `cn_grounding`
3. 启 Flask

```bash
python scripts_run_playground.py --port 8080 --host 0.0.0.0
```

### 4.3 多租户隔离

每个请求一个 `Needle` 实例，补丁在模块级别，只装一次：

```python
import needle
import cn_grounding
cn_grounding.install()  # 全局一次

def handle_request(user_id: int, query: str):
    # 每个请求新建实例，但补丁已生效
    agent = needle.Needle(
        tools=tool_registry[user_id],
        system=f"user_id: {user_id}; date: {today()}",
    )
    return agent.run(query)
```

### 4.4 测试环境清理

pytest fixture 标准模式：

```python
import pytest
import cn_grounding

@pytest.fixture(autouse=True)
def _cn_grounding_per_test():
    cn_grounding.install()
    yield
    cn_grounding.uninstall()
```

### 4.5 Docker 部署（推荐生产形态）

镜像 `needle-cn:latest` 完全自包含：引擎 `libneedle.so` + 权重
`needle3.cact`（约 36 MB）在构建期烘入 `/root/.cache/cactus-needle/v3/3.0.1/`，
**运行期零 HF 依赖**（已实测容器到 huggingface.co 不通仍可正常服务）。

**一个镜像，两种服务**（`NEEDLE_SERVICE` 切换）：

| 服务 | 端口 | 端点 | 面向 |
|---|---|---|---|
| `playground`（默认） | 7860 | 浏览器 UI + `POST /complete` + `/reset` `/load-model` | 人：演示、试 schema |
| `extract` | 8081 | `GET /health` + `POST /extract` | 程序：业务集成后端 |

`extract` 契约：非法 body → 422；engine 忙（锁排队 >10s）→ 503；暖机未完成
`/health` → 503（供调用方 circuit breaker 探测）；不记 query 内容；响应带
`validation.ungrounded` + `latency_ms`。两个服务中文栈一致（grounding + P1）。

#### 文件布局

| 文件 | 用途 |
|---|---|
| `Dockerfile` | python:3.12-slim + needle`[http]` + 中文插件 + 缓存预烘；构建期 sed 把镜像内 `ENGINE_VERSIONS[3]` 钉为 3.0.1（上游 3.0.2 wheel 在 HF 上不存在） |
| `docker-compose.yml` | 双服务：`needle-cn`(7860) + `needle-http`(8081)，各自 healthcheck、restart |
| `docker/entrypoint.sh` | `NEEDLE_SERVICE=playground\|extract` 分支入口；环境变量覆盖 |
| `docker/prepare-cache.sh` | 从本机 `~/.cache/cactus-needle/v3/3.0.1` 刷新构建缓存（不进 git） |

#### 构建与启动

```bash
# 首次: 本机先跑过一次 needle (使 ~/.cache 里有引擎), 再刷新构建缓存
./docker/prepare-cache.sh

# 一键起双服务 (playground + extract)
docker compose up -d --build

# 验证
curl -s http://127.0.0.1:7860/ -o /dev/null -w "%{http_code}\n"        # 200 (UI)
curl -s http://127.0.0.1:8081/health                                    # {"status":"ok",...}
```

#### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `NEEDLE_SERVICE` | `playground` | `playground`（UI + /complete）或 `extract`（/health + /extract 业务后端） |
| `NEEDLE_HOST` | `0.0.0.0` | 监听地址 |
| `NEEDLE_PORT` | playground 7860 / extract 8081 | 监听端口 |
| `NEEDLE_WEIGHTS` | （空=基础权重） | 微调 `.cact` 路径，配合卷挂载：`-v ./tuned.cact:/weights/tuned.cact -e NEEDLE_WEIGHTS=/weights/tuned.cact` |
| `NEEDLE_TELEMETRY` | — | `0` 关闭匿名使用计数（needle-http 脚本内已默认关闭） |

#### HTTP 契约

**playground** `POST /complete`，body `{query, tools}`；响应 envelope 含
`function_calls` / `validation.ungrounded`（中文 grounding 结果）。示例：

```bash
curl -s -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{"query": "把厨房灯调暗到30",
       "tools": [{"name":"set_lights","description":"控制灯",
                  "parameters":{"type":"object",
                                "properties":{"room":{"type":"string"},
                                              "brightness":{"type":"integer"}},
                                "required":["room","brightness"]}}]}'
# → function_calls: [{room: 厨房, brightness: 30}], ungrounded: []
```

**extract** `POST /extract`，body `{query, tools, system?, max_new_tokens?}`；
额外保证：422（非法 body）/ 503（engine 忙、暖机中）/ `latency_ms` 字段；
CJK query 自动过 P1 归一化（`三十`→`30`）。示例：

```bash
curl -s -X POST http://127.0.0.1:8081/extract \
  -H "Content-Type: application/json" \
  -d '{"query": "把厨房灯调暗到三十",
       "tools": [{"name":"set_lights","description":"Set room light brightness",
                  "parameters":{"type":"object",
                                "properties":{"room":{"type":"string"},
                                              "brightness":{"type":"integer"}},
                                "required":["room","brightness"]}}]}'
# → brightness=30 (P1 归一化生效), ungrounded=[], latency_ms≈500
```

#### 镜像内回归

```bash
docker run --rm --entrypoint sh \
  -v $PWD/tests:/tests:ro -v $PWD/cn_grounding.py:/app/cn_grounding.py:ro \
  needle-cn -c "pip install -q pytest pydantic && python -m pytest /tests -q"
# → 88 passed
```

#### 实测性能（CPU，容器内）

| 指标 | 数值 |
|---|---|
| prefill | 436–491 tok/s |
| decode | 207–213 tok/s |
| 峰值内存 | 102 MB |
| 单请求延迟 | < 1 s |
| 容器启动到 healthy | < 10 s |

#### 注意事项

- 宿主 `7860` 若被占（如本地跑过 `scripts_run_playground.py`），先停掉或改 `docker-compose.yml` 端口映射。
- 上游 `ENGINE_VERSIONS[3]="3.0.2"` 的修复在**构建期 sed** 完成；若未来 HF 发布 3.0.2 wheel，删掉该 sed 并同步更新 `scripts_run_playground.py` 的补丁条件即可。
- `docker-cache/` 已在 `.gitignore`，clone 后镜像构建前需先执行 `prepare-cache.sh`（或让容器首拉走 HF，需网络）。

---

## 5. 行为变更对照表

| Query 片段 | 原 needle 行为 | 补丁后行为 |
|----------|--------------|----------|
| `三十` | `_source_numbers` 返回空集 | 返回 `{Decimal(30)}` |
| `百分之三十` | 返回空集 | 返回 `{Decimal('0.30')}` |
| `二零二四年` | `_source_years` 返回空集 | 返回 `{2024}` |
| `明天` | `_relative_cue` 返回 False | 返回 True，触发 system 日期许可 |
| `上周五` | 返回 False | 返回 True |
| `30 dollars` | 返回 `{Decimal(30)}` | 返回 `{Decimal(30)}`（不变） |
| `March 5, 2024` | 返回 `{2024}` | 返回 `{2024}`（不变） |

P1 归一化层（`install()` 后使用 `normalize_cn_numbers`，或经 needle-http / playground 入口自动执行）：

| Query 片段 | 进引擎前 | 效果 |
|---|---|---|
| `把厨房灯调暗到三十` | `把厨房灯调暗到30` | 引擎直接填对 brightness |
| `二零二四年` / `一条` / `张三` | **原样不动** | 由 grounding 输出侧兜底（保守规则，见 §3.4） |

---

## 6. 错误码与边界

**库层**：

| 场景 | 行为 |
|---|---|
| query 为 `None` | `_source_years/_source_numbers` 返回空集，不抛异常 |
| query 为空串 `""` | 同上 |
| 中文数字混入英文 (`"混入了英文abc"`) | `_parse_cn_number` 返回 `None` |
| 中文数字段截断 (`"三万五"` 后无单位) | 仍能解析：`35000` |
| 同 query 多种格式 (`"2024年3月5日和2025年4月"`) | 返回 `{2024, 2025}` |
| query 无相对词 | `_relative_cue` 返回 False（不误判） |

**服务层**（needle-http `/extract`，详见 §13）：

| HTTP 码 | 语义 | 调用方动作 |
|---|---|---|
| 422 | body 非法（query 空 / tools 非数组） | 修请求，不必重试 |
| 503 `engine busy` | 锁排队 >10s | 可重试（带退避） |
| 503 `loading` | `/health` 暖机未完成 | 视为 down，走 breaker |
| 502 | 引擎内部错误 | 记 `last_error`，降级到备用提取 |

---

## 7. 性能特征

**纯函数开销**：

| 操作 | 复杂度 | 实测耗时 |
|---|---|---|
| `_cn_section_to_int` | O(n)，n=字符数 | < 1 μs |
| `_parse_cn_number` | O(n) + 一次正则 | < 10 μs |
| `_cn_extract_years` | O(n) + 5 次正则 | < 50 μs |
| `normalize_cn_numbers` | 一次正则替换 | < 100 μs |
| 完整 `install()` / `uninstall()` | 3 次属性赋值/还原 | < 100 μs |

对典型 query（< 100 字符），整条 grounding + P1 路径增加耗时 < 1 ms。

**服务实测**（Docker 容器内，CPU，离线）：

| 指标 | extract (:8081) | playground (:7860) |
|---|---|---|
| 单请求延迟 | 376–548 ms | < 1 s |
| prefill / decode | ~308 / ~222 tok/s | 436–491 / 207–213 tok/s |
| 峰值内存 | 124.5 MB | 102 MB |
| 暖机 | 0.2s（缓存命中） | < 10s 到 healthy |

---

## 8. 兼容性矩阵

| 组件 | 版本 | 兼容 |
|---|---|---|
| needle | 3.x | ✅ |
| Python | 3.10+ | ✅ |
| PyTorch | 2.0+ | ✅（依赖 needle） |
| 引擎 wheel | libneedle 3.0.1 | ✅（3.0.2 在 HF 上不存在，需降级） |
| 操作系统 | Linux / macOS / Windows | ✅（纯 Python） |

---

## 9. 升级与回滚

### 升级 `cn_grounding.py`

替换文件即可，函数签名向后兼容。如新增函数，旧代码不受影响。

### 回滚到纯英文 grounding

```python
import cn_grounding
cn_grounding.uninstall()
```

无需重启进程，下次 `Needle.run()` 调用立即回到英文行为。

### 灰度发布

按租户/百分比路由：

```python
def should_use_cn(user_id: int) -> bool:
    return hash(user_id) % 100 < 10  # 10% 灰度

def make_agent(user_id):
    agent = needle.Needle(tools=...)
    if should_use_cn(user_id):
        cn_grounding.install()  # 注意：模块级别，会影响其他请求
    return agent
```

> ⚠️ **警告**：`install()` 是模块级别的，全局生效。如需按请求隔离，请改用 `_patched_source_years` 等函数直接调用，把 query 预处理后再传给 needle。

---

## 10. 故障排查

| 现象 | 可能原因 | 排查方法 |
|---|---|---|
| 中文 query 仍报 ungrounded | 1. 未 install；2. 引擎填的值与 query 不一致 | `cn_grounding.is_installed()`；打印 `agent.run` 返回的 `validation.ungrounded` |
| `import cn_grounding` 失败 | 文件路径不在 `sys.path` | `python -c "import sys; print(sys.path)"` |
| 中文数字 `三十` 解析成 None | query 含非法字符（英文/数字夹在中间） | 单独调用 `_parse_cn_number("三十")` 测试 |
| 安装后英文路径异常 | 极少见，多线程竞争 | 重启进程；install/uninstall 加锁 |

---

## 11. 完整示例

```python
"""业务集成示例：智能家居控制后端"""
import datetime
import needle
import cn_grounding
from pydantic import BaseModel

cn_grounding.install()


# ── 工具定义 ──
class ControlLights(BaseModel):
    room: str
    brightness: int  # 0-100
    color: str = "white"


def set_lights(room: str, brightness: int, color: str = "white"):
    """控制房间灯光。
    Args:
        room: 房间名 (kitchen/living_room/bedroom)
        brightness: 亮度 0-100
        color: 颜色
    """
    return {"ok": True, "room": room, "brightness": brightness, "color": color}


# ── 业务入口 ──
def handle_user_query(query: str) -> dict:
    agent = needle.Needle(
        tools=[set_lights],
        system=f"date: {datetime.date.today().isoformat()} locale: zh-CN",
    )
    response = agent.run(query)
    return {
        "calls": response.get("function_calls", []),
        "ungrounded": response.get("validation", {}).get("ungrounded", []),
        "ok": len(response.get("validation", {}).get("ungrounded", [])) == 0,
    }


# ── 测试 ──
if __name__ == "__main__":
    tests = [
        "把厨房灯调暗到三十",
        "明天把客厅灯调到80",
        "二零二六年三月五日下午三点关卧室灯",
        "dim kitchen to 30",  # 英文也工作
    ]
    for q in tests:
        r = handle_user_query(q)
        print(f"Q: {q}")
        print(f"   {r}\n")
```

**HTTP 版**（服务已起时，等价逻辑 3 行）：

```python
import requests

def handle_user_query(query: str, tools: list) -> dict:
    r = requests.post("http://127.0.0.1:8081/extract",
                      json={"query": query, "tools": tools}, timeout=10).json()
    ok = not r.get("validation", {}).get("ungrounded")
    return {"calls": r.get("function_calls", []), "ok": ok}
```

---

## 12. HTTP 接口参考（needle-http，:8081）

### 12.1 `GET /health`

模型未暖机完成前返回 **503**（`{"status":"loading","model_loaded":false}`），
完成后 **200**——直接用作存活探针 / circuit breaker 探测端点。

```json
{
  "status": "ok",
  "model_loaded": true,
  "version": "3.0.1",
  "cn_grounding": true,
  "extract_count": 42,
  "last_error": null
}
```

| 字段 | 说明 |
|---|---|
| `status` | `ok` / `loading` |
| `model_loaded` | 引擎就绪（暖机=加载+1 次 dummy 推理） |
| `cn_grounding` | 中文补丁是否在位（部署自检用） |
| `extract_count` | 成功提取计数 |
| `last_error` | 最近一次引擎错误摘要（≤200 字符） |

### 12.2 `POST /extract`

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | ✅ 非空 | 用户请求；含 CJK 时服务端自动 P1 归一化（`三十`→`30`） |
| `tools` | array\<ToolSchema\> | ✅ 对象数组 | 标准 function schema：`{name, description, parameters(JSON Schema)}` |
| `system` | string | ❌ | 附加系统事实（如 `date: ...`）；缺省时自动注入当前日期 |
| `max_new_tokens` | int | ❌ 默认 512 | 生成上限 |

**响应 200**（实测全字段）：

```json
{
  "type": "call",
  "success": true,
  "error": null, "error_code": null, "reason": null,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "kitchen", "brightness": 30}}
  ],
  "suppressed_calls": [],
  "reasoning": "Query 'dim kitchen to 30' -> ...",
  "confidence": 0.9,
  "prefill_tps": 308.0, "decode_tps": 221.8, "peak_ram_mb": 124.5,
  "validation": {"ungrounded": [], "negation": false},
  "latency_ms": 376.2
}
```

| 字段 | 业务侧处理建议 |
|---|---|
| `function_calls` | 待执行调用列表；**执行权在调用方**（权限/审计/PII 不绕过） |
| `validation.ungrounded` | **非空 ⇒ 对应字段（`tool.field` 路径）无输入依据，不要执行**，走追问或降级 |
| `validation.negation` | true 表示引擎判定请求是否定式（"不要开灯"） |
| `type` | `call`=有调用；`respond`=引擎选择直接答复 |
| `confidence` | 引擎置信度（0-1），可设阈值二次把关 |
| `latency_ms` | 服务端耗时（不含网络），监控用 |

**错误响应**：422 / 503（`engine busy`，含 `retry_after_s`）/ 503（暖机）/ 502，见 §6 服务层表。

### 12.3 决策边界（诚实条款）

- **保证**：数值/日期/相对时间类错误绝不静默放行（grounding 拦截）；P1 后常见指令数值可直接提取。
- **不保证**：纯中文长句的工具选择与实体拷贝（引擎英文训练 + 中文 byte 分词上限，见 TEST_REPORT §9）；语言侧预处理是**调用方职责**（本项目不做运行时翻译）。
- 建议调用方策略：`ungrounded` 非空或 `type!=call` ⇒ fallback 路径（追问 / 上游 LLM 直接提取）。

### 12.4 性能契约

**吞吐上限（单实例）**：needle C 引擎非线程安全，全局 `threading.Lock` 串行化所有 `/extract` 请求。实测：

| 场景 | 单请求 | 单实例吞吐 |
|---|---|---|
| 单调用（dim kitchen to 30） | ~1-3s（含 prefill） | ~20-60 req/min |
| 多调用（turn_on + set_brightness） | ~5-12s | ~5-12 req/min |
| 30 并发压测（client timeout=30s） | — | ~10/30 通过，**77% 客户端超时** |

**关键默认值**：

| 项 | 默认 | 说明 |
|---|---|---|
| `max_new_tokens` | 64 | 单调用响应通常几十 token 足够；业务侧传 0 等同缺省 |
| `LOCK_TIMEOUT_S` | 3s | 服务端排队超过 3s → 503 `engine busy` |
| `MAX_QUEUE_DEPTH` | 6 | 同时在飞的 extract 请求数上限；超出 → 503 `queue full` |

**客户端调用约束**：

- 调用方 **必须** 设客户端超时（建议 5-10s），不要被卡死的请求拖死 worker；
- 收到 503（`engine busy` / `queue full`）必须**立即重试**（带 50-200ms 退避），不要重发同样请求在原队列里；
- 高并发（>10 RPS）场景起**多实例横向扩展**：compose 复制 `needle-http` service 即可，每个实例独立引擎锁，吞吐线性增长；
- 单实例内存 ~125MB，3 实例 ≈ 400MB —— 在边缘盒子也扛得住。

**实现细节**：`extract` 是 async handler，但把「加锁 + engine 推理」整体放进 `asyncio.to_thread` 的线程池里跑，避免 `_engine_lock.acquire` 同步阻塞 event loop——否则请求会在入栈时被串行化，`_queue_depth` 永远涨不到阈值（实测修复前 peak_queue=1，修复后 =7）。

**为什么会有 503**：needle 引擎是单线程串行，客户端 timeout 30s 但服务端 30 个并发排队可能要 60s+。queue depth + lock timeout 双闸门 + asyncio.to_thread 三件事一起，让**排队方快速失败**而不是干等 30s —— 调用方能立刻重试到其他实例（多实例）或换降级路径。

**30 并发实测**（修复后）：wall=6.9s, 200=1, 503=29, 超时=0, peak_queue=7。

---

## 13. 支持与反馈

- 项目仓库：`git@github.com:chschytzmcy/needle.git`
- 测试报告：`TEST_REPORT.md`（§14 含 Docker/extract 验证记录）
- 源码：`cn_grounding.py`（插件 + P1）、`scripts_run_needle_http.py`（提取服务）
- Docker 部署：`Dockerfile` / `docker-compose.yml` / `docker/`（见 §4.5）
- 测试用例：`tests/test_cn_grounding.py`（49）+ `tests/test_cn_grounding_integration.py`（14），全套 227 passed