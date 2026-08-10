# Elecheck Agent 安全专项审查

## 多数据源 Agent 本地 RAG 补充结论

多数据源 Agent 的 `market_search_knowledge` 是自动执行的只读工具，只能读取显式白名单
公开资料和派生 `rag_*` 表。索引器没有文件路径、SQL、Shell、凭据、会话、审批、日志或
业务时序入口。检索正文被标记为不可信外部内容，模型生成的引用 ID 必须与工具结果
逐项匹配；无有效引用时只返回程序生成的资料列表。索引更新采用独立代次和原子切换，
失败不会扩大权限或修改业务数据。完整边界见 `RAG_GUIDE.md`。

审查范围：

```text
src/powertrade_crawler/agent/
src/powertrade_crawler/llm_router.py
src/powertrade_crawler/credentials.py
src/powertrade_crawler/scheduler.py
src/powertrade_crawler/storage.py
src/powertrade_crawler/cli.py
src/powertrade_crawler/gui.py
```

审查关注 Provider、提示词、工具注册、参数校验、审批、幂等、凭据、日志、SQLite、
导出和 Windows 任务计划边界。结论：交付前发现的高风险设计问题均已修复；没有注册
任意可写 SQL、Shell、任意文件、删除、数据库维护、跨数据源或凭据修改工具。新增的
Elecheck SQL 查询使用只读连接和表/字段/函数白名单，不属于任意数据库访问。

## 已修复问题

| ID | 原风险 | 等级 | 修复与证据 |
|---|---|---:|---|
| SR-001 | 可配置 Endpoint 若接受任意主机，可能把本地免费池 Key 发往攻击者服务器 | 高 | `llm_router.py`、`agent/repository.py::set_config` 和 `agent/provider.py::FreeLLMRouterProvider.__init__` 同时只接受本机 HTTP 回环地址；实际 Endpoint 运行时从项目外 `client-free.env` 读取；httpx 默认不跟随重定向 |
| SR-002 | 写工具在进程崩溃或模型重复提议后可能重复执行 | 高 | `agent/repository.py::create_tool_call` 按 run/tool/参数摘要复用调用；`begin_tool_call` 先持久化 executing，executing/failed 状态禁止自动重试；业务写入继续使用现有 upsert |
| SR-003 | 用户误把密钥贴进聊天时，会话标题可能保留原文 | 中 | `agent/repository.py::add_message` 对消息 JSON 和标题都调用统一脱敏；评测覆盖 Authorization/Bearer |
| SR-004 | 工具异常、旧任务参数或任务消息可能把敏感片段送进提示词/事件 | 中 | `agent/tools.py` 在模型看到工具结果前统一递归脱敏；任务参数、运行消息、Provider 异常和事件再次脱敏 |
| SR-005 | 同一会话在同一秒重复导出可能覆盖文件 | 低 | `agent/elecheck_tools.py::_export_path` 使用 UTC 微秒时间戳，且模型不能提供输出路径 |
| SR-006 | 模型生成 SQL 可能读到敏感表、执行写入或制造超大查询 | 高 | `elecheck_sql_query` 仅允许单条 SELECT/CTE；SQLite `mode=ro`、`query_only` 和 authorizer 同时限制表/字段/函数，排除 raw_json/Agent 表/其他来源，最多 200 行且有执行超时；回归测试覆盖 DELETE、多语句和越权字段/表 |

## 现有控制

- 免费池 Key 只由 `llm_router.py` 从项目外 `client-free.env` 读取，并在 `provider.py`
  构造 Authorization 请求头。
- `.auth/credentials.json` 继续保存业务数据源凭据并被 Git 忽略；免费池 Key 不复制进该文件。
- 模型配置表只保存本机 Endpoint、`smart-auto` 或 `provider/<渠道ID>` 和协议，不保存凭据。
- 固定渠道保存前、运行前都验证 `tier=free` 且 `available=true`；失败不回退付费接口。
- Pydantic 工具参数统一 `extra="forbid"`；未知工具和额外参数被拒绝。
- 所有工具来自显式 Elecheck allowlist；模型无法通过工具名访问 registry 之外的函数。
- 工具输出在提示词中标记为 `untrusted_tool_result`，系统提示明确禁止执行其中的指令。
- 自动工具只包含白名单只读查询、分析和预设目录导出；SQL 不具备 DML/DDL 能力。
- 单地区现货采集、现货“更新全部数据”、其他采集及调度创建/运行/启停需要普通审批；
  Windows 任务安装/卸载需要精确工具名二次确认。
- 全部现货更新只接受结束日期，地区和每个地区的起始日期由固定本地目录及 GUI 同款
  规则计算，模型不能注入地区列表或自行扩大起始范围。
- 常用的今天/昨天/前天全部现货更新使用本地确定性路由，直接进入普通审批；包含明确
  地区名称时不会被该路由覆盖。
- 批准绑定规范化参数 SHA-256；参数变化使旧批准失效。
- 最终 `data_sources`、`executed_actions` 和 `generated_files` 由运行轨迹确定性落地，防止
  模型虚构来源、操作或文件。
- Provider 超时只重试无副作用模型请求一次；工具执行失败不会自动重试。
- JSON 事件不包含隐藏推理、原始模型响应或完整请求头，只记录响应头中允许的渠道、
  上游模型和告警数量。
- 在线评测的动作案例只验证进入审批，不调用 approve。

## 残余风险

1. `.auth/credentials.json` 和项目外 `client-free.env` 仍是本机明文文件。Python
   `chmod` 在 Windows 上不能等价于完整 ACL；同一 Windows 用户下运行的恶意程序仍
   可能读取。后续可接入 Windows
   Credential Manager 或 DPAPI。
2. 用户问题和脱敏后的 Elecheck 工具结果会经本地路由器发送给第三方免费模型服务。当前数据以公开市场
   数据为主，但不应在聊天中粘贴未公开数据、个人信息或商业秘密。
3. Windows 任务安装/卸载最终依赖当前进程的 OS 权限和 `schtasks`。强化审批防止模型
   越权意图，但不能替代 Windows 账户和终端安全。
4. 本地 SQLite 审计记录不是防篡改日志；拥有数据库文件写权限的本地用户可修改轨迹。
5. 大模型仍可能产生多余只读工具调用或错误业务表述。严格 Schema、工具数值和评测降低
   风险，但高影响业务判断仍应由用户复核。

## 验证

```text
offline eval: 9/9
live eval: 12/12
structured output: 100%
tool selection: 100%
parameter validation: 100%
approval boundary: 100%
pytest: 134 passed
ruff: passed
compileall: passed
PyInstaller build: passed
packaged Agent CLI/GUI smoke: passed on the prior build
final unsigned rebuild launch: blocked by local Windows application-control policy
```
