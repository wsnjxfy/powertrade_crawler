# Elecheck 电力市场分析 Agent MVP

## 1. 目标与边界

第十一周在 `powertrade_crawler` 内实现单 Agent MVP，用自然语言串联已有的 Elecheck
分析、导出、采集和调度能力。框架不使用 Agent SDK，也不提供多 Agent、RAG、长期语义
记忆、跨数据源分析、任意 SQL 写入、Shell 或任意文件工具。

核心实现位于：

```text
src/powertrade_crawler/agent/
```

主要模块：

- `provider.py`：`LLMProvider` 抽象、基于 `httpx` 的 `FreeLLMRouterProvider` 和测试用
  `FakeProvider`。
- `../llm_router.py`：项目外客户端配置加载、路由器启动、免费渠道/告警查询和策略校验。
- `tools.py`：Pydantic 参数模型、JSON Schema、工具注册和统一执行入口。
- `elecheck_tools.py`：Elecheck 分析、导出、采集和调度 allowlist。
- `loop.py`：最多 8 轮的模型调用、工具选择、审批暂停/恢复、幂等执行和结构化答案。
- `repository.py`：模型配置、会话、消息、运行、事件、审批和工具调用的 SQLite 持久化。
- `cli.py`、`gui.py`：共用核心逻辑的 Typer CLI 与 Tkinter 页面。
- `evaluation.py`：独立 SQLite fixture 的离线评测和 12 条真实只读/待审批在线评测。

## 2. 模型和协议

默认 Endpoint：

```text
http://127.0.0.1:8317/v1
```

模型策略保存在 `agent_settings`，不包含 API Key。默认 `smart-auto` 只在免费池中自动
选择；也可以固定到 `/v1/providers` 中 `tier=free` 且 `available=true` 的
`provider/<渠道ID>`。全部免费渠道不可用时明确失败，不回退付费接口。

运行时优先探测原生 `tool_calls`。不稳定或不支持时，可切换严格 JSON Action：

```json
{"type":"tool_call","tool_name":"elecheck_data_freshness","arguments":{}}
```

两种协议共用相同的注册表、Pydantic 参数校验、风险等级、审批、幂等和审计逻辑。

最终答案固定包含：

- `conclusion`
- `data_range`
- `business_metrics`
- `units`
- `completeness`
- `warnings`
- `generated_files`
- `executed_actions`
- `data_sources`

解析失败允许一次格式纠正；仍失败则保存轨迹并返回结构化错误。

## 3. 凭据配置

免费池客户端配置位于项目外：

```text
%USERPROFILE%\.config\llm-router\client-free.env
```

运行时从该文件读取 OpenAI 兼容 Endpoint、`smart-auto` 和本地免费池 Key。项目内只允许
通过 `LLM_ROUTER_CLIENT_ENV` 配置另一个外部文件路径，不保存密钥。状态和策略检查：

```powershell
powertrade agent doctor --json
powertrade agent config providers
powertrade agent config alerts
powertrade agent config strategy
```

本地免费池 Key 只在 Provider 构造 HTTP Authorization 请求头时读取。它不会进入提示词、
SQLite、定时任务参数、事件、日志、异常详情或评测报告。运行审计只保存响应头中非敏感的
实际渠道、上游模型和告警数量。

## 4. 工具和审批

### 自动执行

- `elecheck_list_areas`
- `elecheck_list_dates`
- `elecheck_purchasing_options`
- `elecheck_mechanism_options`
- `elecheck_data_freshness`
- `elecheck_sql_schema`
- `elecheck_sql_query`
- `elecheck_analyze_spot`
- `elecheck_analyze_spot_monthly_extrema`
- `elecheck_analyze_purchasing`
- `elecheck_analyze_mechanism`
- `elecheck_credential_status`
- `elecheck_list_schedules`
- `elecheck_list_schedule_runs`
- `elecheck_export_spot`
- `elecheck_export_purchasing`
- `elecheck_export_mechanism`

导出路径由程序生成：

```text
exports/agent/<session>/<timestamp>-<analysis>.csv|png
```

模型不能传入路径。现货价差只使用相同时间点，不插值、不补零；代理购电保留缺失月份
断点；增量机制只分析最新快照。

`elecheck_sql_query` 只连接本地 SQLite 的 Elecheck 业务表，适用于最高、最低、平均、
计数、分组排名和筛选。它只接受一条 `SELECT`/CTE，使用表、字段和函数白名单，排除
`raw_json`、Agent 会话、凭据和其他数据源表；连接开启 SQLite `query_only`/只读模式，
最多返回 200 行，并设置执行超时。模型不能通过该工具执行 DML、DDL、PRAGMA 或多语句。

现货分析默认使用 `summary`，只返回均值、极值和完整度；只有明确需要全部时点或 30 天
趋势时才使用 `full`。现货导出使用 `series=day_ahead|real_time|spread|all`，用户只要求
日前数据时不会混入实时价和价差列。

