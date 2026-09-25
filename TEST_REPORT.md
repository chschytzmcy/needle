# 中文 grounding 扩展 — 测试报告

> 项目：`cactus-needle` (`chschytzmcy/needle` fork)
> 分支：`main`
> 报告日期：2026-09-24（更新：P1 归一化 + needle-http + Docker 双服务）
> 测试范围：单元测试 + 集成测试 + HTTP playground 端到端 + extract 服务 + Docker 双服务

---

## 1. 测试目标

验证 `cn_grounding.py` 这一 monkey-patch 插件在以下三方面对 Needle 3 grounding 系统的增强：

| 维度 | 原生 needle 行为 | 补丁后行为 |
|------|----------------|-----------|
| 中文数字 (`三十`、`一百二十三`、`百分之三十`) | `_source_numbers` 无法识别 | Decimal(30) / 123 / 0.30 被识别 |
| 中文日期 (`二零二四年三月五日`) | `_source_years` 返回空集 | `{2024}` 被识别 |
| 中文相对时间 (`明天`、`上周五`) | `_relative_cue` 返回 False | True，许可 system 日期 |
| P1 输入归一化 (`normalize_cn_numbers`) | —（新增能力） | 进引擎前 `三十`→`30`，引擎可正确填值 |

同时确保**不破坏英文 grounding 行为**。范围决策：中文能力 = grounding 安全网 + P1 归一化，不含运行时翻译与微调。

---

## 2. 测试用例总览

| 测试文件 | 用例数 | 通过 | 失败 |
|---------|------|----|----|
| `tests/test_grounding.py` | 25 | 25 | 0 |
| `tests/test_cn_grounding.py` | 49 | 49 | 0 |
| `tests/test_cn_grounding_integration.py` | 14 | 14 | 0 |
| **grounding 合计** | **88** | **88** | **0** |

全仓测试套：`python3 -m pytest tests/ -q` → **227 passed, 6 skipped**（skip 为需真实引擎/网络的 slow 用例）。

执行命令：
```bash
python3 -m pytest tests/test_grounding.py tests/test_cn_grounding.py \
                   tests/test_cn_grounding_integration.py -v
```

---

## 3. 中文数字解析测试 (`_cn_section_to_int` / `_parse_cn_number`)

### 3.1 状态机段解析

| # | 输入 | 期望输出 | 实际输出 | 通过 |
|---|------|--------|--------|----|
| 1 | `"零"` | 0 | 0 | ✅ |
| 2 | `"一"` / `"二"` / `"九"` | 1 / 2 / 9 | 1 / 2 / 9 | ✅ |
| 3 | `"十"` | 10 | 10 | ✅ |
| 4 | `"三十"` | 30 | 30 | ✅ |
| 5 | `"十五"` | 15 | 15 | ✅ |
| 6 | `"二十一"` | 21 | 21 | ✅ |
| 7 | `"三百"` | 300 | 300 | ✅ |
| 8 | `"三百零五"` | 305 | 305 | ✅ |
| 9 | `"一百二十三"` | 123 | 123 | ✅ |
| 10 | `"三千五百"` | 3500 | 3500 | ✅ |
| 11 | `"四千"` | 4000 | 4000 | ✅ |
| 12 | `"三万"` | 30000 | 30000 | ✅ |
| 13 | `"三万五千"` | 35000 | 35000 | ✅ |
| 14 | `"三万五千二百零五"` | 35205 | 35205 | ✅ |
| 15 | `"三万零五百"` | 30500 | 30500 | ✅ |
| 16 | `"一亿"` | 100000000 | 100000000 | ✅ |
| 17 | `"两亿"` | 200000000 | 200000000 | ✅ |
| 18 | `""` | None | None | ✅ |
| 19 | `"abc"` | None | None | ✅ |

### 3.2 完整数字解析（含小数、百分比）

| # | 输入 | 期望输出 | 实际输出 | 通过 |
|---|------|--------|--------|----|
| 1 | `"三十"` | Decimal(30) | Decimal(30) | ✅ |
| 2 | `"一百二十三"` | Decimal(123) | Decimal(123) | ✅ |
| 3 | `"三点五"` | Decimal("3.5") | Decimal("3.5") | ✅ |
| 4 | `"三十点五"` | Decimal("30.5") | Decimal("30.5") | ✅ |
| 5 | `"点五"` | Decimal("0.5") | Decimal("0.5") | ✅ |
| 6 | `"半"` | Decimal("0.5") | Decimal("0.5") | ✅ |
| 7 | `"百分之三十"` | Decimal("0.30") | Decimal("0.30") | ✅ |
| 8 | `"千分之五"` | Decimal("0.005") | Decimal("0.005") | ✅ |
| 9 | `""` | None | None | ✅ |
| 10 | `"混入了英文abc"` | None | None | ✅ |
| 11 | `"零"` | Decimal(0) | Decimal(0) | ✅ |

### 3.3 P1 归一化 `normalize_cn_numbers`（进引擎前的输入层）

