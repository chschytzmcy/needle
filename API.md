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
- 测试用例：`tests/test_cn_grounding.py` (41) + `tests/test_cn_grounding_integration.py` (14)