# 核心模块详解

核心层是 Axiom 的骨架，由 7 个模块组成，共同支撑起代理循环的运行。

---

## 1. `agent.py` — 代理循环核心

**作用**：实现 LLM 驱动的迭代式推理-行动循环（Agent Loop），是整个系统的中枢。

### 核心类：`Agent`

| 方法 | 作用 |
|------|------|
| `chat(user_input, on_token, on_tool)` | 主循环。将用户输入追加到消息列表，调用上下文压缩，然后反复调用 LLM：若 LLM 返回工具调用则执行并回传结果，若返回文本则返回给调用方 |
| `_exec_tool(tc)` | 根据工具名分发单个工具调用，优雅处理异常 |
| `_exec_tools_parallel(tool_calls, on_tool)` | 使用 `ThreadPoolExecutor` 并发执行多个独立工具调用（最多 8 线程） |
| `reset()` | 清空对话历史 |

### 数据流

```
用户输入 → ContextManager.maybe_compress() → LLM.chat() → 
  ├─ 工具调用 → 执行（可并行）→ 追加上下文 → 再次调用 LLM
  └─ 文本回复 → 返回给调用方
```

### 子系统集成

Agent 通过惰性加载（lazy import）集成可选子系统：
- `MemoryManager` — 记忆系统
- `DreamDistillEngine` — 梦与蒸馏
- `GoalJudgeEngine` — 目标评判
- `SkillLoader` — 技能加载

---

## 2. `cli.py` — 命令行接口

**作用**：程序的入口，负责参数解析、配置加载、会话管理和两种运行模式（REPL / 一次性执行）。

### 核心函数

| 函数 | 作用 |
|------|------|
| `_parse_args()` | 使用 argparse 解析命令行参数：`--model`、`--base-url`、`--api-key`、`--prompt`、`--resume`、`--version` |
| `main()` | 从环境变量加载配置，可选覆盖为 CLI 参数，创建 LLM 客户端和 Agent，挂载 GoalJudgeEngine，处理会话恢复，然后分发到一次性执行或 REPL |
| `_run_once(agent, prompt)` | 非交互模式：执行单次提示词并流式输出 token |
| `_repl(agent, config)` | 交互模式：基于 readline 的循环，支持丰富的控制台 I/O |

### 设计特点

- 通过 `LiteLLM` 子类支持 100+ 模型供应商
- 支持 `--resume` 从之前的会话恢复

---

## 3. `config.py` — 配置管理

**作用**：零外部依赖的配置加载，通过环境变量和 `.env` 文件管理。

### 核心类：`Config`

```python
@dataclass
class Config:
    model: str          # 模型名称（如 gpt-4）
    api_key: str        # API 密钥
    base_url: str       # API 基础 URL
    max_tokens: int     # 最大输出 token 数
    temperature: float  # 温度参数
    max_context_tokens: int  # 最大上下文 token 数
    provider: str       # 供应商类型（openai / litellm）
```

### 配置加载顺序

1. 从当前目录向上查找 `.env` 文件并加载
2. 读取环境变量（如 `AXIOM_API_KEY`、`OPENAI_API_KEY` 等）
3. 使用合理的默认值

---

## 4. `context.py` — 上下文管理

**作用**：多层级上下文窗口压缩策略，防止长对话中 token 溢出。

### 核心类：`ContextManager`

| 方法 | 作用 |
|------|------|
| `maybe_compress(messages, llm)` | 在三个阈值（50%、70%、90% 的 `max_tokens`）下依次触发压缩 |
| `_snip_tool_outputs(messages)` | **第一层**：对超过 1500 字符的工具输出，保留前 3 行 + 后 3 行，中间用 `<history snipped>` 替代 |
| `_summarize_old(messages, llm, keep_recent=8)` | **第二层**：使用 LLM 总结较旧的对话轮次，保留最近 8 条消息完整 |
| `_hard_collapse(messages, llm)` | **第三层**：紧急压缩，仅保留最后 4 条消息 + 总结 |
| `estimate_tokens(messages)` | 粗略估算 token 数（~3.5 字符/token） |

### 设计理念

受 Claude Code 的 `HISTORY_SNIP` 机制启发，采用渐进式压缩策略，在上下文窗口快要耗尽时才触发更激进的压缩，日常对话中完全不干预。

---

## 5. `llm.py` — 大语言模型客户端

**作用**：供应商无关的 LLM 封装，支持 OpenAI 兼容 API 和 LiteLLM。

### 核心数据结构

```python
@dataclass
class ToolCall:
    id: str             # 工具调用 ID
    name: str           # 工具名称
    arguments: dict     # 参数
    
@dataclass
class LLMResponse:
    content: str        # 文本回复
    tool_calls: list[ToolCall]  # 工具调用列表
```

### 核心类

| 类 | 作用 |
|----|------|
| `LLM` | 使用 `openai.OpenAI` 客户端，支持流式传输、工具调用累积、指数退避重试（3 次）、费用估算 |
| `LiteLLM(LLM)` | 使用 `litellm` 库，支持 `anthropic/claude-3-haiku` 等跨供应商模型 |

### 定价表

内置了约 20 个模型（OpenAI、DeepSeek、Anthropic、阿里 Qwen、月之暗面 Kimi）的每百万 token 价格，用于自动估算运行成本。

### 重试策略

对 `RateLimitError`、`APITimeoutError`、`APIConnectionError` 和 5xx 错误进行指数退避重试（最多 3 次）。

---

## 6. `prompt.py` — 系统提示词

**作用**：组装系统提示词，指导 LLM 如何作为编码助手行事。

### 核心函数：`system_prompt(tools)`

生成包含以下内容的系统提示词：
- **代理身份** — "Axiom"，一个自主编码助手
- **环境信息** — 当前工作目录、操作系统、Python 版本
- **可用工具列表** — 从工具注册表自动生成
- **行为规则** — 8 条规则（先读后改、验证工作、保持简洁等）

---

## 7. `session.py` — 会话持久化

**作用**：将会话保存到磁盘 JSON 文件，支持恢复。

### 核心函数

| 函数 | 作用 |
|------|------|
| `save_session(messages, model, session_id)` | 保存消息列表和模型名到 `~/.axiom/sessions/<id>.json` |
| `load_session(session_id)` | 加载已保存的会话，返回 `(messages, model)` |
| `list_sessions()` | 列出最近的 20 个会话（最新在前） |
| `_normalize_session_id(session_id)` | 对会话 ID 做安全处理，或生成新的时间戳 + UUID |
| `_session_path(session_id)` | 防路径穿越的文件路径解析 |

### 存储位置

```
~/.axiom/sessions/
  ├── 20250301_abc123.json
  └── ...
```