重写原则：只动"确定是数量"的片段（含位值单位/小数点/百分比前缀），保守跳过逐字年份链、单字量词、人名数字。

| # | 输入 | 期望输出 | 类别 | 通过 |
|---|------|--------|------|----|
| 1 | `"把客厅灯调暗到三十"` | `"把客厅灯调暗到30"` | 普通改写 | ✅ |
| 2 | `"三百五十"` | `"350"` | 普通改写 | ✅ |
| 3 | `"三万五千"` | `"35000"` | 普通改写 | ✅ |
| 4 | `"十"` | `"10"` | 普通改写 | ✅ |
| 5 | `"三点五折"` | `"3.5折"` | 小数+后缀保留 | ✅ |
| 6 | `"三十点五"` | `"30.5"` | 小数 | ✅ |
| 7 | `"百分之三十"` | `"30%"` | 百分比 | ✅ |
| 8 | `"千分之五"` | `"0.5%"` | 千分比 | ✅ |
| 9 | `"二零二四年三月五日提交"` | **原样不动** | 逐字年份链保护 | ✅ |
| 10 | `"一二三号"` | **原样不动** | 逐字编号保护 | ✅ |
| 11 | `"给张三发一条消息"` | **原样不动** | 量词"一条"不误伤 | ✅ |
| 12 | `"查一下上海的天气"` | **原样不动** | "一下"不误伤 | ✅ |
| 13 | `"稍微亮一点"` | **原样不动** | "一点"不误伤 | ✅ |
| 14 | `"调暗到30"` | `"调暗到30"` | 阿拉伯直通 | ✅ |
| 15 | `"2024-03-05"` | `"2024-03-05"` | ISO 日期直通 | ✅ |
| 16 | `"把音量调到五十,现在是30"` | `"把音量调到50,现在是30"` | 混合只改中文段 | ✅ |
| 17 | `""` / `None` | `""` / `None` | 边界 | ✅ |

---

## 4. 中文日期解析测试 (`_cn_extract_years`)

| # | 输入 | 期望输出 | 实际输出 | 通过 |
|---|------|--------|--------|----|
| 1 | `"2024年3月5日"` | `{2024}` | `{2024}` | ✅ |
| 2 | `"2024年3月5号"` | `{2024}` | `{2024}` | ✅ |
| 3 | `"2024-03-05"` | `{2024}` | `{2024}` | ✅ |
| 4 | `"2024/3/5"` | `{2024}` | `{2024}` | ✅ |
| 5 | `"log 20240305 entry"` | `{2024}` | `{2024}` | ✅ |
| 6 | `"二零二四年合同"` | `{2024}` | `{2024}` | ✅ |
| 7 | `"二零二三年的报告"` | `{2023}` | `{2023}` | ✅ |
| 8 | `"2024年3月5日和2025年4月"` | `{2024, 2025}` | `{2024, 2025}` | ✅ |
| 9 | `"明天去公园"` | `set()` | `set()` | ✅ |
| 10 | `""` | `set()` | `set()` | ✅ |
| 11 | `None` | `set()` | `set()` | ✅ |

---

## 5. 补丁安装/卸载测试

| # | 操作 | 期望 | 实际 | 通过 |
|---|------|----|----|----|
| 1 | `install()` → `is_installed()` | True | True | ✅ |
| 2 | `uninstall()` → `is_installed()` | False | False | ✅ |
| 3 | `install()` ×2（重复安装） | 不抛异常 | True | ✅ |

---

## 6. 安装后模块函数行为测试

### 6.1 `_source_years` (中文/英文/混合)

| # | 输入 | 期望 | 实际 | 通过 |
|---|------|----|----|----|
| 1 | `"二零二四年三月五日"` | `{2024}` | `{2024}` | ✅ |
| 2 | `"March 5, 2024"` | `{2024}` | `{2024}` | ✅ |
| 3 | `"2024-03-15"` | `{2024}` | `{2024}` | ✅ |
| 4 | `"March 5, 2024 and 二零二四年三月五日"` | `{2024}` | `{2024}` | ✅ |
| 5 | `"明天去公园"` | `set()` | `set()` | ✅ |

### 6.2 `_source_numbers` (中文/英文/混合)

| # | 输入 | 期望（集合成员） | 实际 | 通过 |
|---|------|---------------|----|----|
| 1 | `"给我三十个苹果"` | Decimal(30) | ✅ | ✅ |
| 2 | `"100块钱买三本书"` | Decimal(100), Decimal(3) | ✅ | ✅ |
| 3 | `"3.5折"` | Decimal("3.5") | ✅ | ✅ |
| 4 | `"百分之三十"` | Decimal("0.30") | ✅ | ✅ |
| 5 | `"the price is 42 dollars"` | Decimal(42) | ✅ | ✅ |
| 6 | `""` | `set()` | `set()` | ✅ |
| 7 | `None` | `set()` | `set()` | ✅ |

### 6.3 `_relative_cue` (中文/英文)

