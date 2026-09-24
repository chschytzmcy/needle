# 中文 grounding 扩展 — 业务集成 API 文档

> 目标读者：业务后端 / 应用开发者
> 适用范围：`cn_grounding.py` (Chinese Grounding Plugin)
> 版本：v1.0 (2026-09-24)

---

## 1. 一句话集成

```python
import cn_grounding
cn_grounding.install()
```

装上后，所有 `needle.Needle` 实例自动获得中文 grounding 能力，无需其他改动。

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

下面三个函数在 `import cn_grounding` 后可直接使用，不依赖 monkey-patch：

| 函数 | 签名 | 返回 | 用途 |
|---|---|---|---|
| `_parse_cn_number(text)` | `str -> Decimal \| None` | 解析后的十进制数 / `None` | 业务里抽取中文数字 |
| `_cn_extract_years(text)` | `str -> set[int]` | 年份集合 | 业务里抽取中文日期年份 |
| `_cn_section_to_int(text)` | `str -> int \| None` | 整数值 / `None` | 业务里解析纯中文数字段 |

**示例**：

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
# → 87 passed
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

---

## 6. 错误码与边界

| 场景 | 行为 |
|---|---|
| query 为 `None` | `_source_years/_source_numbers` 返回空集，不抛异常 |
| query 为空串 `""` | 同上 |
| 中文数字混入英文 (`"混入了英文abc"`) | `_parse_cn_number` 返回 `None` |
| 中文数字段截断 (`"三万五"` 后无单位) | 仍能解析：`35000` |
| 同 query 多种格式 (`"2024年3月5日和2025年4月"`) | 返回 `{2024, 2025}` |
| query 无相对词 | `_relative_cue` 返回 False（不误判） |

---

## 7. 性能特征

| 操作 | 复杂度 | 实测耗时 |
|---|---|---|
| `_cn_section_to_int` | O(n)，n=字符数 | < 1 μs |
| `_parse_cn_number` | O(n) + 一次正则 | < 10 μs |
| `_cn_extract_years` | O(n) + 5 次正则 | < 50 μs |
| 完整 `install()` | 3 次属性赋值 | < 100 μs |
| 完整 `uninstall()` | 3 次属性还原 | < 100 μs |

对典型 query（< 100 字符），整条 grounding 路径增加耗时 < 1 ms。

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

---

## 12. 支持与反馈

- 项目仓库：`git@github.com:chschytzmcy/needle.git`
- 测试报告：`TEST_REPORT.md`
- 源码：`cn_grounding.py`（283 行，含详细注释）
- Docker 部署：`Dockerfile` / `docker-compose.yml` / `docker/`（见 4.5 节）
- 测试用例：`tests/test_cn_grounding.py` (41) + `tests/test_cn_grounding_integration.py` (14)