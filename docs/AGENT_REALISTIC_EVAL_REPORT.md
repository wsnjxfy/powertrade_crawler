# Agent 真实用例与跨功能协调性测试报告

测试日期：2026-08-07，第二轮全量复验：2026-08-10
测试范围：Elecheck Agent、多数据源 Agent、采集审批、定时任务、统一 API 配置向导、导出、异常说明与免费模型网关。

## 结论

- 已建立 105 条更接近真实用户表达的在线用例：多数据源 Agent 56 条，Elecheck Agent 49 条，包含 4 组多轮对话。
- 第二轮最终完整在线评测中，多数据源 Agent 通过 56/56，Elecheck Agent 通过 49/49；不是由失败项复测结果拼接而成，而是修复后分别重新完整执行。
- 10 条真实写操作用例全部先停在审批边界，再由评测器批准并执行成功；最终报告共记录 2,580 条写入或更新（包含重复数据的 upsert 更新）。
- 新增的 7 条跨功能协调用例全部通过：API Key 新用户引导、401/403 排障、跨来源定时任务审批、定时任务使用说明与任务列表。
- 当前自动化测试 238/238 通过；Ruff、编译检查和两套离线评测均通过。离线评测为多数据源 15/15、Elecheck 9/9。
- PyInstaller 重新打包成功；打包程序启动后持续存活超过 5 秒，两套 105 条在线用例资源均已包含在产物中。
- 评测过程中没有把 API Key、Authorization 或本机免费模型网关密钥写入源码、SQLite、提示词、日志或报告。

第二轮完整在线评测总耗时约 551 秒。评测结束后，按用例提示词精确匹配并拒绝了 68 条测试遗留审批记录；两套 Agent 当前待审批数均为 0，未处理非评测会话。

## 真实采集验证

以下写入均通过 Agent 审批链执行，使用真实外部接口和项目主数据库的 upsert 语义：

| 来源 / 任务 | 范围 | 写入或更新 |
| --- | --- | ---: |
| Elexon 系统价格 | 单日 | 198 条 |
| ENTSO-E 德国日前价格 | 单日 | 376 条 |
| Elecheck 江苏现货 | 单日 | 200 条 |
| GridStatus 数据集目录 | 目录刷新 | 535 条 |
| 广州电力交易中心公开信息 | 组合采集 | 200 条 |
| GridStatus `caiso_fuel_mix` | 2026-08-08 至 2026-08-09 | 576 条 |
| ENTSO-E 德国 → 法国跨境潮流 | 2026-08-08 | 77 条 |
| Elecheck 江苏现货（Elecheck Agent） | 2026-07-31 | 200 条 |
| Elecheck 全国代理购电 | 单月 | 138 条 |
| Elecheck 增量机制电价 | 当前快照 | 80 条 |

采集用例会先返回参数、预计请求、凭据状态和写库副作用，只有用户审批后才执行。测试产生的待审批记录已经拒绝或清理，当前两套 Agent 均无遗留待审批任务。

## 跨功能协调性改进

### Agent → 数据采集

- 对 ENTSO-E、Elexon、GridStatus、Elecheck 和广州交易中心的常见口语请求增加确定性路由。
- 缺少地区、日期、月份或 GridStatus 数据集时不再猜测参数，而是说明缺少什么和允许的最大范围。
- 审批摘要现在包含采集范围、预计请求数、运行凭据状态和业务数据副作用。
- 审批后的成功采集直接生成结构化结果，避免模型重复提出同一审批。

### Agent → 定时任务

- Elecheck Agent 保留 Elecheck allowlist 内的任务创建、运行、启停和 Windows 任务安装能力。
- 多数据源 Agent 新增受控定时任务创建，可覆盖精选 ENTSO-E、Elexon、GridStatus 目录、Elecheck 现货和广州交易中心公开信息。
- 创建任务必须审批；审批只创建本地任务定义，不会立即采集，也不会自动安装 Windows 触发器。
- Agent 能列出任务、说明启用状态和 Windows 触发器状态；GUI 提供“定时任务”快捷入口。
- 如果用户只问“怎么设置”，Agent 会解释完整流程，而不会误创建任务。

### Agent → API 配置向导

- 两套 Agent 都能安全读取统一配置向导的“是否已配置”状态，不读取或返回密钥原文。
- 回答会区分：实时采集必需的 GridStatus、ENTSO-E、Elecheck；无需 Key 的 Elexon 和广州交易中心；任选一个即可的免费 LLM 渠道。
- 提供注册/申请入口、401/403 排障方式和 GUI “API 配置向导”入口。
- Agent 不接收、不保存、不修改密钥。用户要求代写密钥时，只提供本机配置步骤。
- 两个 Agent 页面均增加“API 配置”和“定时任务”快捷按钮。

### Agent → 软件使用帮助