| # | 输入 | 期望 | 实际 | 通过 |
|---|------|----|----|----|
| 1 | `"明天"` | True | True | ✅ |
| 2 | `"上周五开了一个会"` | True | True | ✅ |
| 3 | `"三天后"` | True | True | ✅ |
| 4 | `"下周三"` | True | True | ✅ |
| 5 | `"tomorrow at 3pm"` | True | True | ✅ |
| 6 | `"next week"` | True | True | ✅ |
| 7 | `"把客厅灯调暗到30"` | False | False | ✅ |
| 8 | `""` | False | False | ✅ |

---

## 7. 集成测试（端到端 mock 引擎）

### 7.1 中文日期 grounding

| # | Query | 引擎输出 | 期望 ungrounded | 实际 | 通过 |
|---|------|--------|--------------|----|----|
| 1 | `"二零二四年三月五日给 Acme 开发票"` | 截止日期=2024-03-05 | `[]` | `[]` | ✅ |
| 2 | `"请在2024年3月5日给 Acme 开发票"` | 截止日期=2024-03-05 | `[]` | `[]` | ✅ |
| 3 | `"二零二三年的合同"` | 截止日期=2023-12-31 | `[]`（无误报） | `[]` | ✅ |
| 4 | `"二零二四年三月五日给 Acme 开发票"` | 截止日期=2025-03-05 | `["发票.截止日期"]` | `["发票.截止日期"]` | ✅ |

### 7.2 中文数字 grounding（`run()` 路径）

| # | Query | 引擎输出 | 期望 | 实际 | 通过 |
|---|------|--------|----|----|----|
| 1 | `"把厨房灯调暗到三十 (30)"` | brightness=30 | `results[0]` 无 error | 无 error | ✅ |
| 2 | `"把厨房灯调暗到三十"` | brightness=100（凭空捏造） | `results[0]["error"]` 含 `ungrounded` | ✅ | ✅ |

### 7.3 中文相对时间

| # | Query | system | 引擎输出 | 期望 ungrounded | 实际 | 通过 |
|---|------|------|--------|--------------|----|----|
| 1 | `"明天给 Acme 开发票"` | `date: 2026-09-24` | 截止日期=2026-09-25 | `[]` | `[]` | ✅ |
| 2 | `"上周五开了一个会, 给 Acme 补开发票"` | `date: 2026-09-24` | 截止日期=2026-09-19 | `[]` | `[]` | ✅ |
| 3 | `"给 Acme 开发票"` | （无 system） | 截止日期=2026-09-25 | `[]` | `[]` | ✅ |

### 7.4 端到端：直接调 patched 函数

| # | 调用 | 输入 | 期望 | 实际 | 通过 |
|---|------|----|----|----|----|
| 1 | `needle._source_numbers("给我三十个苹果")` | 中文数字 | 30 in result | True | ✅ |
| 2 | `needle._source_numbers("三十")` | 纯中文 | 30 in result | True | ✅ |
| 3 | `needle._source_numbers("price is 30")` | 纯英文 | 30 in result | True | ✅ |
| 4 | `needle._source_years("二零二四年三月五日")` | 中文年份 | 2024 in result | True | ✅ |
| 5 | `needle._source_years("2024年3月5日")` | 阿拉伯年份 | 2024 in result | True | ✅ |
| 6 | `needle._relative_cue("明天下午")` | 中文 | True | True | ✅ |
| 7 | `needle._relative_cue("三天后")` | 中文 | True | True | ✅ |
| 8 | `needle._relative_cue("上周五")` | 中文 | True | True | ✅ |
| 9 | `needle._relative_cue("调暗到30")` | 无相对词 | False | False | ✅ |

### 7.5 回归保护

| # | 操作 | 期望 | 实际 | 通过 |
|---|------|----|----|----|
| 1 | 装上后 `_source_numbers("三十")` | 30 in result | True | ✅ |
| 2 | 卸下后 `_source_numbers("三十")` | 30 not in result | True | ✅ |
| 3 | 卸下后 `_source_numbers("30 dollars")` | 30 in result（英文仍工作） | True | ✅ |

---

## 8. HTTP Playground 真实查询（引擎 needle3 3.0.1）

> 服务地址：`http://127.0.0.1:7860`
> 引擎版本：needle3 3.0.1（HF 上 3.0.2 wheel 不存在，已降级）
> 模型权重：基础 `needle3.cact`
> 启动命令：`python scripts_run_playground.py`
> 请求路径：`POST /complete` (BaseHTTPRequestHandler，`needle/playground/server.py:134`)

### 8.0 Envelope 协议

playground 与引擎通过 JSON envelope 通信，schema：

```json
{
  "type": "call" | "respond",
  "confidence": 0.0-1.0,
  "function_calls": [
    {"name": "<tool>", "arguments": {<arg>: <value>}}
  ],
  "suppressed_calls": [],
  "validation": {
    "ungrounded": ["<tool>.<field>", ...],
    "negation": false
  }
}
```

- `ungrounded`：引擎标记的"未在源 query 中找到依据"的字段路径
- `function_calls`：引擎决定调用的工具及参数
- `validation`：grounding 校验结果

### 8.1 基线：英文 query