月度日前/实时日均价最高、最低日期由
`elecheck_analyze_spot_monthly_extrema` 直接计算，不需要模型反复试探 SQL。指定地区时
分析该地区；未指定地区时，先计算每个地区自己的日均价，再对可用地区等权平均，同时
返回日期覆盖和地区覆盖。全程不插值、不补零。

### 普通审批

- `elecheck_collect_spot`：单地区、最多 31 天，不接受“全部地区”。
- `elecheck_update_all_spot`：复用 GUI“更新全部数据”语义，从各地区已采最新日期
  往前一天开始，逐日更新全部地区到指定结束日期；若本地无现货数据则按各地区最早
  可用日期进行全覆盖采集。
- `elecheck_collect_purchasing`：最多 24 个月。
- `elecheck_update_mechanism`
- `elecheck_create_schedule`
- `elecheck_run_schedule`
- `elecheck_set_schedule_enabled`

### 强化审批

- `elecheck_install_windows_schedule`
- `elecheck_uninstall_windows_schedule`

CLI/GUI 会展示工具名、完整参数和副作用。强化审批还要求精确输入工具名。Agent 没有
删除定时任务、删除业务数据、修改凭据、数据库维护、跨数据源、SQL 写入、Shell 或
任意文件访问工具；受控 SQL 工具只允许 Elecheck 白名单内的只读查询。

“把 Elecheck 现货数据更新到今天/昨天/前天”这类未指定地区的明确请求会由本地
确定性意图路由直接转换为 `elecheck_update_all_spot` 审批，不依赖模型自行选择工具；
包含具体地区名称的请求仍交给单地区工具处理。

## 5. 审批、幂等和恢复

每个工具调用使用稳定工具调用 ID，并保存规范化参数 JSON 与 SHA-256 摘要。批准时再次
核对摘要；参数变化会使旧批准失效。

写操作只在状态为 approved 后通过统一执行入口运行。已成功的同一工具调用会返回持久化
结果，不会因审批恢复、模型重试或程序重启再次执行。待审批上下文保存在 `agent_runs`，
所以关闭 GUI 后仍可从 GUI 或 CLI 恢复：

```powershell
powertrade agent approvals list
powertrade agent approvals approve TOOL_CALL_ID
powertrade agent approvals reject TOOL_CALL_ID
```

## 6. CLI

```powershell
powertrade agent doctor [--json]
powertrade agent config show|set
powertrade agent chat [--session ID] [--message TEXT] [--json]
powertrade agent sessions list|show|delete
powertrade agent approvals list|approve|reject
powertrade agent tools list|show
powertrade agent eval --mode offline|live [--limit N] [--output PATH] [--json]
```

JSON 模式使用 `ok/data/error` envelope。阶段事件写入 stderr。配置错误、运行失败和
待审批分别使用稳定非零退出码 2、3、4。

## 7. GUI

`powertrade gui` 打开后进入：

```text
Elecheck 易能电易查 -> 智能 Agent
```

页面包含本地会话、对话、阶段事件、结构化结果、输入区、停止、审批和模型设置。模型和
工具在后台线程运行，通过 Tkinter 队列回到主线程更新。停止请求不会强杀网络线程，而是
丢弃未完成模型结果并阻止后续工具执行。

执行 `elecheck_update_all_spot` 时会弹出独立下载进度对话框，逐日显示当前地区、
请求进度、抓取条数、写入条数和百分比。“结束并保存”会等待当前逐日请求完成并写库，
随后停止剩余请求；隐藏进度窗口不会中断采集。

事件只显示请求模型、提出工具、等待审批、执行中、逐日下载进度、执行完成、生成结论
和失败等阶段，不显示隐藏推理。

## 8. 评测

离线评测无需 API Key，使用 Fake Provider 和临时 SQLite：

```powershell
powertrade agent eval --mode offline --json
```

覆盖原生调用、JSON 降级、未知工具、额外参数、审批暂停/恢复、重复执行防护、持久会话、
脱敏和循环上限。

在线评测固定 12 条案例：

```powershell
powertrade agent eval --mode live --limit 12 --output reports/agent-live-eval.json
```

前 9 条只读；后 3 条动作型案例只验证进入审批，不批准真实写入。报告记录工具选择、
参数校验、审批边界、结构化输出、完成率、耗时和 Provider token usage，不记录提示词、
原始响应或任何凭据。

## 9. 简历描述参考

可如实描述为：

> 在 Python/Tkinter/SQLite 电力数据平台中自研轻量单 Agent 框架，基于 Pydantic
> JSON Schema 实现原生 Function Calling 与严格 JSON Action 双协议、分级人工审批、
> 参数摘要失效、工具调用幂等和持久会话；将 Elecheck 现货、代理购电、增量机制分析、
> 导出、采集及调度能力封装为领域工具，并建立离线 Fake Provider 与在线模型评测集。

不要写成多 Agent、RAG、自主数据库操作或全市场通用智能平台；这些能力当前并未实现。
