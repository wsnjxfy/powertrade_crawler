# Powertrade Crawler Codex Project Guide

> 中文：这份文档是给未来新开的 Codex 对话长期参考用的“项目万用介绍和协作指南”。开始改代码前，优先阅读本文，再按需阅读 README、源码和测试。
>
> English: This document is the long-lived project briefing and collaboration guide for future Codex sessions. Read it first before changing code, then inspect the README, source files, and tests as needed.

## 1. 项目概览 / Project Overview

中文：

`powertrade_crawler` 是一个 Python 数据采集、存储、浏览和打包分发项目，目标是建设电力交易、电价和电力系统运行数据的本地采集与查看工具。当前重点数据源包括 ENTSO-E Transparency Platform、GridStatus、广州电力交易中心新闻，以及微信小程序“易能电易查”的多个业务接口。

English:

`powertrade_crawler` is a Python project for collecting, storing, browsing, and distributing power-market and power-system data. Its current data sources include ENTSO-E Transparency Platform, GridStatus, Guangzhou Power Exchange news, and several business APIs from the WeChat mini-program "Elecheck / 易能电易查".

核心技术栈 / Main stack:

- Python 3.12 locally, with project metadata supporting Python 3.11+
- Typer CLI
- SQLite local database
- SQLAlchemy ORM
- Tkinter GUI
- PyInstaller executable packaging
- pytest and ruff

常见项目路径 / Common paths:

- Source: `src/powertrade_crawler/`
- Tests: `tests/`
- Scripts: `scripts/`
- GridStatus request config: `configs/gridstatus/requests.json`
- ENTSO-E request catalog: `configs/entsoe/requests.json`
- ENTSO-E bilingual guide: `docs/ENTSOE_API_GUIDE.md`
- Local credential file: `.auth/credentials.json` (ignored by Git)
- Local database: `data/powertrade.db`
- GUI entry: `powertrade gui`
- CLI entry: `powertrade`

## 2. Codex 开始工作时先做什么 / First Steps for Codex

中文：

1. 确认当前任务目标，不要只凭历史上下文猜测需求。
2. 先读相关源码，再改代码。优先查看本文第 9 节列出的文件。
3. 注意本项目可能存在大型本地数据、打包产物和真实授权文件，不能随意提交或复制。
4. 如果任务涉及接口、字段、GUI 表格或数据库结构，要同时考虑 client、spider、model、storage、CLI、GUI 和测试。
5. 改完后尽量运行 `ruff`、`py_compile` 或 `pytest` 中合适的一组检查。

English:

1. Confirm the current task instead of relying only on historical context.
2. Read the relevant code before editing. Start with the files listed in section 9.
3. Be careful with large local data, packaged artifacts, and real authorization files. Do not commit or copy them casually.
4. For API, field, GUI-table, or database-schema work, consider client, spider, model, storage, CLI, GUI, and tests together.
5. After edits, run an appropriate subset of `ruff`, `py_compile`, and `pytest` whenever practical.

## 3. 环境与常用命令 / Environment and Common Commands

中文：

建议使用项目内虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

常用检查命令：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall src
```

常用运行命令：

```powershell
.\.venv\Scripts\python.exe -m powertrade_crawler.cli init-db
.\.venv\Scripts\python.exe -m powertrade_crawler.cli list-spiders
.\.venv\Scripts\python.exe -m powertrade_crawler.cli gui
```

如果 `powertrade` 命令已安装，也可以使用：

```powershell
powertrade init-db
powertrade list-spiders
powertrade gui
```

English:

Use the in-project virtual environment when available:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Common checks:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall src
```

Common run commands:

```powershell
.\.venv\Scripts\python.exe -m powertrade_crawler.cli init-db
.\.venv\Scripts\python.exe -m powertrade_crawler.cli list-spiders
.\.venv\Scripts\python.exe -m powertrade_crawler.cli gui
```

If the `powertrade` console script is installed:

```powershell
powertrade init-db
powertrade list-spiders
powertrade gui
```

## 4. 架构地图 / Architecture Map

中文：

- `clients/`: 封装外部 HTTP/API 请求。新接口优先从这里加方法。
- `spiders/`: 业务采集逻辑，把 client 返回的数据转换成项目内部记录。
- `models.py`: Pydantic 业务记录模型。
- `storage.py`: SQLAlchemy ORM 表、建表、会话、写入、upsert、导出等持久化逻辑。
- `registry.py`: spider 注册表，决定 `list-spiders` 和 `crawl` 能看到什么。
- `cli.py`: Typer 命令行入口。
- `gui.py`: Tkinter GUI，包含数据浏览、筛选、导出、清空、采集进度窗口等。
- `config.py`: 非敏感环境变量与运行配置。
- `credentials.py`: GridStatus、Elecheck、ENTSO-E 三种凭据的统一本地存储和读取。
- `elecheck_auth.py`: Elecheck Authorization 的兼容读取、缓存和进程内覆盖。

