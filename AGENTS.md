# Powertrade Crawler Codex Project Guide

> 中文：这份文档是给未来新开的 Codex 对话长期参考用的“项目万用介绍和协作指南”。开始改代码前，优先阅读本文，再按需阅读 README、源码和测试。
>
> English: This document is the long-lived project briefing and collaboration guide for future Codex sessions. Read it first before changing code, then inspect the README, source files, and tests as needed.

## 1. 项目概览 / Project Overview

中文：

`powertrade_crawler` 是一个 Python 数据采集、存储、浏览和打包分发项目，目标是建设电力交易、电价、市场数据的本地采集与查看工具。当前重点数据源包括 GridStatus、广州电力交易中心新闻，以及微信小程序“易能电易查”的多个业务接口。

English:

`powertrade_crawler` is a Python project for collecting, storing, browsing, and distributing power-market and electricity-price data. Its current data sources include GridStatus, Guangzhou Power Exchange news, and several business APIs from the WeChat mini-program "Elecheck / 易能电易查".

核心技术栈 / Main stack:

- Python 3.11
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
- `models.py`: SQLAlchemy ORM 模型。
- `storage.py`: 建表、会话、写入、upsert、导出等持久化逻辑。
- `registry.py`: spider 注册表，决定 `list-spiders` 和 `crawl` 能看到什么。
- `cli.py`: Typer 命令行入口。
- `gui.py`: Tkinter GUI，包含数据浏览、筛选、导出、清空、采集进度窗口等。
- `config.py`: 环境变量与配置。
- `elecheck_auth.py`: Elecheck Authorization 读取和失效判断。

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

## 7. Authorization 逻辑 / Authorization Logic

中文：

Elecheck 接口需要 Authorization。当前策略：

- `.env` 中可配置 `ELECHECK_AUTHORIZATION`。
- 程序一直使用本地保存的 token。
- 不再按 24 小时主动失效。
- 只有接口返回 HTTP 401 时，才认为授权失效。
- GUI 失效提示为：`授权已过期，请联系管理员更新授权文件。`

不要把真实 `.env` 或真实 token 提交到仓库。`.env.example` 只能放占位符。

English:

Elecheck APIs require Authorization. Current behavior:

- `ELECHECK_AUTHORIZATION` can be set in `.env`.
- The program keeps using the locally saved token.
- The token is no longer proactively expired after 24 hours.
- Only HTTP 401 means the authorization is treated as invalid.
- GUI message: `授权已过期，请联系管理员更新授权文件。`

Never commit the real `.env` file or real tokens. `.env.example` should contain placeholders only.

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
src/powertrade_crawler/spiders/elecheck.py
src/powertrade_crawler/models.py
src/powertrade_crawler/storage.py
src/powertrade_crawler/cli.py
src/powertrade_crawler/gui.py
src/powertrade_crawler/registry.py
src/powertrade_crawler/elecheck_auth.py
tests/test_elecheck_spider.py
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

## 15. 给未来 Codex 的一句话 / One Sentence for Future Codex

中文：

这是一个已经具备 CLI、SQLite、ORM、Tkinter GUI、PyInstaller 分发和多数据源采集能力的实际工具型项目；每次开发都要把“采集、存储、浏览、导出、打包、测试”作为一条完整链路来思考。

English:

This is a practical tool project with CLI, SQLite, ORM, Tkinter GUI, PyInstaller distribution, and multiple data sources; every change should be considered across the full chain of collection, storage, browsing, export, packaging, and tests.