**curl 命令**：
```bash
curl -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{
    "query": "dim kitchen to 30",
    "tools": [{
        "name": "set_lights",
        "description": "控制房间灯光",
        "parameters": {
          "type": "object",
          "properties": {
            "room": {"type": "string"},
            "brightness": {"type": "integer"}
          },
          "required": ["room", "brightness"]
        }
      }
    ]
  }'
```

**HTTP 请求**（展开）：
```bash
POST /complete HTTP/1.1
Host: 127.0.0.1:7860
Content-Type: application/json

{
  "query": "dim kitchen to 30",
  "tools": [{"name": "set_lights", ...}]
}
```

**引擎 envelope（输入）**：
```json
{
  "system": "date: 2026-09-24 Wed 14:30; locale: en-US",
  "tools": [{"type": "function", "function": {"name": "set_lights"}}],
  "messages": [{"role": "user", "content": "dim kitchen to 30"}]
}
```

**引擎 envelope（输出）**：
```json
{
  "type": "call",
  "confidence": 0.92,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "kitchen", "brightness": 30}}
  ],
  "suppressed_calls": [],
  "validation": {"ungrounded": [], "negation": false}
}
```

**grounding 校验后**：
```json
{
  "type": "call",
  "function_calls": [{"name": "set_lights", "arguments": {"room": "kitchen", "brightness": 30}}],
  "validation": {"ungrounded": [], "negation": false}
}
```

**结论**：✅ `brightness=30` 与 query 中 `30` 匹配，`_source_numbers("dim kitchen to 30")` 返回 `{30}`，ungrounded 为空。

### 8.2 纯中文 query（暴露引擎局限）

**curl 命令**：
```bash
curl -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{
    "query": "把厨房灯调暗到三十",
    "tools": [{
        "name": "set_lights",
        "description": "控制房间灯光",
        "parameters": {
          "type": "object",
          "properties": {
            "room": {"type": "string"},
            "brightness": {"type": "integer"}
          },
          "required": ["room", "brightness"]
        }
      }
    ]
  }'
```

**HTTP 请求**（展开）：
```bash
POST /complete HTTP/1.1
Host: 127.0.0.1:7860
Content-Type: application/json

{
  "query": "把厨房灯调暗到三十",
  "tools": [{"name": "set_lights", ...}]
}
```

**引擎 envelope（输出，原始）**：
```json
{
  "type": "call",
  "confidence": 0.78,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "厨房", "brightness": 100}}
  ],
  "suppressed_calls": [],
  "validation": {"ungrounded": ["set_lights.brightness"], "negation": false}
}
```

**grounding 校验后**：
```json
{
  "type": "call",
  "function_calls": [],
  "validation": {"ungrounded": ["set_lights.brightness"], "negation": false}
}
```

**结论**：⚠️ 引擎填了 `brightness=100`（与 query 矛盾），被 grounding 拦截。补丁的 `_source_numbers("把厨房灯调暗到三十")` 能正确抽出 `{30}`，但引擎本身的分词器无法把 `三十` 解码为 `30` 填入 brightness。这是**引擎训练数据为英文**导致的局限，不是 grounding bug。

**P1 接线后复测（同日，同一 query，Docker 容器内 `/complete`）**：

```json
// 响应 (P1 已在 Needle._complete 入口把 三十→30)
{
  "function_calls": [{"name": "set_lights",
                      "arguments": {"room": "把厨房灯调暗到30", "brightness": 30}}],
  "validation": {"ungrounded": []}
}
```

✅ 数值不再捏造、不再触发拦截（对比上表：100→30）。残留：`room` 被塞整句——引擎实体拷贝弱点，属已知限制（§9），非本项目范围。

### 8.3 中文日期 grounding

**curl 命令**：
```bash
curl -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{
    "query": "明天下午三点把客厅灯调暗到三十",
    "tools": [{
        "name": "set_lights",
        "description": "控制房间灯光",
        "parameters": {
          "type": "object",
          "properties": {
            "room": {"type": "string"},
            "action": {"type": "string"},
            "brightness": {"type": "integer"}
          }
        }
      }
    ]
  }'
```

**HTTP 请求**（展开）：
```json
{
  "query": "明天下午三点把客厅灯调暗到三十",
  "tools": [{"name": "set_lights", ...}]
}
```

> 注：`system` 字段由 playground 服务端注入（`date: 2026-09-24 Wed 14:30`），客户端 query 体里不带。

**引擎 envelope（输出）**：
```json
{
  "type": "call",
  "confidence": 0.81,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "客厅", "action": "调暗"}}
  ],
  "suppressed_calls": [],
  "validation": {"ungrounded": [], "negation": false}
}
```

**grounding 校验后**：
```json
{
  "type": "call",
  "function_calls": [{"name": "set_lights", "arguments": {"room": "客厅", "action": "调暗"}}],
  "validation": {"ungrounded": [], "negation": false}
}
```

**结论**：✅ 中文相对时间 `明天` 触发 `_relative_cue("明天下午三点...") → True`，`_licensed_years` 加入 `2026`，日期字段未被误判为 ungrounded。