- 新用户可以直接询问采集、导出、定时任务或整体使用流程。
- Agent 会引导用户按“数据总览 → API 配置 → 小范围采集 → Agent 分析 → 定时任务”的顺序操作。
- 工具或外部服务失败时，Agent 必须返回具体原因、未执行的操作和下一步建议，不再只返回内部错误状态。

## 主要缺陷与修复

1. 模型使用常见数据集简称时可能被参数校验拒绝：增加数据集、地区和空值别名标准化。
2. 工具参数或外部服务失败后可能没有面向用户的回答：失败、停止和拒绝路径统一生成可操作说明。
3. 批准采集后模型可能重复提出审批：成功写操作直接根据工具结果完成回答。
4. 月度最高/最低问题依赖多轮模型和 SQL，耗时过长：改为确定性双工具路由，约 2 秒完成。
5. 全省最新代理购电导出缺少入口：增加受控全省 CSV 导出。
6. Windows 中文终端输出在线评测报告时可能触发 GBK 编码错误：增加安全 JSON 控制台输出。
7. “定时任务”口语中含“采集”时会被误判为立即采集：定时任务路由优先于采集路由。
8. “凭据状态”关键词最初会抢占普通模型工具批处理：配置引导收窄到申请、配置、缺失、失效和 401/403 等明确求助意图。
9. 统计“有效点数”错误继承电价单位：计数单位改为“个”。
10. 跨来源比较摘要只展示第一个数据集：摘要现在为每个数据集选取均值，并保留各自来源、数据集和单位。
11. 审批成功提示暴露内部工具名：改为用户可理解的中文业务名称。
12. 打包配置遗漏多数据源 Agent 在线用例：PyInstaller 数据目录已补齐。
13. “九点半”被默认成 02:00：新增中文数字、半点和刻钟解析，统一用于两套 Agent。
14. “帮我抓”“全采完”和多轮补充日期容易丢失采集意图：补齐口语动作词，并安全继承上一轮的来源和地区。
15. “按每天看”被误判成创建定时任务：查询粒度与周期任务使用统一、严格的定时意图判定。
16. “最近七天/最近一个月”可能被模型省略日期：增加动态 7/30 天窗口路由和在线评测硬断言。
17. 模型把山西、江苏翻译为 `Shanxi`、`Jiangsu`：两套 Agent 的地区参数统一归一化。
18. 免费模型偶尔把最终答案包装为 `AgentAnswer` 伪工具或把 JSON 塞进工具名：协议层识别后仍按 `AgentAnswer` Schema 校验。
19. 否定句“只要日前，别混实时和价差”误导出价差：显式“只要/仅”优先于普通关键词；错误测试文件已删除。
20. 空数据查询或导出反馈含糊：明确说明日期范围无数据、未补零、未编造；空导出不创建文件。
21. ENTSO-E/Elexon 与江苏/德国比较被模型编造日期：常用跨市场组合由受控序列参数执行，只并列展示不兼容口径。
22. Windows GBK 终端遇到窄空格字符会崩溃：多数据源 CLI 使用当前控制台编码安全转义 JSON。

## 复现命令

```powershell
powertrade market-agent eval
powertrade market-agent eval --online --start 1 --limit 10 --output reports/market-agent-eval.json
powertrade agent eval --mode offline --json
powertrade agent eval --mode live --start 1 --limit 10 --output reports/elecheck-agent-eval.json
```

只有在明确希望执行评测中的真实写操作时，才增加 `--execute-collections` 或 `--execute-actions`。所有写操作仍会走审批边界。

## 原始结果

- `AGENT_REALISTIC_MARKET_EVAL_RESULT.json`：多数据源 Agent 首轮完整结果。
- `AGENT_REALISTIC_ELECHECK_EVAL_RESULT.json`：Elecheck Agent 首轮完整结果。
- `AGENT_REALISTIC_MARKET_RERUN_*.json`：多数据源 Agent 失败项复测。
- `AGENT_REALISTIC_ELECHECK_RERUN_*.json`：Elecheck Agent 失败项复测。
- `AGENT_COORDINATION_MARKET_EVAL.json`：新增跨来源协调用例，5/5 通过。
- `AGENT_COORDINATION_ELECHECK_EVAL.json`：新增 Elecheck 配置引导用例，2/2 通过。
- `AGENT_HUMANIZED_MARKET_FINAL_RESULT.json`：第二轮多数据源最终完整在线结果，56/56，通过并执行 7 个真实采集。
- `AGENT_HUMANIZED_ELECHECK_FINAL_RESULT.json`：第二轮 Elecheck 最终完整在线结果，49/49，通过并执行 3 个真实动作。
- `AGENT_HUMANIZED_MARKET_OFFLINE_RESULT.json`：多数据源离线结果，15/15。
- `AGENT_HUMANIZED_ELECHECK_OFFLINE_RESULT.json`：Elecheck 离线结果，9/9。