English:

- `clients/`: External HTTP/API wrappers. Add new API methods here first.
- `spiders/`: Business collection logic that transforms client responses into project records.
- `models.py`: SQLAlchemy ORM models.
- `storage.py`: Table creation, sessions, inserts, upserts, exports, and persistence utilities.
- `registry.py`: Spider registry used by `list-spiders` and `crawl`.
- `cli.py`: Typer command-line entry point.
- `gui.py`: Tkinter GUI for browsing, filtering, exporting, clearing data, and running collection progress dialogs.
- `config.py`: Environment variables and settings.
- `elecheck_auth.py`: Elecheck Authorization loading and expiration handling.

## 5. 已有业务模块 / Existing Business Modules

中文：

当前项目已具备：

- `powertrade` 命令行入口
- `powertrade gui` 图形化入口
- spider 注册机制
- 本地 SQLite 数据库
- CSV 导出
- GridStatus 数据浏览与下载
- ENTSO-E 欧洲数据源 CLI 采集、GUI 选择/预览/执行和本地结果浏览
- GZPEC 新闻采集
- Elecheck 易能电易查数据采集与 GUI 浏览

English:

The project currently includes:

- `powertrade` CLI entry
- `powertrade gui` GUI entry
- Spider registry
- Local SQLite database
- CSV export
- GridStatus data browsing and downloading
- ENTSO-E market, load, generation, transmission, balancing, and outage collection
- ENTSO-E GUI dataset selection, dry-run preview, collection, and local result browsing
- GZPEC news collection
- Elecheck data collection and GUI browsing

## 6. Elecheck 模块 / Elecheck Modules

中文：

Elecheck 目前在 GUI 中有一个主页：`Elecheck 易能电易查`，下面分为三个 tab：

- `现货价格`
- `代理购电价格`
- `增量机制电价`

### 6.1 现货价格 / Clear Price

主要 spider:

```text
elecheck_clear_price
```

示例命令：

```powershell
powertrade crawl elecheck_clear_price --area 江苏 --start-date 2026-05-10 --end-date 2026-06-10 --daily
```

关键点：

- GUI 中默认逐日采集，不再暴露 `--daily` 开关。
- 支持按地区和日期范围采集。
- 选择 `全部` 地区时，对所有地区按用户选择的日期范围采集。
- 支持“爬取全部数据 / 更新全部数据”。
- 如果已有历史现货数据，更新会从各地区最新日期往前顺延一天开始。
- 支持进度弹窗、最小化、暂停、继续、结束并保存、放弃采集。
- 支持筛选、CSV 导出、清空数据。

相关表：

```text
elecheck_clear_price_records
elecheck_area_records
```

`elecheck_area_records` 中有字段：

```text
earliest_clear_price_date
```

用于记录各地区现货价格最早可用日期，GUI 选择地区时会提示最早可选日期。

### 6.2 代理购电价格 / Purchasing Price

相关 spiders:

```text
elecheck_purchasing_province_list
elecheck_purchasing_national_month
elecheck_purchasing_national_range
elecheck_purchasing_province_month
```

GUI 当前主要基于：

```text
elecheck_purchasing_national_range
```

示例命令：

```powershell
powertrade crawl elecheck_purchasing_national_range
powertrade crawl elecheck_purchasing_national_range --start-month 2024-02 --end-month 2026-06
```

关键点：

- 代理购电价格从 `2024-02` 开始采集。
- GUI 支持采集全部、清空数据、月份筛选、省份筛选、数据类型筛选、指标筛选、CSV 导出。
- GUI 表格已隐藏“环比变化”列，但数据库和导出仍保留 `diff_value` 字段。

相关表：

```text
elecheck_purchasing_records
elecheck_purchasing_area_records
```

`elecheck_purchasing_area_records` 使用字段：

```text
province_name
```

省份接口：

```text
/electricCheckApi/queryData/purchasing/provinceList
```

### 6.3 增量机制电价 / Mechanism Electricity Price

主要 spider:

```text
elecheck_mechanism_electricity_price
```

示例命令：

```powershell
powertrade crawl elecheck_mechanism_electricity_price
```

相关表：

```text
elecheck_mechanism_electricity_price_records
```

关键点：

