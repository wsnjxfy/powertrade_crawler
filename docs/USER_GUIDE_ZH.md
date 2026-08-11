# Powertrade Crawler 中文用户使用指南

本文面向希望直接使用 Powertrade Crawler 的电力行业用户、教师、学生和数据分析人员。内容以
`v0.1.0` 为基线，重点说明怎样下载、首次启动、配置数据源、采集与分析数据、使用 Agent、设置
定时任务，以及出现常见问题时怎样处理。

如果你的目标是修改源码、增加数据源或重新打包，请阅读
[中文开发维护与接手指南](DEVELOPER_HANDOVER_GUIDE_ZH.md)。

## 1. 软件能做什么

Powertrade Crawler 是一个本地桌面工具，将以下能力放在同一个程序中：

- 采集 ENTSO-E 欧洲电力市场数据；
- 采集 Elexon 英国电力系统和市场数据；
- 采集 GridStatus 北美 ISO/RTO 数据；
- 采集 Elecheck 国内现货、代理购电和增量机制电价；
- 采集广州电力交易中心公开信息；
- 使用 SQLite 在本机保存、筛选和导出数据；
- 展示现货、代理购电、机制电价等专题图表；
- 创建来源级增量更新和 Windows 定时任务；
- 通过受控 Agent 查询、比较、导出或提出采集任务；
- 通过完全本地的混合 RAG 检索规则、数据集说明和公开文章。

软件是本地单用户桌面应用，不是云端多人协作平台。数据库、凭据和 Agent 会话默认保存在运行
软件的电脑上。

## 2. 推荐方式：使用 Windows 发布包

### 2.1 运行条件

- Windows 10 或 Windows 11，64 位系统；
- 至少预留约 500 MiB 磁盘空间；如果长期采集历史数据，应额外预留数 GiB 或更多空间；
- 普通功能不要求管理员权限；
- 软件目录必须允许当前用户写入；
- 浏览内置数据和使用本地 RAG 不要求联网；在线采集和在线 Agent 需要网络。

### 2.2 下载

从项目官方页面下载，不要使用来历不明的网盘包或单独的 EXE：