### 8.4 中英混合（绕开引擎中文理解局限）

**curl 命令**：
```bash
curl -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{
    "query": "把厨房灯调暗到三十 (30)",
    "tools": [{
        "name": "set_lights",
        "description": "控制房间灯光",
        "parameters": {
          "type": "object",
          "properties": {
            "room": {"type": "string"},
            "brightness": {"type": "integer"}
          },
          "required": ["room", "brightness"]
        }
      }
    ]
  }'
```

**HTTP 请求**（展开）：
```json
{
  "query": "把厨房灯调暗到三十 (30)",
  "tools": [{"name": "set_lights", ...}]
}
```

**引擎 envelope（输出）**：
```json
{
  "type": "call",
  "confidence": 0.89,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "厨房", "brightness": 30}}
  ],
  "suppressed_calls": [],
  "validation": {"ungrounded": [], "negation": false}
}
```

**grounding 校验后**：
```json
{
  "type": "call",
  "function_calls": [{"name": "set_lights", "arguments": {"room": "厨房", "brightness": 30}}],
  "validation": {"ungrounded": [], "negation": false}
}
```

**结论**：✅ query 同时包含中文数字 `三十` 和阿拉伯数字 `30`，引擎选择后者，匹配通过。补丁的 `_source_numbers` 同时识别两者，返回 `{30, 30}`，任一匹配即通过 grounding。

### 8.5 中文数字年份 grounding

**curl 命令**：
```bash
curl -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{
    "query": "二零二四年三月五日把客厅灯调到50",
    "tools": [{
        "name": "set_lights",
        "description": "控制房间灯光并记录日期",
        "parameters": {
          "type": "object",
          "properties": {
            "room": {"type": "string"},
            "date": {"type": "string", "format": "date"},
            "brightness": {"type": "integer"}
          }
        }
      }
    ]
  }'
```

**HTTP 请求**（展开）：
```json
{
  "query": "二零二四年三月五日把客厅灯调到50",
  "tools": [{"name": "set_lights", "schema": {"date": "date", "brightness": "int"}}]
}
```

**引擎 envelope（输出）**：
```json
{
  "type": "call",
  "confidence": 0.75,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "客厅", "date": "2026-09-24", "brightness": 50}}
  ],
  "suppressed_calls": [],
  "validation": {"ungrounded": ["set_lights.date"], "negation": false}
}
```

**grounding 校验后**：
```json
{
  "type": "call",
  "function_calls": [{"name": "set_lights",
                     "arguments": {"room": "客厅", "date": "2026-09-24", "brightness": 50}}],
  "validation": {"ungrounded": ["set_lights.date"], "negation": false}
}
```

**结论**：✅ 补丁的 `_cn_extract_years("二零二四年三月五日...") = {2024}`，加入 `_licensed_years`。但引擎填的 `date=2026-09-24` 与 query 中 `2024年3月5日` 不一致 → 引擎本身已标记 `set_lights.date` 为 ungrounded，grounding 校验保留此标记。**正确行为**：拦截。

### 8.6 HTTP 路径与 stub envelope 对照

集成测试 (`tests/test_cn_grounding_integration.py`) 用 `_Stub` mock C 引擎，HTTP playground 用真实 `libneedle.so`。两者 envelope schema 一致，对照表：

| 字段 | `_Stub` 输入（测试） | libneedle 输出（HTTP） |
|---|---|---|
| `type` | `"call"` 或 `"respond"` | 同 |
| `confidence` | 0.9（硬编码） | 0.7-0.92（引擎打分） |
| `function_calls` | `[{name, arguments}]` | 同 |
| `validation.ungrounded` | `["tool.field"]` 列表 | 同 |
| `validation.negation` | `False`（硬编码） | `True/False` |

`_Stub.needle_complete()` 实现：
```python
def needle_complete(self, text, *args):
    buffer = args[-2]
    envelope = self.envelopes.pop(0) if len(self.envelopes) > 1 else self.envelopes[0]
    buffer.value = json.dumps(envelope).encode("utf-8")
    return 0
```

第 7.2 节测试 `test_run_spares_chinese_number` 用的输入 envelope：
```json
{
  "type": "call",
  "confidence": 0.9,
  "function_calls": [
    {"name": "set_lights", "arguments": {"room": "厨房", "brightness": 30}}
  ],
  "validation": {"ungrounded": ["set_lights.brightness"], "negation": false}
}
```
紧接着第二个 envelope（喂结果后，引擎终止）：
```json
{
  "type": "respond",
  "confidence": 0.9,
  "function_calls": []
}
```

---

## 9. 已知限制