- GUI 支持数据浏览、地区筛选、电源类型筛选、CSV 导出、采集全部、清空数据。
- 采集全部时会请求 Elecheck 增量机制电价列表接口，一次性抓取全部地区和全部电源类型记录。
- GUI 表头按业务口径显示：`price` 显示为 `燃煤基准价`，`clear_price` 显示为 `26年增量机制电价`。

### 6.4 抓包适配环境 / Packet-Capture Compatibility

中文：

Elecheck 接口最初通过微信小程序抓包确认。当前已经验证可用的抓包组合是：

```text
微信 Windows 版 3.9.12
WMPF version: 14315
WMPFDebugger
```

注意事项：

- 当前这个微信 / WMPF 版本组合是适配可用的，后续需要重新抓包时优先保持该环境，不要随意升级微信或 WMPF 插件。
- 曾遇到 `version config not found: 19921`，说明较新的 WMPF 19921 当时不在 WMPFDebugger 支持列表中。
- 曾遇到 `version config not found: 14185`，说明微信 3.9.12 初始对应的 WMPF 14185 也不适配。
- 后来通过微信内置命令更新 WMPF 插件后变为 14315，WMPFDebugger 成功注入并能在 DevTools Network 中抓到 Elecheck 请求。
- 若未来需要重新抓包，先确认终端日志出现类似 `script loaded, WMPF version: 14315` 和 `miniapp client connected`，再分析 Network 请求。

English:

Elecheck currently has a main GUI page named `Elecheck 易能电易查` with three tabs:

- `现货价格` / Clear Price
- `代理购电价格` / Purchasing Price
- `增量机制电价` / Mechanism Electricity Price

The main implementation points are:

- Clear price uses `elecheck_clear_price`, stores data in `elecheck_clear_price_records`, and uses `elecheck_area_records.earliest_clear_price_date` to guide valid date choices.
- Purchasing price uses several purchasing spiders, with the GUI primarily using `elecheck_purchasing_national_range`. Data starts from `2024-02` and is stored in `elecheck_purchasing_records`.
- Mechanism electricity price uses `elecheck_mechanism_electricity_price` and stores data in `elecheck_mechanism_electricity_price_records`.
- Packet capture for Elecheck was validated with WeChat for Windows 3.9.12, WMPF version 14315, and WMPFDebugger. Keep this known-good setup when recapturing mini-program traffic. WMPF 19921 and 14185 previously failed with `version config not found`.

## 7. 统一鉴权逻辑 / Unified Credential Logic

中文：

所有 token、API key 和 Authorization 已统一迁移到：

```text
.auth/credentials.json
```

结构固定为：

```json
{
  "gridstatus_api_key": "",
  "elecheck_authorization": "",
  "entsoe_security_token": ""
}
```

关键规则：

- `.auth/` 整个目录已加入 `.gitignore`，绝对不能强制提交。
- `.env` 只保存数据库、超时、重试、请求间隔等非敏感配置。
- 不要重新把 `GRIDSTATUS_API_KEY`、`ELECHECK_AUTHORIZATION` 或 `ENTSOE_SECURITY_TOKEN` 放回 `.env`。
- 使用 `powertrade set-credential gridstatus|elecheck|entsoe` 隐藏输入凭据，避免进入 PowerShell 历史。
- 使用 `powertrade credentials-status` 只查看 configured/missing，不显示具体值。
- 当前读取优先级是：显式临时参数、进程内覆盖、`.auth/credentials.json`。
- Elecheck Authorization 一直使用到接口返回 HTTP 401，不主动按时间过期。
- Elecheck CLI/GUI 输入新 Authorization 后会保存到统一鉴权文件。
- GridStatus GUI 的“更换 API key”也写入统一鉴权文件。
- ENTSO-E 的 `--entsoe-token` 只应作为临时覆盖；正常运行读取 `.auth`。

不要在文档中固化本机凭据是否存在。每次以以下命令实时检查为准：

```powershell
powertrade credentials-status
```

不要在日志、截图、聊天、测试 fixture 或提交信息中打印真实凭据。曾经有一次 ENTSO-E token 出现在终端错误 URL 中，因此客户端已经改为不在错误日志中输出带查询参数的完整 URL。

English:

All API keys, tokens, and authorization values are stored in:

```text
.auth/credentials.json
```

The `.auth/` directory is ignored by Git. Do not move secrets back into `.env`, logs, screenshots, tests, or commits. Use `powertrade set-credential ...` and `powertrade credentials-status`.

## 8. 数据库、打包与分发 / Database, Packaging, and Distribution

中文：

默认数据库路径：

```text
data/powertrade.db
```

注意：

