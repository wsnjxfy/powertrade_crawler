# Powertrade Crawler 中文开发维护与接手指南

本文面向接手 Powertrade Crawler 的开发者和维护者，说明怎样建立环境、理解架构、扩展数据源、
维护 SQLite 与 GUI、遵守 Agent/RAG 安全边界、运行测试并生成 Windows 发布包。

普通用户请先阅读 [中文用户使用指南](USER_GUIDE_ZH.md)。

## 1. 当前项目基线

- 项目版本：`0.1.0`；
- 首个公开 Release：[`v0.1.0`](https://github.com/wsnjxfy/powertrade_crawler/releases/tag/v0.1.0)；
- 该版本代码提交：`70a07a4dac9176914f8470ef5ed205e6ca662d7b`；
- Python：项目元数据支持 3.11+，当前维护环境使用 3.12；
- GUI：Tkinter；
- 数据库：SQLite + SQLAlchemy；
- CLI：Typer；
- HTTP：httpx；
- 数据模型：Pydantic；
- 本地向量推理：FastEmbed 0.8.0 + ONNX Runtime；
- Windows 冻结：PyInstaller；
- 当前自动化基线：319 项 Pytest 使用 `-W error` 通过，Ruff 和 Python 编译检查通过。

不要把“公开仓库”“源码可见”“Windows 可执行包”和“已经获得第三方接口授权”混为一谈。各数据
源的条款、凭据和访问范围仍由使用者负责。

## 2. 接手后的阅读顺序

不要一上来就改爬虫。建议按以下顺序建立全局认识：

1. `AGENTS.md`：项目地图、已有约束、历史决策和后续建议；
2. `README.md`：功能、命令和各数据源入口；
3. `docs/USER_GUIDE_ZH.md`：最终用户真实使用路径；
4. `docs/FINAL_DELIVERY_GUIDE.md`：交付边界、已知限制和验收事实；
5. `docs/API_KEY_SETUP_GUIDE.md`：凭据申请与项目内外边界；
6. 当前任务对应的数据源指南、Agent 指南或 RAG 指南；
7. 对应源码和测试，不要只根据文档标题推断实现。

接手当天先记录：

```powershell
git status --short --branch
git log -5 --oneline
git remote -v
python --version
```

确认工作区、分支、远端和 Python 后再开发。

## 3. 克隆和开发环境

### 3.1 克隆

```powershell
git clone https://github.com/wsnjxfy/powertrade_crawler.git
cd powertrade_crawler
```

准备提交改动时建议使用独立分支：

```powershell
git switch -c feature/short-description
```

### 3.2 虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

也可以使用 `uv venv` 创建项目虚拟环境，但后续测试、构建和脚本必须始终使用同一环境的 Python。

浏览器依赖不是默认要求。只有新数据源确实依赖 JavaScript 渲染且官方没有更稳定 API 时再安装：

```powershell
python -m pip install -e ".[browser]"
playwright install chromium
```

### 3.3 首次运行

```powershell
powertrade init-db
powertrade list-spiders
powertrade gui
```

如果 console script 不可用：

```powershell
.\.venv\Scripts\python.exe -m powertrade_crawler.cli init-db
.\.venv\Scripts\python.exe -m powertrade_crawler.cli gui
```

## 4. 总体架构

```text
外部 API / 公开网页
        ↓
clients：HTTP、鉴权、分页、重试、异常分类
        ↓
spiders：业务参数、解析、标准化、自然键材料
        ↓
Pydantic models
        ↓
storage：SQLite 表、索引、事务、幂等 upsert
        ↓
CLI / GUI / metrics / scheduler / Agent tools
        ↓
本地浏览、分析、导出、自动更新和受控问答
```

主要目录和文件：

| 路径 | 职责 |
|---|---|
| `src/powertrade_crawler/clients/` | 各数据源 HTTP/API 封装 |
| `src/powertrade_crawler/spiders/` | 采集、分页编排、响应解析和记录转换 |
| `models.py` | Pydantic 业务记录模型 |
| `storage.py` | SQLAlchemy 表、索引、查询、upsert、导出和一致性保护 |
| `registry.py` | Spider 注册；决定 CLI/GUI 能看到什么 |
| `cli.py` | Typer 命令行入口 |
| `app_shell.py` | 八个顶层页面、导航和快捷操作 |
| `gui.py` 及 `*_gui.py` | 各来源页面、看板和交互 |
| `ui_dispatch.py` | 后台线程到 Tk 主线程的事件队列和生命周期令牌 |
| `metrics.py` | 日维度确定性指标 |
| `scheduler.py` | 来源级增量计划、运行记录、互斥和 Windows 任务同步 |
| `agent/` | Elecheck Agent |
| `market_agent/` | 多数据源 Agent、确定性数据工具和本地 RAG |
| `configs/` | GridStatus、ENTSO-E、Elexon 数据集请求目录 |
| `resources/initial/` | 首次启动使用的轻量数据库和种子资源 |
| `scripts/` | 初始库、模型准备、验收、打包和发布脚本 |
| `tests/` | 与业务模块对应的自动化测试 |

桌面入口是 `desktop_launcher.py`，源码 CLI 入口是 `powertrade_crawler.cli:app`。不要新增一个平行入口
绕开现有运行路径。

## 5. 五个来源的实现差异

| 来源 | 推荐接入方式 | 时间与分页重点 | 凭据 |
|---|---|---|---|
| ENTSO-E | 官方 Transparency Platform REST API，解析 XML/ZIP | UTC、结束边界不包含；多个 TimeSeries/Period；PT15M/PT60M；区域角色 | Security Token |
| Elexon | 官方 Insights API JSON | 日期、结算周期和大 BMU 查询分批；快照 0 条可能正常 | 当前无需 Key |
| GridStatus | 官方 API，配置优先 | cursor 分页、UTC、filter/location、进程级限流和断点 | API Key |
| Elecheck | 已确认业务接口 | 现货按地区逐日；代理购电按月；Authorization 401 后更新 | Authorization |
| 广州电力交易中心 | 公开索引页 + 详情页 | 索引分页、正文块顺序、网页结构变化 | 无 |

通用要求：

- 网络失败、超时、401、403、429、5xx 和“无数据”必须分类；
- 不要在异常中输出带 token 的完整 URL；
- 结束日期是否包含必须按数据源语义明确；
- 所有跨来源时间进入数据库前应明确 UTC 或原始业务口径；
- 分页必须有终止条件、重复页保护和合理限流；
- 真实联网冒烟只使用一天、小地区、小页数或 `dry-run`。

## 6. 新数据源或新数据集的完整接入流程

一个可交付的数据源不是“写完爬虫就结束”。建议按以下垂直链路实现：

### 第一步：确认接口事实

- 优先查官方 API 文档、Postman collection 或公开接口说明；
- 记录 URL、HTTP 方法、固定参数、业务参数、返回示例、分页、频率、时区和授权方式；
- 不要根据数据集中文名称猜 `documentType`、`businessType` 等枚举码；
- 不要把抓包中的真实凭据保存到 fixture、文档、日志或 Git。

### 第二步：扩展 client

- 在 `clients/` 增加请求方法；
- 复用统一超时、重试和网络异常分类；
- 凭据从 `credentials.py` 读取，不作为普通任务参数持久化；
- 分页和限流应尽量封装在客户端或专用下载器中；
- 错误信息只保留排障所需的非敏感上下文。

### 第三步：定义标准记录

- 在 `models.py` 增加或扩展 Pydantic 模型；
- 区分业务时间、UTC 时间、采集时间和原始返回；
- 保留可解释复杂字段的 `raw_json`，但不要依赖它完成所有常用查询；
- 在写库前确定稳定自然键，不能把 `collected_at` 放进自然键。

### 第四步：实现 spider

- 在 `spiders/` 中将接口数据转换成标准模型；
- 同一响应可能包含多序列、多周期和不同粒度，不能假设固定 24 点；
- 空响应、业务无数据和解析失败必须有不同结果；
- 支持 `dry-run` 时只展示非敏感请求参数，不联网、不写库。

### 第五步：存储、索引和事务

- 在 `storage.py` 增加表、唯一约束、常用筛选索引和 upsert；
- 自然键要覆盖“同一业务记录”的全部身份字段；
- 需要兼容 NULL 时使用 SQLite NULL-safe 唯一索引或等价保护；
- 整批写入应在一个明确事务中完成，异常时回滚，不能留半批数据；
- 补“首次写入、重复写入、字段更新、异常回滚”测试。

### 第六步：注册和暴露入口

- 在 `registry.py` 注册；
- 在 `cli.py` 暴露必要参数；
- GridStatus、ENTSO-E、Elexon 优先评估能否只扩展 `configs/*/requests.json`；
- 不要为一个配置可解决的数据集复制整套 Spider 类。

### 第七步：GUI、分析和导出

- 在正确的现有页面增加数据集，不随意堆新的顶层页面；
- 同时考虑参数说明、日期、执行、取消、状态、浏览、筛选、CSV 和必要的清理入口；
- 分析指标由确定性 Python/SQL 计算，不能让 LLM 计算正式统计值；
- 缺失数据不补零、不插值，单位和币种不兼容时不做伪比较。

### 第八步：调度和 Agent

- 只有适合增量更新的数据集才加入来源级调度；
- 明确运行时日期窗口、回看修订天数、快照语义和并发锁；
- Agent 工具必须有 Pydantic 参数、白名单、风险等级、审批规则和失败答案；
- 不要为方便模型而增加任意 SQL、Shell、文件浏览或凭据工具。

### 第九步：测试、文档和打包

- 单元测试使用 fixture，不依赖第三方在线服务；
- 增加小范围真实联网证据，但不要把凭据或完整响应提交；
- 更新 README、用户指南和对应 API 指南；
- 确认 PyInstaller spec 包含新增的受控配置/资源；
- 检查冻结态首次启动和数据不覆盖行为。

## 7. SQLite 数据一致性约束

### 7.1 表和自然键

`storage.py` 包含通用市场、ENTSO-E、Elexon、GridStatus、Elecheck、日指标、调度和两套 Agent 的
表。典型自然键包括：

- `MarketRecordRow`：来源、市场、地区、时间和产品等业务字段；
- `EntsoeRecordRow`：`dataset + row_key`；
- `ElexonRecordRow`：`dataset + row_key`；
- `GridStatusRecordRow`：`request_name + row_key`；
- Elecheck：地区、日期、端点、时间点、指标等组合自然键。

`row_key` 应由稳定业务内容生成，不能随每次采集变化。重复运行的预期行为是更新或保持同一行，而
不是每天追加副本。

### 7.2 upsert 和事务

- upsert 返回的写入数量要能用于 GUI、调度和验收；
- 写入前后的查询范围必须与自然键一致；
- 批量失败时回滚；
- SQLite 会把 NULL 视为互不相等，相关表已有额外唯一索引保护，扩展时不要破坏；
- 不要在 GUI 线程中长时间持有事务；
- 不要把凭据、Agent 网关 Key 或用户文件内容写入业务表。

### 7.3 指标和跨来源查询

`metrics.py` 维护日维度确定性指标。`market_agent/data_tools.py` 负责跨来源的受控查询、时间范围、
单位和完整度说明。新增指标时应：

1. 明确来源表和筛选条件；
2. 明确日界、时区、粒度和缺失值处理；
3. 返回单位、样本数、覆盖地区和日期；
4. 写确定性测试；
5. 不把业务统计交给 LLM 即席计算。

## 8. 时间和日期约定

- ENTSO-E 查询窗口使用 UTC，`end-date` 通常不包含；
- 非快照 Elexon 日期窗口也按结束边界不包含处理；
- Elecheck 现货按逐日请求，用户结束日期是包含的；
- GridStatus 数据时间统一为 UTC，目录更新时间不能代替“程序运行时的今天”；
- 广州文章以发布日为主，不应伪造成高频时序；
- 不要假设每一天固定 24 点，欧洲日前市场已经可能为 15 分钟粒度；
- 新代码优先复用 `datetime_utils.py`，并测试闰日、跨月、夏令时和空日期。

当前尚未为所有 ENTSO-E 区域自动选择当地交易日。报告和跨来源比较必须明确这是现有边界。

## 9. GUI 开发约束

八个顶层页面由 `app_shell.py` 的 `PageSpec` 定义。新增普通功能优先放入现有页面，除非确实存在独立
业务流程和导航价值。

### 9.1 主线程规则

Tkinter 只能在创建它的主线程中更新。网络、模型、长查询和导出应在后台线程运行，结果通过
`ui_dispatch.py` 返回：

- `TkEventDispatcher` 使用线程安全队列；
- 主线程定时批量消费回调；
- 后台线程不能调用 `widget.after(...)`、`messagebox` 或直接修改控件；
- `UiLifecycle` 绑定页面或子窗口销毁事件；
- 页面关闭、任务取消或根窗口销毁后，迟到结果必须被丢弃。

### 9.2 取消和生命周期

取消通常意味着停止后续步骤、丢弃未完成结果，而不是强杀 Python 线程。每个长任务都要定义：

- 已完成数据是否保留；
- 后续请求是否停止；
- 迟到回调是否还能更新页面；
- 关闭窗口后数据库事务和文件句柄怎样释放。

### 9.3 可用性

- 保持最小窗口、响应式网格和高 DPI 下按钮可见；
- 长表格和说明区使用明确滚动结构；
- 状态文案区分成功、无数据、部分成功、失败、跳过和已取消；
- 采集完成后的自动刷新不能覆盖更重要的错误状态；
- 修改导航、按钮和窗口布局时运行 `test_ui_navigation.py`、`test_ui_smoke.py` 和相关 GUI 测试。

## 10. 调度系统

`scheduler.py` 支持单 Spider、高级维护和 `source_update` 来源级增量任务。维护时注意：

- 日期窗口在每次运行时计算，不能在创建任务时永久写死；
- 同一任务使用线程级和进程级互斥，第二个运行记录 `already_running`；
- 来源内多个子步骤分别捕获异常，部分失败记为 `partial`，其余步骤继续；
- 本地 `enabled` 不等于 Windows 触发器已安装；
- 页面刷新要查询真实 Windows 任务计划状态，清理失效关联；
- 任务参数递归拒绝 API key、token、Authorization、password 和 secret；
- Windows 后台入口统一使用 `desktop_launcher.py --headless` 或冻结 EXE 的 `--headless`。

调度改动至少运行：

```powershell
python -m pytest -q tests/test_scheduler.py -W error
```

并在中文空格路径、GUI 关闭、立即运行和 Windows 触发器四种场景中抽查。

## 11. 两套 Agent 的维护边界

### 11.1 独立性

- `agent/` 是 Elecheck Agent；
- `market_agent/` 是多数据源 Agent；
- 两者共享业务数据、采集基础设施和中立 LLM 路由客户端；
- 两者的会话表、设置、工具和安全策略相互独立；
- 不要通过导入对方内部包来“快速复用”会话或审批状态。

### 11.2 典型组成

| 文件 | 职责 |
|---|---|
| `schemas.py` | 消息、工具调用、结构化答案和状态模型 |
| `tools.py` | 工具注册、JSON Schema、参数校验和统一执行入口 |
| `loop.py` | 模型/工具循环、最多轮次、审批暂停恢复、幂等和最终答案 |
| `repository.py` | 会话、消息、运行、事件、审批和工具调用持久化 |
| `provider.py` | OpenAI 兼容协议和本机免费路由器调用 |
| `security.py` | 风险级别、参数限制和安全规则 |
| `evaluation.py` | 离线 Fake Provider 和真实模型评测 |

### 11.3 不可突破的边界

- 只执行注册工具；
- 参数必须通过 Pydantic 校验；
- 写操作审批与规范化参数 SHA-256 绑定；
- 同一工具调用成功后恢复会话不能重复执行；
- 只读 SQL 只能单 SELECT/CTE、白名单表/字段/函数、只读连接、超时和 200 行上限；
- 不允许任意 SQL 写入、Shell、删除业务数据、任意文件访问和凭据修改；
- 模型不能直接计算最终业务统计值；
- 免费渠道全部不可用时明确失败，不自动回退付费模型；
- 失败必须回答原因、是否修改数据和下一步。

新增工具时必须同时增加：参数模型、工具说明、风险级别、审批策略、确定性执行器、失败测试、幂等
测试和离线评测案例。

## 12. 本地混合 RAG

RAG 只接入多数据源 Agent，核心在 `market_agent/rag.py`。

当前设计：

- 模型：`BAAI/bge-small-zh-v1.5`，512 维；
- 推理：FastEmbed CPU ONNX，冻结态完全离线；
- 向量：`float32` 写入 SQLite BLOB；
- 全文：FTS5 `trigram`，适配中文子串；
- 召回：向量和全文独立排名，RRF 融合；
- 增量：按内容哈希复用未变化分块向量；
- 更新：新代次完整构建后原子切换；失败时保留旧代次；
- 降级：向量不可用时明确进入 `lexical_fallback`；
- 引用：由工具结果确定性生成，正文被标记为不可信外部内容。

知识白名单只允许：广州公开文章、GridStatus 数据集目录、ENTSO-E/Elexon 请求定义和受控目录。
禁止把业务价格时序、凭据、会话、审批、日志、任意用户文件加入索引。

常用维护命令：

```powershell
python scripts/prepare_rag_model.py --verify-only
powertrade market-agent rag status --json
powertrade market-agent rag rebuild
powertrade market-agent rag search "绿证交易" --top-k 5 --json
powertrade market-agent rag eval --json
```

修改 RAG 后至少运行：

```powershell
python -m pytest -q tests/test_market_agent_rag.py -W error
```

并验证完整重建、内容未变复用、原子切换、取消、FTS 降级、来源过滤、无结果和引用有效性。

## 13. 测试和质量门禁

提交前完整检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q -W error
.\.venv\Scripts\python.exe -m compileall src
git diff --check
git status --short --ignored
```

常见改动与重点测试：

| 改动 | 重点测试 |
|---|---|
| 凭据和配置向导 | `test_credentials.py`、`test_credential_setup.py` |
| ENTSO-E | `test_entsoe_spider.py` |
| Elexon | `test_elexon_spider.py` |
| GridStatus | `test_gridstatus_spider.py`、`test_gridstatus_downloader.py` |
| Elecheck | `test_elecheck_spider.py`、业务看板测试 |
| 存储和自然键 | `test_storage_integrity.py` |
| 调度 | `test_scheduler.py` |
| Agent | `test_agent.py`、`test_agent_failure.py`、`test_market_agent.py` |
| RAG | `test_market_agent_rag.py` |
| GUI | `test_ui_dispatch.py`、`test_ui_navigation.py`、`test_ui_smoke.py` |
| 首次启动和发布 | `test_initial_database.py`、`test_release_gate.py` |

离线 Agent 评测不需要真实模型 Key：

```powershell
powertrade agent eval --mode offline --json
powertrade market-agent eval
powertrade market-agent rag eval --json
```

真实联网测试只作为额外证据，不能取代 fixture 自动化测试。执行前限制日期、地区和条数，避免产生
大额请求或无意修改主数据库。

## 14. 凭据与敏感数据

必须遵守：

- 电力来源凭据只存 `.auth/credentials.json`；
- `.env` 只存非敏感运行配置；
- LLM 上游 Key 只存在项目外的 `llm-router` 配置；
- 不在 URL、异常、日志、截图、fixture、数据库、Agent 提示词或评测报告中打印凭据；
- 不提交 `.auth/`、`.env`、`data/`、`exports/`、`work/`、`build/`、`dist/`；
- 不用 `git add -f` 绕过忽略规则；
- 发布前递归检查 `.auth`、`.env`、`credentials.json`、PFX、PEM、私钥和用户数据库；
- 任何真实凭据误入 Git 后，应先在来源平台撤销/轮换，再处理仓库历史。

不要在公开文档中记录“某台机器目前已经配置了什么 Key”。实时状态只能在本机使用
`powertrade credentials-status` 查看。

## 15. Windows 构建、验收和 Release

### 15.1 准备 RAG 模型

模型位于 Git 忽略的 `work/rag-model`，必须存在清单并通过 SHA-256 校验：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_rag_model.py --verify-only
```

缺少模型时按 [RAG 指南](RAG_GUIDE.md) 的准备流程生成。运行时禁止自动联网下载模型。

### 15.2 PyInstaller

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PowertradeCrawler.spec
```

正式构建最好使用独立暂存目录，避免覆盖开发者正在使用的 `dist`。干净分发源的根目录只能包含
冻结程序自身，不能先运行 EXE；运行后生成的 `.env`、`data/`、`configs/` 会污染待发布目录。

分发时必须压缩整个 `PowertradeCrawler` 文件夹，不能只上传 EXE。

### 15.3 干净机验收

验收源目录必须满足：

- 不含根部 `.env`、`.auth`、`data`、`configs`；
- 不含 `credentials.json` 和私钥；
- 大小不超过项目门禁；
- 复制到全新的中文空格路径后再运行；
- 离线首次启动能复制初始数据库；
- RAG 状态 ready、hybrid 检索成功；
- 8 个顶层页面可构建；
- 第二次启动不覆盖用户数据库。

可以使用：

```powershell
.\scripts\clean_machine_acceptance.ps1 `
  -DistributionPath .\dist\PowertradeCrawler `
  -AcceptanceRoot ".\work\发布 验收 新目录" `
  -ReportPath .\work\release-acceptance.json
```

每次 `AcceptanceRoot` 必须是从未存在过的新路径。

### 15.4 未签名与签名发布

当前公开版采用未签名 Windows 分发，这是发布成本和兼容性的明确取舍，不是功能 Bug。资产名和
Release 说明必须包含 `unsigned`，并诚实提醒 SmartScreen、Smart App Control 和企业策略风险。

如果组织以后要求可信发布者和企业应用控制兼容，应取得受信任 RSA 代码签名证书，并按
[Windows 发布门禁](WINDOWS_RELEASE_GATE.md) 执行签名、时间戳、全二进制验证和实际策略验收。
不能用自签名证书冒充公开可信签名。

### 15.5 GitHub Release 清单

1. 项目版本、标签和提交对应；
2. 完整测试和干净机验收通过；
3. ZIP 只包含完整程序目录；
4. 生成并复核 SHA-256；
5. Release 说明包含功能、首次使用、验收、未签名提示和已知限制；
6. 先创建草稿、上传附件并核对 GitHub 返回的大小和 digest；
7. 确认目标提交后再发布为正式版；
8. 发布后检查公开页面、标签、附件下载地址和 HTTP 状态。

## 16. 提交和评审

建议每次提交保持单一主题：

```powershell
git status --short --branch
git diff --check
git diff --stat
git diff
git add <明确文件>
git commit -m "Describe the change"
git push origin <branch>
```

提交前人工确认：

- 没有无关格式化或用户本地文件；
- 没有凭据、数据库、导出、日志、构建目录；
- 文档、测试和实现描述一致；
- 新增行为具有失败路径和回滚/幂等测试；
- 对第三方接口的结论注明真实验证日期和窗口；
- 不使用“绝对没有 Bug”“适用于所有环境”等无法证明的表述。

仓库当前提供第三方 RAG 许可说明，但项目所有者仍应根据预期协作和再分发方式，确保主项目许可证
及第三方许可信息清晰、完整。

## 17. 常见维护问题

### 重复采集出现重复行

先检查自然键是否遗漏地区、端点、时间点、产品或数据集字段，再检查 NULL 唯一性和 upsert 查询
条件。不要用采集时间去重。

### GUI 偶发关闭后报错

检查后台线程是否直接访问 Tk 控件，回调是否通过 `UiLifecycle.post()`，窗口销毁后 token 是否失效。

### 定时任务显示已安装但 Windows 中不存在

刷新时必须查询真实任务计划状态，不能只信数据库中的旧名称。参考 `test_scheduler.py` 的失效同步
用例。

### RAG 重建失败后知识库不可用

检查是否在新代次完成前改写了 `active_generation`。失败时必须删除未完成代次并保留旧代次。

### Agent 说要执行但没有执行

先看任务时间线：可能是参数缺失、进入审批、用户拒绝、工具不在白名单、免费模型不可用或凭据缺失。
失败回答不能只写“失败”，必须说明是否修改数据和下一步。

### 冻结包比验收值大很多

先检查是否在待发布目录运行过 EXE，导致根部生成 `.env`、`data`、`configs`；其次检查模型、初始库、
依赖重复和 PyInstaller datas。不要直接删除不认识的运行库来压缩体积。

## 18. 交接清单

交接给下一位维护者时，至少提供：

- 当前 `main`、远端和最新 Release 的对应关系；
- 最近一次完整测试、Ruff、编译和冻结验收结果；
- 已真实联网验证的数据源、日期和限制；
- 未验证的第三方环境和企业策略；
- 数据库结构或迁移变化；
- 新增自然键、调度窗口和 Agent 工具边界；
- RAG 模型版本、清单和索引状态；
- 当前已知问题和优先级；
- 凭据申请方式，但绝不交付真实凭据；
- 构建与 Release 的 SHA-256、标签和目标提交。

推荐后续方向包括：补强 ENTSO-E 拥塞与容量数据、Elexon REMIT/停运和 BMU 元数据、自动化当地
交易日语义、更多物理干净机与企业策略验证、依赖锁定/SBOM，以及在有合适证书时加入正式代码
签名。每项扩展仍应遵循“采集—存储—浏览—分析—调度—Agent—测试—打包”的完整链路。

## 19. 进一步阅读

- [项目 README](../README.md)
- [中文用户使用指南](USER_GUIDE_ZH.md)
- [最终交付使用与验收指南](FINAL_DELIVERY_GUIDE.md)
- [API Key 与账号配置指南](API_KEY_SETUP_GUIDE.md)
- [ENTSO-E API 指南](ENTSOE_API_GUIDE.md)
- [Elexon API 指南](ELEXON_API_GUIDE.md)
- [Agent MVP 指南](AGENT_MVP_GUIDE.md)
- [Agent 安全评审](AGENT_SECURITY_REVIEW.md)
- [Agent 威胁模型](AGENT_THREAT_MODEL.md)
- [本地混合 RAG 指南](RAG_GUIDE.md)
- [调度验收报告](SCHEDULE_ACCEPTANCE_REPORT.md)
- [Windows 发布门禁](WINDOWS_RELEASE_GATE.md)