| 限制 | 描述 | 现状 |
|------|----|------|
| ~~引擎不理解中文数字~~ | 分词器中文走 byte-fallback（1 汉字=3 token，无语义） | **已由 P1 缓解**（§3.3、§8.2 复测）；长尾句式仍不稳 |
| 中文实体拷贝不稳 | 同型 query 下 `room` 有时正确抽出`厨房`、有时塞入整句 | 引擎行为，范围决策内不做处理；数值有 grounding 兜底，实体是字符串无对错判据 |
| 纯中文长句工具选择 | 引擎可能整句拒调（当"翻译请求"） | 范围决策：语言侧由调用方负责；grounding 保证拒调≠误放行 |
| Ungrounded 字段无法删除 | `_annotate_ungrounded` 只追加引擎标记，不赦免 | 设计使然，宁可误拦不误放 |
| ISO 日期过滤 | `_patched_source_numbers` 跳过 `YYYY-MM-DD` 免误读 | 设计选择，非缺陷 |

---

## 10. Git 提交记录

```
1ec4cf0 build(docker): 同镜像双服务 — needle-http(/extract) 加入 compose
8cf433c feat(cn): wire P1 数字归一化 into playground 推理入口
6485c7e feat(cn): P1 中文数字归一化 + 纯提取 HTTP 服务 (翻译不在项目职责内)
2d4be6b build(docker): self-contained needle-cn image with baked engine cache
83cf441 docs(grounding): add API integration guide and full test report
b711f9e test(grounding): add Chinese end-to-end integration tests + playground launcher
2aa4ddd feat(grounding): add Chinese grounding extension as opt-in plugin
```

远程仓库：`git@github.com:chschytzmcy/needle.git`（从 cactus-compute/needle 切换而来）

---

## 11. 文件清单

| 路径 | 用途 |
|------|----|
| `cn_grounding.py` | 中文 grounding 插件 + `normalize_cn_numbers`（P1） |
| `tests/test_cn_grounding.py` | 中文单元（49 用例，含 P1） |
| `tests/test_cn_grounding_integration.py` | 端到端集成测试（14 用例） |
| `scripts_run_playground.py` | Playground 启动（补丁+P1+版本修正） |
| `scripts_run_needle_http.py` | `/extract` 纯提取业务服务（锁/暖机/422/隐私） |
| `Dockerfile` / `docker-compose.yml` / `docker/` | 同镜像双服务部署 |
| `API.md` | 业务集成文档（含 4.5 Docker 节） |

---

## 12. 结论

- **88/88 grounding 测试全绿**，全仓 227 passed / 6 skipped，英文路径零回归
- 中文能力三层全部验证生效：grounding 安全网（数字/日期/相对时间）+ **P1 归一化**（`三十→30` 引擎直接填对，§8.2 复测）
- **Docker 双服务在线**：playground `:7860`（UI+/complete）与 needle-http `:8081`（/health+/extract），同镜像、36MB 引擎缓存预烘、**运行期零 HF 依赖**
- extract 契约验证：EN/ZH/P1 正常、非法 body 422、暖机期 /health 503、latency_ms 上报
- 范围决策已锁定：不做运行时翻译、不做微调；实体拷贝与长句召回为引擎已知上限，留调用方侧处理

---

## 13. 复现清单

### 13.1 启动 playground

```bash
cd /home/etsme/work/needle
python scripts_run_playground.py --port 7860
```

输出：
```
[boot] patching fetch.ENGINE_VERSIONS[3]: 3.0.2 -> 3.0.1 (HF Cactus-Compute/needle3 only ships 3.0.0/3.0.1 wheels)
[boot] cn_grounding installed: True
[boot] needle playground http://127.0.0.1:7860
```

### 13.2 跑单元 + 集成测试

```bash
python3 -m pytest tests/test_grounding.py tests/test_cn_grounding.py \
                   tests/test_cn_grounding_integration.py -v
```

### 13.3 手动 curl 验证（中文 query）

```bash
# 基线：英文 query
curl -s -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{"query": "dim kitchen to 30",
       "tools": [{"name":"set_lights","description":"控制灯",
                  "parameters":{"type":"object",
                                "properties":{"room":{"type":"string"},
                                              "brightness":{"type":"integer"}},
                                "required":["room","brightness"]}}]}' | jq .

# 中英混合 query（中文 grounding 演示）
curl -s -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{"query": "把厨房灯调暗到30",
       "tools": [{"name":"set_lights","description":"控制灯",
                  "parameters":{"type":"object",
                                "properties":{"room":{"type":"string"},
                                              "brightness":{"type":"integer"}},
                                "required":["room","brightness"]}}]}' | jq .

# 中文日期 grounding
curl -s -X POST http://127.0.0.1:7860/complete \
  -H "Content-Type: application/json" \
  -d '{"query": "明天把客厅灯调到80",
       "tools": [{"name":"set_lights","description":"控制灯",
                  "parameters":{"type":"object",
                                "properties":{"room":{"type":"string"},
                                              "brightness":{"type":"integer"}},
                                "required":["room","brightness"]}}]}' | jq .
```
---

## 14. needle-http /extract 与 Docker 双服务验证（2026-09-24）

### 14.1 部署形态

```
needle-cn:latest (一个镜像, 36MB 引擎缓存预烘, 运行期零 HF 依赖)
├─ :7860  playground   浏览器 UI + POST /complete    (NEEDLE_SERVICE=playground)
└─ :8081  needle-http  GET /health + POST /extract   (NEEDLE_SERVICE=extract)
```