- 本地数据库可能很大，曾达到约 1.16GB，主要来自现货历史数据。
- 不要把大型历史数据库提交到 Git。
- exe 分发包应使用轻量数据库，只保留必要基础表和空业务表结构。

PyInstaller 入口：

```text
desktop_launcher.py
PowertradeCrawler.spec
```

打包命令：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PowertradeCrawler.spec
```

English:

Default database path:

```text
data/powertrade.db
```

Notes:

- The local database may be very large, formerly around 1.16GB, mostly from historical clear-price data.
- Do not commit large historical databases.
- Executable distribution packages should use a lightweight database containing required metadata tables and empty business schemas.

PyInstaller files:

```text
desktop_launcher.py
PowertradeCrawler.spec
```

Packaging command:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean PowertradeCrawler.spec
```

## 9. 修改代码时优先阅读的文件 / Files to Read First

中文：

新任务开始时，根据任务类型优先阅读：

```text
src/powertrade_crawler/clients/elecheck.py
src/powertrade_crawler/clients/entsoe.py
src/powertrade_crawler/clients/gridstatus.py
src/powertrade_crawler/spiders/elecheck.py
src/powertrade_crawler/spiders/entsoe.py
src/powertrade_crawler/models.py
src/powertrade_crawler/storage.py
src/powertrade_crawler/cli.py
src/powertrade_crawler/gui.py
src/powertrade_crawler/registry.py
src/powertrade_crawler/elecheck_auth.py
src/powertrade_crawler/credentials.py
configs/entsoe/requests.json
docs/ENTSOE_API_GUIDE.md
tests/test_elecheck_spider.py
tests/test_entsoe_spider.py
tests/test_credentials.py
```

English:

At the start of a task, read the relevant files first:

```text
src/powertrade_crawler/clients/elecheck.py
src/powertrade_crawler/spiders/elecheck.py
src/powertrade_crawler/models.py
src/powertrade_crawler/storage.py
src/powertrade_crawler/cli.py
src/powertrade_crawler/gui.py
src/powertrade_crawler/registry.py
src/powertrade_crawler/elecheck_auth.py
tests/test_elecheck_spider.py
```

## 10. 新接口接入流程 / Workflow for Adding a New API

中文：

推荐顺序：

1. 分析 HAR、接口 URL、请求参数、响应结构、授权要求和分页规则。
2. 在 `clients/` 中新增或扩展 client 方法。
3. 在 `models.py` 中新增或扩展 ORM 模型。
4. 在 `spiders/` 中新增 spider，把接口响应转换成内部字段。
5. 在 `storage.py` 中新增建表、写入、upsert 或查询逻辑。
6. 在 `registry.py` 注册 spider。
7. 在 `cli.py` 暴露命令参数。
8. 如需 GUI，在 `gui.py` 增加 tab、筛选、表格、采集按钮、导出、清空和进度逻辑。
9. 补测试，至少覆盖响应解析和 spider 输出。
10. 运行 `ruff` 和相关 `pytest`。

English:

Recommended order:

1. Analyze HAR files, URLs, parameters, response structure, authorization, and pagination.
2. Add or extend client methods under `clients/`.
3. Add or extend ORM models in `models.py`.
4. Add a spider under `spiders/` to transform API responses into internal fields.
5. Add table creation, insert, upsert, or query logic in `storage.py`.
6. Register the spider in `registry.py`.
7. Expose command parameters in `cli.py`.
8. If GUI support is needed, update `gui.py` with tabs, filters, tables, collection buttons, export, clearing, and progress behavior.
9. Add tests, at least for response parsing and spider output.
10. Run `ruff` and relevant `pytest` tests.

## 11. GUI 修改提醒 / GUI Editing Notes

中文：

`gui.py` 较大，修改前先定位相关 tab 和 repository 方法。GUI 相关改动通常需要同时考虑：

- 数据查询方法
- 表格列定义
- 筛选条件
- 导出字段
- 清空数据按钮
- 采集按钮
- 进度弹窗
- 授权过期提示
- 大量数据刷新时的性能

不要只改表头而忘记查询、导出或测试。业务显示名称可以不同于数据库字段名，但要在代码中保持映射清楚。

English:

`gui.py` is large. Before editing, locate the relevant tab and repository methods. GUI changes usually involve:

- Query methods
- Table columns
- Filters
- Export fields
- Clear-data buttons
- Collection buttons
- Progress dialogs
- Authorization-expired messages
- Performance with large datasets

Do not update only table headers while forgetting queries, exports, or tests. Business display names may differ from database field names, but the mapping should stay explicit.

## 12. Git 与敏感文件 / Git and Sensitive Files