- [最新版 GitHub Release](https://github.com/wsnjxfy/powertrade_crawler/releases/latest)
- [项目主页](https://github.com/wsnjxfy/powertrade_crawler)

在 Release 页面下载名称类似下面的附件：

```text
PowertradeCrawler-vX.Y.Z-windows-x64-unsigned.zip
```

`Source code (zip)` 是源码快照，不是可直接双击运行的软件包。

### 2.3 核对 SHA-256

每个 Release 的说明中都会给出发布包 SHA-256。下载后在 ZIP 所在目录打开 PowerShell：

```powershell
Get-FileHash .\PowertradeCrawler-v0.1.0-windows-x64-unsigned.zip -Algorithm SHA256
```

将输出值与 Release 页面公布的值逐字符比较。文件名、版本或哈希不一致时不要运行。

`v0.1.0` 的官方 ZIP SHA-256 是：

```text
E421E3E1DD72AAE5796AB2F0338E16C31FACD2A617DEB0B1E8CA131D44C03330
```

后续版本应以对应 Release 页面公布的值为准，不能继续使用旧版本哈希。

### 2.4 解压和启动

1. 完整解压 ZIP，得到 `PowertradeCrawler` 文件夹。
2. 将整个文件夹放到桌面、文档等当前用户可写的位置。
3. 不要在压缩包预览窗口中直接运行。
4. 不要只复制 `PowertradeCrawler.exe`；程序还需要 `_internal` 中的运行库、模型和初始资源。
5. 双击 `PowertradeCrawler.exe`。

首次启动会在程序目录创建：

```text
.env
configs/
data/
```

只有在 `data/powertrade.db` 不存在时，程序才复制内置的轻量演示数据库；不会覆盖已经存在的
用户数据库。发布包不携带开发者的 API Key、Authorization、Agent 会话或定时任务。

### 2.5 未签名程序提示

当前 Windows 发布包没有 Authenticode 代码签名，因此可能出现：

- 浏览器提示“此文件不常下载”；
- Microsoft Defender SmartScreen 显示“Windows 已保护你的电脑”；
- Windows 11 Smart App Control 或学校、企业应用控制直接阻止运行。

未签名表示 Windows 无法确认发布者身份，不等于系统已经确认文件有病毒。处理原则是：

1. 只从官方 Release 下载；
2. 核对 SHA-256；
3. 使用 Microsoft Defender 扫描 ZIP 和解压目录；
4. 个人设备确认来源可信且系统允许时，才考虑使用“更多信息 → 仍要运行”；
5. 学校或企业受管理设备如果没有放行选项，应联系管理员，不要关闭安全软件或绕过单位策略。

## 3. 第一次使用的推荐顺序

建议按下面的路径熟悉软件：

```text
数据总览
  → API 配置向导
  → 选择一个数据源做小范围采集
  → 浏览、筛选、图表和导出
  → Agent
  → 定时任务
```

第一次不要直接下载多年、全区域或全数据集。先使用一个地区、一天或一个小数据集，确认凭据、
时间范围、单位和写入结果正确后再扩大范围。

## 4. 八个主要页面

| 页面 | 主要用途 | 首次使用建议 |
|---|---|---|
| 数据总览 | 查看五个来源的本地数据量、时间范围、质量状态和快捷入口 | 启动后先确认演示数据是否可见 |
| GridStatus | 浏览北美数据集目录，配置筛选、分页和时间范围并下载 | 先浏览内置目录，再配置 API Key |
| Elecheck 易能电易查 | 国内现货、代理购电、增量机制的分析、数据和独立 Agent | 先查看演示图表，再决定是否配置 Authorization |
| ENTSO-E 欧洲 | 选择欧洲数据集、区域和 UTC 日期范围，预览、采集、浏览和导出 | 先使用 `dry-run` 和单一区域 |
| Elexon 英国 | 查询英国需求、发电、价格、平衡、裕度和互联线数据 | 无需 Key，先使用一天范围 |
| 多数据源 Agent | 跨来源查询、受控比较、知识检索、导出和需审批的采集操作 | 先做只读问题，再尝试审批型操作 |
| 定时任务 / 数据维护 | 创建来源级增量任务、查看运行记录、安装 Windows 触发器和维护数据库 | 先“立即运行”验收，再安装触发器 |
| API 配置向导 | 查看每项能力需要什么账号，并隐藏输入、保存和检测凭据状态 | 只配置实际需要使用的来源 |

广州电力交易中心当前没有单独的顶层页面，其公开信息会进入本地数据库、数据总览和多来源知识
库；也可以通过 CLI 运行对应采集器。

## 5. API Key、账号和凭据

### 5.1 什么情况下需要配置

| 功能或来源 | 是否需要凭据 | 说明 |
|---|---|---|
| 启动软件、浏览演示数据、筛选和导出 | 不需要 | 完全本地运行 |
| Elexon 在线采集 | 不需要 | Insights API 当前公开访问 |
| 广州电力交易中心公开信息 | 不需要 | 读取公开网页 |
| GridStatus 在线采集 | 需要 | 需要 GridStatus API Key，数据范围还受账户套餐影响 |
| ENTSO-E 在线采集 | 需要 | 需要 Transparency Platform Security Token |
| Elecheck 在线采集 | 需要 | 需要有效 Authorization，目前没有公开自助开发者 Key 页面 |
| 两套 Agent | 需要额外服务 | 需要本机 `llm-router` 和至少一个可用的免费上游模型渠道 |

### 5.2 GUI 配置

进入“API 配置向导”，页面会按“实时采集必需”“Agent 功能必需”“可选”和“无需配置”分类。
保存后只显示 `configured` 或 `missing`，不会回显密钥原文。

### 5.3 CLI 配置

源码版也可以通过隐藏输入保存凭据：

```powershell
powertrade set-credential gridstatus
powertrade set-credential entsoe
powertrade set-credential elecheck
powertrade credentials-status
```

凭据保存在本机 `.auth/credentials.json`，不要将该文件发送给 Agent、上传 Git、加入截图或复制给
其他用户。LLM 上游平台 Key 只配置在项目外的本机路由器中，不写入本项目。

申请入口和详细步骤见 [API Key 与账号配置指南](API_KEY_SETUP_GUIDE.md)。

## 6. 五个数据源怎样使用

### 6.1 ENTSO-E 欧洲

1. 在 API 配置向导中保存 Security Token。
2. 进入“ENTSO-E 欧洲”。
3. 选择数据集，例如日前价格、负荷、按类型发电或跨境物理潮流。
4. 选择区域；跨境数据需要来源区域和目标区域。
5. 选择日期。当前请求窗口按 UTC 发送，结束日期通常是不包含的边界。
6. 先执行 `dry-run`，检查参数中不含凭据且区域、日期正确。
7. 再执行采集，完成后刷新、筛选或导出。

并非每一个“数据集 × 区域 × 日期”组合都有数据。返回 0 条可能是业务上无数据，不一定是程序
故障。欧洲不同竞价区的当地交易日不能简单等同于 UTC 自然日，做日度分析时应注明口径。

### 6.2 Elexon 英国

Elexon Insights API 当前不要求 Key。进入“Elexon 英国”后选择数据集和日期范围即可。建议：

- 系统价格、需求和燃料发电先查询一天；
- BMU、结算周期或大结果集应分日期、分参数查询；
- 快照类数据返回 0 条可能是正常情况；
- 先阅读页面中的 LOLP、de-rated margin、BMU、NETBSAD 等术语说明。

### 6.3 GridStatus 北美

1. 可以在无 Key、无网络时浏览内置数据集目录快照。
2. 在线下载前配置 GridStatus API Key。
3. 选择数据集、时间范围和必要的 `location`、`filter_column`、`filter_value`。
4. 首次测试选择短时间窗口。

下载器会按 cursor 自动分页，SQLite 按自然键增量写入。部分数据集受账户套餐、限流、最大页大小
和数据集自身参数约束。遇到 403 或 429 时不要连续重复点击。

### 6.4 Elecheck 国内业务

Elecheck 页面包含六个业务子页：

- 现货价格分析；
- 现货价格数据；
- 代理购电分析；
- 代理购电数据；
- 增量机制分析；
- 增量机制数据。

现货分析按单一地区、单一日期显示日前价、实时价、共同时间点价差和最近 30 个自然日日均趋势。
缺失点不会补零或插值。代理购电分析使用全国表记录展示费用构成、合计价趋势和省份排名；数据从
2024-02 起。增量机制页面展示当前快照，不会虚构历史趋势。

Authorization 失效时一般表现为 HTTP 401，需要在 API 配置向导中重新保存有效值。软件不会代替
用户注册、登录或绕过访问权限。

### 6.5 广州电力交易中心

公开信息采集器会抓取索引和详情，保存正文中的文字块和图片 URL；不会下载图片或执行 OCR。
源码版常用命令：

```powershell
powertrade crawl gzpec-news-combined --dry-run
powertrade crawl gzpec-news-combined
```

网页结构或站点可访问性变化时可能需要维护采集器。

## 7. 数据浏览、图表和导出

- 先确认页面当前的数据集、地区、日期和单位，再解释图表；
- “无新数据”表示请求成功但没有新增记录，不等同于网络失败；
- 重复采集使用业务自然键 upsert，正常情况下不会不断制造重复行；
- 日前价和实时价的价差只按共同时间点计算；
- 缺失日期保留断点，不补零、不插值；
- 不同币种、单位、时间粒度和市场口径的数据默认只并列展示，不自动计算没有意义的价差；
- 导出失败时先关闭正在占用目标 CSV/PNG 的 Excel、WPS 或图片程序。

## 8. 怎样使用 Agent

项目包含两套独立 Agent：

- Elecheck Agent：只处理 Elecheck 现货、代理购电、增量机制及相关采集、导出和任务；
- 多数据源 Agent：处理五个来源的查询、比较、受控采集、定时任务和本地知识检索。

### 8.1 使用前检查

1. 本机 `llm-router` 已启动；
2. 至少一个免费模型渠道可用；
3. 涉及在线数据时，对应来源凭据已经配置；
4. 问题中尽量明确数据源、地区、日期、指标和单位。

源码版可以运行：

```powershell
powertrade agent doctor --json
powertrade market-agent doctor --online
```

### 8.2 推荐提问方式

```text
分析江苏最新可用日的日前与实时价格，并说明数据完整度。
列出本地 ENTSO-E 德国日前价格覆盖的日期范围。
并列展示江苏和德国某日价格，同时说明币种和时间口径为什么不能直接相减。
检索本地知识库中关于绿证交易的公开资料，并给出引用来源。
```

### 8.3 权限和审批

只读查询、确定性统计和受控目录导出通常可以自动执行。采集、更新业务数据、创建或运行任务等
操作需要用户审批；安装或卸载 Windows 计划任务需要更强确认。

Agent 不具备以下能力：

- 任意 SQL 写入；
- Shell 命令；
- 删除业务数据；
- 任意文件浏览；
- 修改或回显凭据；
- 免费渠道不可用时自动调用付费模型。

失败回答应说明“为什么没完成、有没有修改数据、下一步怎么办”。审批前务必检查工具名称、完整
参数和副作用，不要只看自然语言描述。

## 9. 本地混合 RAG

多数据源 Agent 的知识库使用内置中文向量模型和 SQLite 全文索引，运行时不需要联网下载模型。
它索引的是受控白名单中的公开文章、数据集目录和请求定义，不会向量化业务价格时序、凭据、Agent
会话、审批、日志或用户文件。

源码版常用命令：

```powershell
powertrade market-agent rag status --json
powertrade market-agent rag search "绿证交易" --top-k 5 --json
```

正常状态应显示 `ready`；正常检索模式为 `hybrid`。如果向量模型不可用，系统会明确提示并降级为
全文检索，而不是悄悄返回伪造结果。详细说明见 [本地混合 RAG 指南](RAG_GUIDE.md)。

## 10. 定时任务和自动更新

“启用本地任务”和“安装 Windows 触发器”是两件不同的事：

- 启用本地任务：允许任务定义被运行；
- Windows 触发器已安装：软件关闭后，Windows 仍会按时启动任务。

推荐流程：

1. 在“定时任务 / 数据维护”选择来源级快捷更新；
2. 设置每日时间，不同来源错开 5 至 10 分钟；
3. 先点击“立即运行”，检查本次运行详情；
4. 确认凭据、日期窗口和写入数量正确；
5. 再安装 Windows 自动触发器；
6. 刷新页面，确认状态为“已安装”。

同一任务并发触发时，第二次运行会被跳过并记录 `already_running`。多步骤来源更新中，一个子步骤
失败不会阻断其他步骤，最终可能显示 `partial`，应在运行详情中只重试失败部分。

源码版常用命令：

```powershell
powertrade schedule-list
powertrade schedule-run 1 --force
powertrade schedule-install-windows 1
powertrade schedule-uninstall-windows 1
```

## 11. 从源码运行

如果需要查看源码或当前没有适合的 Windows 发布包，可以使用 Python 3.11+：

```powershell
git clone https://github.com/wsnjxfy/powertrade_crawler.git
cd powertrade_crawler

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
Copy-Item .env.example .env

powertrade init-db
powertrade gui
```

常用检查和操作：

```powershell
powertrade credentials-status
powertrade list-spiders
powertrade list-tables
powertrade help-commands
```

只有未来接入强依赖网页渲染的数据源时才需要浏览器依赖：

```powershell
pip install -e ".[browser]"
playwright install chromium
```

当前五个正式来源主要使用官方 API 或普通 HTTP 页面，不应为了日常使用无条件安装 Playwright。

## 12. 数据备份、升级和卸载

### 12.1 备份

关闭软件后备份：

```text
data/powertrade.db
.auth/credentials.json
```

数据库可以备份到安全存储；凭据文件应加密保存，不要放入公开网盘或代码仓库。不要在软件正在
写入数据库时直接复制文件。

### 12.2 升级发布包

推荐使用“新目录迁移”，不要直接覆盖旧 `_internal`：

1. 关闭软件并备份旧数据库和凭据；
2. 将新版本完整解压到新的文件夹；
3. 先保留旧文件夹，以便回退；
4. 将旧 `data/powertrade.db` 和 `.auth/credentials.json` 复制到新目录对应位置；
5. 配置目录只迁移确实由用户修改的内容，不要覆盖新版本的数据集目录；
6. 启动新版本，检查数据总览、凭据状态和定时任务；
7. 验证完成后再归档旧目录。

### 12.3 卸载

1. 在“定时任务 / 数据维护”卸载已安装的 Windows 触发器；
2. 关闭软件；
3. 需要保留历史数据时先备份数据库；
4. 删除整个程序文件夹。

本项目没有全局安装器，删除文件夹不会自动清理仍留在 Windows 任务计划程序中的触发器。

## 13. 常见问题

| 现象 | 可能原因 | 建议处理 |
|---|---|---|
| 无新数据 | 请求成功，但筛选范围没有新记录 | 调整日期、地区或数据集，不要连续轰击接口 |
| 连接失败或断网 | 网络、代理、目标站点或本机 LLM 网关不可用 | 检查网络和服务状态，稍后重试 |
| 请求超时 | 日期过大、站点响应慢 | 缩小范围并分批采集 |
| HTTP 401 | 凭据缺失、过期或无效 | 在 API 配置向导重新保存有效凭据 |
| HTTP 403 | 账户无权限或套餐不包含 | 检查来源官方权限和套餐 |
| HTTP 429 | 请求频率或额度受限 | 等待后重试，减少并发并错开任务 |
| HTTP 5xx | 第三方服务临时异常 | 稍后重试，查看多步骤任务是否部分成功 |
| 已有任务运行中 | 同一任务被互斥锁保护 | 等待当前运行结束 |
| Windows 触发器缺失 | 任务未安装或被外部删除 | 刷新状态并重新安装触发器 |
| 免费模型不可用 | 路由器未启动或所有免费渠道不可用 | 启动路由器并刷新渠道，不会自动改用付费模型 |
| 数据库不可写 | 软件目录只读或数据库被占用 | 移到可写目录，关闭占用程序 |
| CSV/PNG 导出失败 | 文件被 Excel、WPS 或图片程序占用 | 关闭目标文件后重试 |
| SmartScreen 或企业策略阻止 | EXE 未签名或缺少信誉 | 核对来源和哈希；受管设备联系管理员 |

## 14. 当前限制

- 在线数据受第三方接口、账号、套餐、限流和网页结构变化影响；
- ENTSO-E 当前统一保存 UTC，尚未自动替所有区域换算当地交易日；
- Elecheck Authorization 需要授权方提供；
- 广州文章中的图片只保存 URL，不下载、不 OCR；
- Agent 只调用注册的受控工具，不是任意电脑操作助手；
- 当前公开 Windows 包未签名，不能保证通过所有企业应用控制策略；
- 已完成本机中文空格路径和离线冻结态验收，不代表覆盖了所有物理干净机和第三方环境。

## 15. 延伸文档

- [最终交付使用与验收指南](FINAL_DELIVERY_GUIDE.md)
- [API Key 与账号配置指南](API_KEY_SETUP_GUIDE.md)
- [ENTSO-E 数据集指南](ENTSOE_API_GUIDE.md)
- [Elexon 数据集指南](ELEXON_API_GUIDE.md)
- [Agent MVP 指南](AGENT_MVP_GUIDE.md)
- [本地混合 RAG 指南](RAG_GUIDE.md)
- [调度验收报告](SCHEDULE_ACCEPTANCE_REPORT.md)
- [Windows 发布门禁](WINDOWS_RELEASE_GATE.md)