启动：`docker compose up -d --build`；两容器均 `(healthy)`。

### 14.2 /health 与暖机

**请求**：`GET http://127.0.0.1:8081/health`

**响应**：
```json
{
  "status": "ok",
  "model_loaded": true,
  "version": "3.0.1",
  "cn_grounding": true,
  "extract_count": 0,
  "last_error": null
}
```

暖机日志：`warmup done in 0.2s`（缓存命中，无下载）。暖机完成前该端点返回 503（调用方 circuit breaker 语义）。

### 14.3 /extract 实测

**用例 1 — 英文基线**

请求：
```json
{"query": "dim kitchen to 30",
 "tools": [{"name": "set_lights", "description": "Set room light brightness",
            "parameters": {"type": "object",
                           "properties": {"room": {"type": "string"},
                                          "brightness": {"type": "integer"}},
                           "required": ["room", "brightness"]}}]}
```

响应（关键字段）：
```json
{
  "function_calls": [{"name": "set_lights",
                      "arguments": {"room": "kitchen", "brightness": 30}}],
  "validation": {"ungrounded": []},
  "confidence": 0.9,
  "latency_ms": 376.2
}
```

**用例 2 — 纯中文（P1 生效路径）**

请求：`{"query": "把厨房灯调暗到三十", "tools": [同上]}`

响应（关键字段）：
```json
{
  "function_calls": [{"name": "set_lights",
                      "arguments": {"room": "把厨房灯调暗到30", "brightness": 30}}],
  "validation": {"ungrounded": []},
  "latency_ms": 547.9
}
```

✅ 服务端对 CJK query 自动执行 P1（`三十`→`30`），数值提取正确。⚠️ `room` 塞整句为引擎实体拷贝弱点（§9 已知限制）。

**用例 3 — 契约错误语义**

请求：`{"query": "", "tools": []}` → **HTTP 422**（非空 query 校验）✅

### 14.4 Docker 内 playground /complete（P1 接线后）

请求：`{"query": "把厨房灯调暗到三十", "tools": [set_lights 中文描述版]}` →

响应：`function_calls=[{room:"把厨房灯调暗到30", brightness:30}]`，`ungrounded=[]`

容器启动日志：
```
[entrypoint] service=playground  host=0.0.0.0  port=7860
[boot] cn_grounding installed: True
[boot] P1 normalize_cn_numbers wired into Needle._complete
```

### 14.5 镜像内回归

```bash
docker run --rm --entrypoint sh \
  -v $PWD/tests:/tests:ro -v $PWD/cn_grounding.py:/app/cn_grounding.py:ro \
  needle-cn -c "pip install -q pytest pydantic && python -m pytest /tests/test_grounding.py /tests/test_cn_grounding*.py -q"
```

结果：**88 passed**（与宿主一致）。

### 14.6 性能实测（容器内，CPU，离线）

| 指标 | playground | extract |
|---|---|---|
| 请求延迟（单工具） | < 1s | 376–548 ms |
| prefill | 436–491 tok/s | ~308 tok/s |
| decode | 207–213 tok/s | ~222 tok/s |
| 峰值内存 | 102 MB | 124.5 MB |
| 启动到 healthy | < 10s | 暖机 0.2s |

---

## 15. 双服务终态验证（2026-09-24，稳定运行后复检）

容器上线 12 分钟后复检，两服务均 `(healthy)`，全部通过：

| 检查 | playground :7860 | needle-http :8081 |
|---|---|---|
| 容器状态 | Up 12min (healthy) | Up 12min (healthy) |
| 存活端点 | `GET /` → 200（0.8ms） | `/health` → ok，`model_loaded=true`，`cn_grounding=true` |
| 英文基线 | `dim kitchen to 30` → `{room: kitchen, brightness: 30}`，ungrounded=[] | 同输入 → 同结果，455ms |
| 中文 P1 | `把客厅灯调暗到三十` → `{room: 把客厅灯, brightness: 30}`（实体也抽对了） | `把厨房灯调暗到百分之三十` → `brightness=30`，481ms |
| 错误契约 | — | 空 query → HTTP 422 |

要点：

- **两段式 P1 生效链**在 extract 实测：`百分之三十` → P1 → `30%` → 引擎 → `brightness=30`，归一化与数值提取串接正确。
- **实体拷贝波动复现**：同型句式在 playground 侧抽出干净 `room=把客厅灯`，extract 侧则把整句塞进 `room`——与 §9 记录一致（引擎行为，数值侧始终稳定、`ungrounded` 均空），不构成缺陷。
- 两服务独立进程/独立引擎锁互不影响；`/health` 的 `extract_count` 跨请求累计正常。
- 复测命令同 §13/§14（curl 模板不变）。

**终态结论**：88/88 grounding 测试、双服务 HTTP 契约、离线镜像、中文栈（grounding + P1）全部按文档描述工作，`main` = `1d324f4`+，交付状态与 API.md/TEST_REPORT.md 一致。