中文：

当前远程仓库：

```text
https://github.com/wsnjxfy/powertrade_crawler.git
```

不要提交：

- `.env`
- `.auth/`
- 真实 Authorization token
- `data/`
- `dist/`
- `build/`
- `.venv/`
- `.venv_pack/`
- `node_modules/`
- `.browser-profiles/`
- 临时导出和工作目录

提交前建议检查：

```powershell
git status --short --ignored
git diff --cached --stat
```

English:

Current remote:

```text
https://github.com/wsnjxfy/powertrade_crawler.git
```

Do not commit:

- `.env`
- Real Authorization tokens
- Real API keys and security tokens
- `data/`
- `dist/`
- `build/`
- `.venv/`
- `.venv_pack/`
- `node_modules/`
- `.browser-profiles/`
- Temporary exports and work directories

Before committing, check:

```powershell
git status --short --ignored
git diff --cached --stat
```

## 13. 项目协作原则 / Collaboration Principles

中文：

- 保持改动聚焦，避免顺手大重构。
- 优先沿用现有风格和结构。
- 新字段、新表、新 tab 要同步考虑导出、筛选和清空逻辑。
- 接口逻辑尽量通过结构化 JSON 解析，不要用脆弱的字符串拼接。
- 对真实外部接口保持谨慎，尽量用小范围请求验证。
- 如果任务涉及“最新接口行为”或网页变动，应重新验证 HAR、接口返回或官方页面。
- 如果遇到用户已有未提交改动，先理解并配合，不要回滚。

English:

- Keep changes focused and avoid opportunistic large refactors.
- Follow the existing structure and style.
- For new fields, tables, or tabs, update export, filter, and clear-data logic together.
- Prefer structured JSON parsing over fragile string handling.
- Be cautious with real external APIs and validate with small requests first.
- If the task depends on current API or web behavior, re-check HAR files, responses, or official pages.
- If there are existing uncommitted user changes, understand and work with them instead of reverting them.

## 14. 常见后续任务 / Common Future Tasks

中文：

可能继续做的方向：

- 接入新的微信小程序接口。
- 扩展 ENTSO-E 常用数据目录和区域类型映射。
- 给 GUI 增加新的 Elecheck 业务板块。
- 扩展现有三个板块的筛选和导出字段。
- 优化 exe 分发包。
- 给普通用户增加授权文件导入功能。
- 增加数据质量校验。
- 增加标准化报表导出。
- 继续分析 HAR 文件并转成 client、spider、storage、GUI 代码。

English:

Likely future work:

- Add new WeChat mini-program APIs.
- Add new Elecheck business tabs to the GUI.
- Extend filters and export fields for existing modules.
- Optimize executable distribution packages.
- Add an authorization-file import feature for regular users.
- Add data-quality checks.
- Add standardized report exports.
- Continue converting HAR analysis into client, spider, storage, and GUI code.

## 15. 2026-06-24 ENTSO-E 模块交接 / ENTSO-E Handoff

中文：

本轮已经完成 ENTSO-E Transparency Platform 官方 REST API 接入。不要抓网页前端，优先使用官方 API：

```text
https://web-api.tp.entsoe.eu/api
```

特别注意：正确端点是 `/api`，不能写成 `/api/`。ENTSO-E 对末尾斜杠敏感：

```text
/api  -> 正确路由；无 token 时通常返回 401 XML
/api/ -> 404
```

已有能力：

- `entsoe_day_ahead_prices` 兼容 spider，继续输出 `MarketRecord` 并写入 `market_records`。
- 其余 ENTSO-E 配置化 spider 输出 `EntsoeRecord` 并写入 `entsoe_records`。
- 当前 `configs/entsoe/requests.json` 配置了 26 个常用数据集。
- 类别覆盖 market、load、generation、transmission、balancing、outages。
- 支持单区域、同区域 in/out、跨境双区域三种 domain 模式。
- 支持 `--area`、`--area-code`、`--in-area`、`--out-area` 和直接 EIC。
- 支持 `--psr-type`，例如 `B16` 光伏、`B18` 海上风电、`B19` 陆上风电。
- 支持重复传入 `--entsoe-param KEY=VALUE` 覆盖或增加官方参数。
- `powertrade entsoe-query` 可直接调用未加入目录的官方 API。
- 长时间范围依据每个数据集的 `chunk_days` 自动分段。
- 通用 XML 解析器保留 document、series、period、point 四层原始上下文。
- 停运接口可能返回 ZIP，且内部使用 `Available_Period` 而不是普通 `Period`；客户端已经支持 ZIP 解包和两种时段结构。
- 错误响应通常是 `Acknowledgement_MarketDocument`，应解析 `Reason.code` 和 `Reason.text`。
- 4xx 请求错误不重复重试；429 按 `Retry-After` 等待。

常用命令：

```powershell
powertrade entsoe-datasets
powertrade entsoe-describe entsoe_actual_total_load
powertrade entsoe-areas
powertrade init-db
```

日前电价：

```powershell
powertrade crawl entsoe_day_ahead_prices --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

实际负荷：

```powershell
powertrade crawl entsoe_actual_total_load --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

按电源类型实际发电：

```powershell
powertrade crawl entsoe_actual_generation_by_type --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

跨境物理潮流：

```powershell
powertrade crawl entsoe_cross_border_physical_flows `
  --in-area FR --out-area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

真实验证结果（2026-06-24）：

- DE-LU 实际总负荷：成功返回 96 个 15 分钟点。
- DE-LU 按能源类型实际发电：成功返回 1309 个点，并识别 `psr_type`。
- FR -> DE-LU 跨境物理潮流：认证、请求和解析成功。
- DE-LU 日前电价：成功返回完整 96 个 15 分钟价格点。

English:

The ENTSO-E module uses the official REST API, with a configuration-driven catalog, generic XML/ZIP parsing, automatic chunking, dedicated storage, area/EIC support, and raw parameter overrides. Read `docs/ENTSOE_API_GUIDE.md` and `configs/entsoe/requests.json` before extending it.

### 15.1 2026-06-26 ENTSO-E GUI 接入 / ENTSO-E GUI Integration

中文：

本轮已经把 ENTSO-E 接入 Tkinter GUI。入口是 `powertrade gui` 顶层 Notebook 的 `ENTSO-E 欧洲` 页签。

GUI 已有能力：

- 显示 ENTSO-E token 配置状态，只显示 `configured` 或 `missing`，绝不显示 token 明文。
- 数据集下拉来自 `configs/entsoe/requests.json`，不是写死在 GUI 中；当前 26 个配置化数据集已全部接入。
- 说明面板展示数据集中文说明、英文说明、固定参数含义、domain mode 和返回数据意义。
- 单区域与同区 in/out 数据使用 `区域`，跨境数据使用 `来源区域` 和 `目标区域`。
- area 下拉来自 `ENTSOE_BIDDING_ZONES`，但 GUI 展示中文可读名称，例如 `德国-卢森堡 (DE-LU)`。
- 选择空白或 `全部区域` 会展开为全部内置 area；跨境数据会展开为来源/目标组合，数量较大时执行前会弹确认。
- 跨境可用性探测结果保存在 `configs/entsoe/border_availability.json`。已探测的数据集会动态过滤来源/目标区域；未探测的数据集继续显示全部区域。
- 支持选择 `start-date` 和 `end-date`；GUI 预览按 UTC 零点生成 `periodStart`/`periodEnd`。
- 支持 `psrType` 和额外 `KEY=VALUE` 参数；但兼容数据集 `entsoe_day_ahead_prices` 不支持这两个 GUI 字段。
- `dry-run 预览` 只构造并展示不含 `securityToken` 的请求参数，不访问 ENTSO-E，也不写数据库。
- `执行爬取` 复用 `registry.get_spider`、现有 spider/client/credentials/storage 逻辑，不另写 API 请求。
- 通用 ENTSO-E 数据写入并浏览 `entsoe_records`；兼容的 `entsoe_day_ahead_prices` 仍写入并浏览 `market_records`。
- GUI 采集线程会在后台运行，并在完成后刷新当前结果表。
- `导出数据` 按当前数据集、区域和日期筛选导出 CSV。
- `清除数据` 按当前数据集、区域和日期筛选删除本地记录，执行前会二次确认。

实现位置：

```text
src/powertrade_crawler/gui.py
  EntsoeDataRepository
  EntsoeDataApp
  launch_gui() 中的 ENTSO-E 欧洲 tab