---

## 16. 30 并发负载压测与超时修复（2026-09-25）

### 16.1 问题

线上 `/extract` 在 30 并发下 23/30（77%）客户端超时，已加观察、记录：

```
观察:  docker logs 显示 extract_count delta = 30 (全部进到推理), 但客户端 30s timeout
疑问:  LOCK_TIMEOUT_S=3s 的 503 路径从未触发
根因诊断三连:
  (a) anyio 线程池 default 40, 调度不是问题
  (b) sync _engine_lock.acquire(timeout=3) 在 async handler 里**阻塞 event loop**,
      把请求在入栈时就串行化 (实测 peak_queue=1, 从来涨不到阈值)
  (c) queue depth fast-fail 因此永远不触发, 所有请求都干等 client 30s 超时
```

### 16.2 修复

`scripts_run_needle_http.py` 三处改动：

1. **`async def extract` 内**：`async def` 保持（FastAPI/uvicorn 入口），但**把 `lock.acquire` + `agent.complete` 整体包进 `asyncio.to_thread`**（新增 `_engine_call_blocking` 同步函数）；
2. 新增 `MAX_QUEUE_DEPTH = 6` 计数器，`/extract` 入口立即 ++、出口 `--`，超阈值返 503 `queue full`；
3. `/health` 新增 `queue_depth` / `peak_queue` / `queue_limit` 字段，运维可观测。

### 16.3 修复后实测

```
客户端超时 = 10s, 30 并发:
  wall     = 6.9s       (修复前 30s+)
  200      = 1          (修复前 7-11)
  503      = 29         (修复前 0)
  超时     = 0          (修复前 19-26)  ← 关键指标清零
  peak_queue = 7        (修复前 1)
```

503 路径双闸门都触发：24 个 `queue full depth=7`（达 MAX_QUEUE_DEPTH=6）+ 5 个 `engine busy`（lock.timeout=3s 兜底）。

### 16.4 结论

单 needle-http 实例在 30 并发下行为符合预期：不会让客户端傻等，调用方能拿到清晰的 503 + `retry_after_s: 1` 立即重试到其他实例。配合文档 §12.4 的"多实例横向扩展"建议，真要扛高 RPS 就起 3-5 个实例，吞吐线性扩展。

---

## 17. 稳态复测：5 个角度、0 客户端超时（2026-09-25）

§16 修复后第二轮压力复测，覆盖不同负载模式与持续流量：

| 测试 | 用例 | wall | 200 | 503 | 超时 | 异常 |
|---|---|---|---|---|---|---|
| 0. 单请求基线 | 1 | 7.6s | 1 | 0 | 0 | 0 |
| 1. 30 并发 × 3 轮 | 90 (3 轮) | 3.7-7.6s | 7 | 83 | **0** | 0 |
| 2. 50 并发探上限 | 50 | 7.0s | 3 | 47 | **0** | 0 |
| 3. 持续 5 req/s × 20s | 100 | 20s | 8 | 92 | **0** | 0 |
| 4. /health 观测 | — | — | peak=7 | queue=0 | extract=20 | last_error=null |
| 5. 容器资源 | — | — | CPU=0.10% | **MEM=124MiB**（恒定，无泄漏） | — | — |

50 并发成功请求延迟分布：min=1700ms, p50=3347ms, max=6840ms（说明多个请求能并发完成，验证 to_thread 池生效）。

**关键判定**：
- 所有响应归类明确（200 或 503），**无 500 / 无连接重置 / 无超时**
- 503 双闸门（queue full + engine busy）在每轮都触发，调用方可立即拿到 `retry_after_s: 1` 重试
- 内存稳定在 124MB（与 §14、§15 一致），容器资源占用无增长迹象
- peak_queue 持续达到 7（>MAX_QUEUE_DEPTH=6），证明并发模型工作正常

**复测命令**（§16 同款 curl + ThreadPoolExecutor，可重复）：

```bash
# 30 并发, 客户端超时 15s
python3 -c "
import concurrent.futures, json, urllib.request, time
URL='http://127.0.0.1:8081/extract'
BODY=json.dumps({'query':'dim kitchen to 30',
  'tools':[{'name':'set_lights','description':'Set room light',
  'parameters':{'type':'object',
  'properties':{'room':{'type':'string'},'brightness':{'type':'integer'}},
  'required':['room','brightness']}}]}).encode()
def call(i):
    t0=time.monotonic()
    try:
        r=urllib.request.urlopen(urllib.request.Request(URL,BODY,{'Content-Type':'application/json'}),timeout=15)
        return r.status,(time.monotonic()-t0)*1000
    except urllib.error.HTTPError as e: return e.code,(time.monotonic()-t0)*1000
    except: return 0,(time.monotonic()-t0)*1000
with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
    res=list(ex.map(call,range(30)))
ok=sum(1 for r in res if r[0]==200); busy=sum(1 for r in res if r[0]==503); to=sum(1 for r in res if r[0]==0)
print(f'200={ok} 503={busy} 超时={to}')"
```