scripts/probe_entsoe_border_availability.py
configs/entsoe/border_availability.json
```

注意事项：

- 不要把 ENTSO-E token 放进 GUI 文本框、日志、dry-run 预览或截图。
- GUI 的 dry-run 语义不同于 CLI `--dry-run`：GUI 预览只展示请求参数；CLI dry-run 会实际跑 spider 并把记录打印出来但不写库。
- Tkinter `ttk.Combobox` 不支持单个下拉项置灰；当前实现是读取可用性文件后把不可用来源/目标组合从可选列表中移除，并在旁边显示可用/无数据/错误数量。
- 当前已运行 `scripts/probe_entsoe_border_availability.py` 探测 5 个跨境数据集，窗口为 `2026-06-01` 到 `2026-06-02`，候选方向 124 个。结果：物理潮流 122/2/0，商业计划 122/2/0，预测传输容量 42/82/0，日前可供交易容量 22/102/0，输电停运 23/60/41，数字顺序为 有数据/无数据/错误。这个结果只代表该日期窗口。
- 如果以后要支持更细的区域角色配置、真实可用边界清单或按数据集维护可用区域白名单，优先扩展现有 `EntsoeDataRepository` / `EntsoeDataApp`，仍复用 spider/client/storage。
- `entsoe_day_ahead_prices` 的存储差异是有意保留的兼容行为，不要为了 GUI 统一展示而把它偷偷迁到 `entsoe_records`。

English:

ENTSO-E is now available in the Tkinter GUI under the top-level `ENTSO-E 欧洲` tab. The GUI reads all 26 datasets from `configs/entsoe/requests.json`, shows token status without revealing the token, displays bilingual dataset explanations and parameter meanings, supports Chinese-readable area/in-area/out-area selection, all-area expansion, border availability filtering from `configs/entsoe/border_availability.json`, date selection, dry-run request preview without token fields, export, clearing local records, and real collection through the existing spider/client/credential/storage path. Generic ENTSO-E rows are stored in `entsoe_records`; the compatibility day-ahead-price spider still uses `market_records`.

## 16. ENTSO-E 参数、时间与区域语义 / Parameter, Time, and Area Semantics

中文：

四类核心代码：

```text
documentType = 数据文档/主题，例如 A44 价格、A65 总负荷、A75 分类型实际发电
processType  = 数据阶段，例如 A16 实际、A01 日前、A31 周前、A32 月前、A33 年前
businessType = 时间序列的具体业务意义
psrType      = 电源或电网资源类型
```

代码不能任意组合。继续扩展时必须对照官方 Postman 或官方代码表验证组合。

时间规则：

- API `periodStart`/`periodEnd` 使用 UTC，格式 `yyyyMMddHHmm`。
- CLI 中 `start-date` 和 `end-date` 当前按 UTC 解释。
- `end-date` 是不包含的区间终点。
- ENTSO-E 有时返回与查询窗口相交的完整市场日序列，不能简单假设返回范围严格等于输入范围。
- 德国 6 月采用 UTC+2；完整德国本地交易日 00:00-24:00 对应前一日 22:00 UTC 到当日 22:00 UTC。
- 当前通用 parser 输出 UTC；尚未实现按区域自动转当地时区，这是明确的后续优化项。

区域规则：

- ENTSO-E area 不是简单国家代码，而是 EIC 标识的 BZN、CTA、MBA、IPA、LFA、REG、SNA 等电力区域。
- 日前电价通常需要 BZN 报价区。
- 平衡数据常需要 control area、imbalance price area 或 LFC area，不能默认国家报价区 EIC 一定适用。
- 跨境数据需要真实存在关系的 in/out 两个区域，方向有意义；FR -> DE-LU 和 DE-LU -> FR 是两次查询。
- 并非每个区域发布每种数据；合法请求返回 `No matching data found` 不代表 token 失效。
- 单机组和停运数据受披露门槛影响。
- DE-LU 当前报价区从 2018-10-01 开始；更早德国数据通常使用历史 `DE-AT-LU` EIC `10Y1001A1001A63L`。
- Transparency Platform 总体历史数据大约从 2015-01-05 开始，但具体最早日期取决于数据集、区域历史和数据提供情况。

当前 `ENTSOE_BIDDING_ZONES` 提供常用别名，但别名存在不等于适用于所有 spider。未知或特殊控制区应使用直接 EIC 参数。

## 17. ENTSO-E 官方资料入口 / Official ENTSO-E References

继续做 ENTSO-E 业务时优先浏览官方资料，不要依赖二手博客：

- 平台帮助和数据项目索引：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/17260622859412-Transparency-Platform-Help-page
- REST API 集成总目录：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/15692855254548-Sitemap-for-Restful-API-Integration
- 官方 Postman 集合，包含大量精确请求和响应样例：
  - https://documenter.getpostman.com/view/7009892/2s93JtP3F6
- 请求端点：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/15696677194644-Request-Endpoint
- 请求方法：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/15696643163924-Request-Methods
- 时间参数：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/12783280128404-Request-Parameters-Time-Interval
- 区域与 EIC 清单：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/15885757676308-Area-List-with-Energy-Identification-Code-EIC
- API 限流：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/12783148966036-API-Rate-Limit-Part-1
- 查询大小限制：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/15854536354964-API-Query-Size-Limit
- 获取 Web API security token：
  - https://transparencyplatform.zendesk.com/hc/en-us/articles/12845911031188-How-to-get-security-token

注意 Web API `securityToken` 与 File Library 的短期 Bearer token 是两套认证。当前爬虫使用前者。

## 18. 本轮容易忽略的实现细节 / Easy-to-Miss Implementation Details

中文：

- 项目实际主目录是 `D:\Code\myproject\powertrade_crawler`。不要误用旧副本 `C:\Users\hxy\Documents\powertrade_crawler`。
- 先阅读 `D:\Code\CODING_ENVIRONMENT.md`；本机使用 mise 管理 Python 3.12/Node 22，Python 环境为项目 `.venv`。
- CLI entry 只有：

```toml
powertrade = "powertrade_crawler.cli:app"
```

用户明确要求不要增加 `powertrade_crawler = "powertrade_crawler.cli:app"` 别名。

- 激活虚拟环境后可直接使用 `powertrade`；不激活时使用 `.\.venv\Scripts\powertrade.exe`。
- `.env`、`.auth/`、`data/` 均被忽略。提交前必须确认没有使用 `git add -f` 把凭据或数据库加入暂存区。
- 通用 ENTSO-E 数据表是 `entsoe_records`；原日前电价兼容 spider 仍写 `market_records`，这是当前有意保留的差异。
- ENTSO-E GUI 已接入，入口是 `powertrade gui` -> `ENTSO-E 欧洲`；不要再把“新增 ENTSO-E GUI 专页”当成待办。
- `MarketRecord` 的自然键不适合长期承载所有高频 ENTSO-E 时序，因此新增了 `EntsoeRecord` 和专用表。
- `EntsoeRecord.row_key` 使用稳定业务内容哈希，不包含采集时间，重复采集可 upsert。
- ENTSO-E XML namespace 会变化，解析使用 local-name，不要硬编码完整 namespace。
- 单一响应中可能有多个 `TimeSeries`、多个 `Period` 和不同 resolution；不要假设永远是 24 小时或 PT60M。
- 欧洲日前市场已可能是 PT15M，一天通常 96 点；不要再默认 24 点。
- 停运响应可能是 ZIP，官方 Postman 示例正文还可能把多个 XML 拼在展示文本中；真实 API 路径应按 ZIP/XML Content 处理。
- 对真实 API 冒烟测试使用一天、小区域和 `--dry-run`，避免大查询和无意写库。
- `powertrade init-db` 使用 SQLAlchemy `create_all` 创建新表。
- 当前完整检查基线：`ruff` 通过，`pytest` 57 passed（2026-06-26）；存在既有 `datetime.utcnow()` deprecation warnings，暂未统一清理。

## 19. 建议的下一步 / Recommended Next Steps

优先级建议：

1. 为 ENTSO-E 26 个配置化数据集逐一做小范围真实 API 验证，记录哪些区域有数据。
2. 把 ENTSO-E BZN、CTA、MBA、IPA、LFA 等区域角色拆成配置，不再只用单一 alias -> EIC 映射。
3. 增加历史区域映射，尤其是 `DE-AT-LU` 与 `DE-LU` 的 2018-10-01 边界。
4. 增加 IANA 时区映射，实现“按当地交易日查询”和 UTC/当地时间双字段。
5. 为停运数据解析 planned/unplanned、revision、cancelled、available capacity、resource name 等业务字段。
6. 增加负荷预测误差、风光预测误差、跨境商业计划与物理潮流偏差等分析层。
7. 继续优化 ENTSO-E GUI：真实可用边界清单、按数据集维护可用区域白名单、区域角色配置和更细的数据结果筛选。
8. 为 `credentials.py` 增加可选的 Windows 文件权限收紧或系统凭据库支持。
9. 缺少任何凭据时使用 `powertrade set-credential`，不要编辑 `.env`；以 `powertrade credentials-status` 的实时结果为准。
10. 提交新业务前继续运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
git status --short --ignored
```

## 20. 给未来 Codex 的一句话 / One Sentence for Future Codex

中文：

这是一个已经具备 CLI、SQLite、ORM、Tkinter GUI、PyInstaller 分发和多数据源采集能力的实际工具型项目；每次开发都要把“采集、存储、浏览、导出、打包、测试”作为一条完整链路来思考。

English:

This is a practical tool project with CLI, SQLite, ORM, Tkinter GUI, PyInstaller distribution, and multiple data sources; every change should be considered across the full chain of collection, storage, browsing, export, packaging, and tests.
