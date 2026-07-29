# Powertrade Crawler

一个面向国内外电力交易、电价和电力系统运行数据的本地采集、存储与浏览工具。

当前已接入的数据源包括：

- ENTSO-E Transparency Platform 欧洲电力数据。
- Elexon Insights API 英国电力数据。
- GridStatus API。
- 广州电力交易中心新闻。
- 微信小程序“易能电易查”的多个业务接口。

## 1. 环境搭建

建议使用 Python 3.11+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

如果后续要爬取强依赖浏览器渲染的网站，再安装：

```powershell
pip install -e ".[browser]"
playwright install chromium
```

## 2. 初始化数据库

```powershell
powertrade init-db
```

如果命令不可用，可以用：

```powershell
python -m powertrade_crawler.cli init-db
```

## 3. 查看已有爬虫

```powershell
powertrade list-spiders
```

当前保留的广州电力交易中心爬虫是：

```text
gzpec-news-combined
```

## 4. 运行示例爬虫

示例爬虫默认读取本地 fixture，不访问外网，方便先验证项目结构。

```powershell
powertrade crawl demo-market
```

运行后数据会写入 `data/powertrade.db` 的 `market_records` 表。

## 5. 合并爬取广州电力交易中心新闻

这个爬虫把索引页和详情页两个步骤合并为一步：

1. 抓取 `index.html` 到 `index_19.html` 的新闻链接。
2. 立即进入每条新闻详情页。
3. 判断新闻类型。
4. 按顺序保存文字块和图片块。
5. 写入一张合并表。

运行：

```powershell
powertrade init-db
powertrade crawl gzpec-news-combined
```

只预览，不写数据库：

```powershell
powertrade crawl gzpec-news-combined --dry-run
```

合并结果写入表：

```text
gzpec_news_records
```

这张表包含：

- `source`
- `category`
- `title`
- `url`
- `publish_date`
- `index_url`
- `news_type`
- `content_json`
- `collected_at`

`news_type` 的取值：

- `green_certificate`：标题以“绿证交易每周行情一览”开头
- `spot_market`：标题以“南方区域电力现货市场每周行情一览”开头
- `ordinary`：其他普通新闻

`content_json` 是按网页正文顺序保存的内容块：

- `text`：文字
- `image`：图片 URL，暂不下载图片，也不做 OCR

查看一条合并后的记录：

```powershell
python -c "import json; from powertrade_crawler.storage import get_session, GzpecNewsRecordRow; s=get_session(); r=s.query(GzpecNewsRecordRow).first(); print(r.news_type, r.publish_date, r.title); print(r.url); print(json.dumps(json.loads(r.content_json)[:5], ensure_ascii=False, indent=2)); s.close()"
```

## 6. GridStatus API 爬虫

GridStatus 使用官方 API，不爬网页前端。API key 统一保存到不会提交 Git 的鉴权目录：

```powershell
powertrade set-credential gridstatus
```

初始化数据库：

```powershell
powertrade init-db
```

查看自动注册的 GridStatus 命令：

```powershell
powertrade list-spiders
```

当前 MVP 已配置 5 个请求：

```text
gridstatus_datasets
gridstatus_caiso_fuel_mix
gridstatus_ercot_load
gridstatus_pjm_lmp_day_ahead_hourly_pseg
gridstatus_nyiso_fuel_mix_updates
```

运行示例：

```powershell
powertrade crawl gridstatus_datasets
powertrade crawl gridstatus_caiso_fuel_mix
powertrade crawl gridstatus_ercot_load
powertrade crawl gridstatus_pjm_lmp_day_ahead_hourly_pseg
powertrade crawl gridstatus_nyiso_fuel_mix_updates
```

所有 GridStatus 结果先统一写入：

```text
gridstatus_records
```

这张表会保存：

- `request_name`：具体 crawl 命令名
- `request_type`：请求类型，例如 datasets、dataset_query、dataset_location_query、dataset_updates
- `dataset`：GridStatus dataset id
- `location`：节点或区域，例如 PSEG
- `interval_start_utc`
- `interval_end_utc`
- `record_time_utc`
- `raw_json`：API 返回的原始行数据
- `collected_at`

新增 GridStatus 请求时，优先改配置文件：

```text
configs/gridstatus/requests.json
```

例如新增一个普通 dataset query：

```json
{
  "name": "gridstatus_aeso_load",
  "type": "dataset_query",
  "dataset": "aeso_load",
  "params": {
    "limit": 1000
  }
}
```

保存后重新运行：

```powershell
powertrade list-spiders
powertrade crawl gridstatus_aeso_load
```

GridStatus 时间参数统一使用 UTC。GUI 下载器支持明确设置 `start_time`、`end_time`、
`filter_column` 和 `filter_value`，首请求使用空 cursor，随后读取响应中的 cursor 自动翻页，
直至每个时间分块全部完成。当前账户验证可用的最大 `page_size` 为 50,000，GUI 默认使用该值；
小时级数据在设置等值筛选后默认按一年分块，以减少完整历史下载的请求次数。下载断点会保存时间、
筛选和分页设置，恢复时会校验查询条件，CSV 按页追加，SQLite 按主键增量写入。

客户端、批量下载和内部重试共享进程级限流器，请求间隔至少 2.1 秒，并额外限制为每分钟最多
30 次。GUI 对活跃数据集生成下载区间时，结束时间取程序运行当下的 UTC 时间，不再把本地数据集
目录中的旧更新时间当作今天。`ercot_spp_day_ahead_hourly` 下载窗口默认预填
`location_type = Load Zone`；选择“完整数据集”时，开始时间来自目录中的
`2010-12-01T06:00:00+00:00`。

## 7. 新增一个网站爬虫

每接入一个新网站，只需要做三件事：

1. 在 `src/powertrade_crawler/spiders/` 下新增一个文件，例如 `caiso.py`。
2. 继承 `BaseSpider`，实现 `crawl()` 方法，返回标准化后的数据模型。
3. 在 `src/powertrade_crawler/registry.py` 里注册这个 spider。

## 8. ENTSO-E Transparency Platform 爬虫

ENTSO-E 建议使用官方 Transparency Platform REST API，不直接抓网页前端。

使用隐藏输入命令保存 ENTSO-E token：

```powershell
powertrade set-credential entsoe
```

Elecheck authorization 同样保存在统一鉴权文件中：

```powershell
powertrade set-credential elecheck
```

查看凭据是否已经配置，不会显示具体内容：

```powershell
powertrade credentials-status
```

凭据统一保存在 `.auth/credentials.json`：

```json
{
  "gridstatus_api_key": "",
  "elecheck_authorization": "",
  "entsoe_security_token": "",
  "elexon_api_key": ""
}
```

`.auth/` 整个目录已加入 `.gitignore`，不会上传到远程仓库。`.env` 只保存请求间隔、超时和数据库地址等非敏感配置。

当前已经实现日前电价、负荷、发电、跨境交换、平衡和停运等常用数据：

```powershell
powertrade entsoe-datasets
powertrade entsoe-areas
powertrade entsoe-describe entsoe_actual_total_load
```

示例：

```powershell
powertrade crawl entsoe_day_ahead_prices --area DE-LU --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

其他常用数据集示例：

```powershell
powertrade crawl entsoe_actual_total_load --area DE-LU --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl entsoe_actual_generation_by_type --area DE-LU --psr-type B16 --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl entsoe_cross_border_physical_flows --in-area FR --out-area DE-LU --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

也可以直接传 EIC：

```powershell
powertrade crawl entsoe_day_ahead_prices --area-code 10Y1001A1001A82H --start-date 2026-06-01 --end-date 2026-06-02
```

### ENTSO-E GUI 使用

图形界面已接入 ENTSO-E。启动：

```powershell
powertrade gui
```

打开后选择顶部的 `ENTSO-E 欧洲` 页签。

这个页签提供：

- token 状态显示：只显示 `configured` 或 `missing`，不会显示 token 明文。
- 数据集下拉框：数据来自 `configs/entsoe/requests.json`，当前 26 个配置化数据集已全部接入 GUI。
- 数据集说明：显示中文说明、英文说明、参数含义和返回数据意义。
- 区域选择：下拉显示中文名称，例如 `德国-卢森堡 (DE-LU)`；单区域数据使用 `区域`，跨境数据使用 `来源区域` 和 `目标区域`。
- 全部区域：选择空白或 `全部区域` 时，执行爬取会展开为全部内置 area；跨境数据会展开为来源/目标组合，执行前会提示确认。
- 跨境可用性：如果存在 `configs/entsoe/border_availability.json`，已探测的数据集会根据可用边界动态过滤来源/目标区域，不可用组合不会进入可选列表。
- 日期选择：`start-date` 和 `end-date` 按 UTC 查询窗口发送，`end-date` 是不包含的结束边界。
- `dry-run 预览`：只展示不含 `securityToken` 的请求参数，不访问接口，也不写数据库。
- `执行爬取`：复用已有 spider/client/credentials/storage 逻辑，请求 ENTSO-E 并写入本地数据库。
- 结果浏览：通用 ENTSO-E 数据从 `entsoe_records` 读取；兼容的 `entsoe_day_ahead_prices` 从 `market_records` 读取。
- `导出数据`：按当前数据集、区域和日期筛选导出 CSV。
- `清除数据`：按当前数据集、区域和日期筛选清除本地记录，执行前会二次确认。

常用区域别名例如：

```text
DE-LU, FR, BE, NL, AT, CZ, PL, DK1, DK2, NO1, SE4
```

如果内置别名不适合某个控制区、报价区或特殊区域，CLI 可以直接使用 EIC 参数；GUI 当前主要提供中文可读的常用 area 下拉，真实数据可用性仍以 ENTSO-E API 返回为准。

跨境数据可用性可以用脚本探测并刷新本地配置：

```powershell
python scripts/probe_entsoe_border_availability.py --dataset entsoe_cross_border_physical_flows
```

脚本会读取 `.auth/credentials.json` 中的 ENTSO-E token，但不会打印或写出 token。输出文件：

```text
configs/entsoe/border_availability.json
```

当前已探测 5 个跨境数据集在 `2026-06-01` 到 `2026-06-02` 窗口内的 124 个候选跨境方向：

| 数据集 | 有数据 | 无数据 | 错误/超时 |
|---|---:|---:|---:|
| `entsoe_cross_border_physical_flows` | 122 | 2 | 0 |
| `entsoe_commercial_schedules` | 122 | 2 | 0 |
| `entsoe_forecasted_transfer_capacity` | 42 | 82 | 0 |
| `entsoe_offered_transfer_capacity` | 22 | 102 | 0 |
| `entsoe_transmission_outages` | 23 | 60 | 41 |

这个结论只代表该探测窗口，跨境数据可用性可能随日期、方向和数据集变化。

API 使用的核心参数：

- `documentType=A44`：Price Document，用于日日前价格。
- `contract_MarketAgreement.type=A01`：日拍卖 / day-ahead。
- `in_Domain` / `out_Domain`：Bidding Zone 的 EIC code。
- `periodStart` / `periodEnd`：UTC 时间，格式 `yyyyMMddHHmm`。
- `securityToken`：ENTSO-E 账号生成的 API token。

存储说明：

- `entsoe_day_ahead_prices` 是兼容 spider，仍输出 `MarketRecord` 并写入 `market_records`。
- 其他 ENTSO-E 配置化 spider 输出 `EntsoeRecord` 并写入 `entsoe_records`。
- `raw_json` 会保留 ENTSO-E XML 解析后的原始上下文，便于以后解释复杂业务字段。

完整的中英文调用说明、每个数据集的固定 API 参数、PSR 发电类型和跨境方向说明见：

```text
docs/ENTSOE_API_GUIDE.md
```

## 9. Elexon Insights API 英国数据源

Elexon 用于查询英国 GB 在 ENTSO-E 停止发布后的负荷、发电、价格和平衡机制数据。当前 Elexon Insights API 公开访问，不要求 API key；项目仍预留可选凭据：

```powershell
powertrade set-credential elexon
```

查看已经配置的数据集：

```powershell
powertrade elexon-datasets
powertrade elexon-describe elexon_initial_demand_outturn
```

示例：

```powershell
powertrade crawl elexon_initial_demand_outturn --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl elexon_generation_by_fuel_half_hourly --start-date 2026-06-01 --end-date 2026-06-02 --elexon-param fuelType=CCGT --dry-run
powertrade crawl elexon_system_prices --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl elexon_balancing_physical --start-date 2026-06-01 --end-date 2026-06-02 --elexon-param settlementPeriod=1 --dry-run
powertrade crawl elexon_loss_of_load_probability --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl elexon_interconnector_flows --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl elexon_net_balancing_services_adjustment --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

通用 Elexon 数据写入：

```text
elexon_records
```

GUI 已增加 `Elexon 英国` 页签，支持数据集选择、说明查看、日期范围、额外参数、dry-run 预览、执行爬取、刷新、导出和清除本地数据。

Elexon 页签中的说明区会直接解释 LOLP、de-rated margin、互联线名称、NETBSAD、DISBSAD、STOR、BMU 等术语；互联线参考接口 `/reference/interconnectors/all` 作为参数说明来源使用，不单独做参考数据页面。

完整说明见：

```text
docs/ELEXON_API_GUIDE.md
```

## 10. Elecheck 分析看板、指标汇总和定时任务

GUI 中的 `Elecheck 易能电易查` 页签新增 `现货价格分析` 子页，提供：

- 单一地区、单一日期的日前价格与实时价格日内折线图
- 仅按共同时间点计算的 `实时价 - 日前价` 价差图
- 最近 30 个自然日的日前、实时日均价趋势，缺失日期保留断点
- 前后有数据日期导航、完整度提示、CSV 和 PNG 导出

图表只读取 `elecheck_clear_price_records` 中 `endpoint = 'detail'` 且 `start_date = end_date` 的每日明细，不做跨地区或跨数据源比较，也不会插值或把缺失值补零。顶部通用 `分析看板` 已从 GUI 隐藏。

同一 Elecheck 页签还提供：

- `代理购电分析`：单省近 12/24 月或全部月份的费用构成柱、合计价趋势，以及所选月份全国合计价高低各 10 名和当前省份。
- `增量机制分析`：按电源类型对比各地区燃煤基准价与 26 年增量机制电价，并展示当前地区各电源类型价格。

代理购电图表只读取 `data_kind = 'national_table'`，排除全国极值和其他摘要记录；增量机制图表只反映当前快照，不生成虚假的历史趋势。两页均保持原始 `CNY/kWh` 口径，支持悬停、CSV 和 PNG 导出，采集完成后自动刷新。

GUI 顶层仍提供 `定时任务/数据维护` 页签。原有通用指标汇总代码和 CLI 继续保留，供后续其他数据源看板复用；如需重建指标，可运行：

```powershell
powertrade metrics-rebuild --start-date 2026-06-01 --end-date 2026-07-01
```

指标写入本地表：

```text
dashboard_daily_metrics
```

定时任务配置和运行日志写入本地表：

```text
scheduled_jobs
scheduled_job_runs
```

常用命令：

```powershell
powertrade schedule-create-templates
powertrade schedule-list
powertrade schedule-run 1 --force
powertrade schedule-install-windows 1
powertrade schedule-uninstall-windows 1
powertrade maintenance-run --analyze --vacuum
```

打包版支持 headless 定时运行：

```powershell
PowertradeCrawler.exe --headless schedule-run 1
```

调度日期会按数据源语义转换：ENTSO-E 和非快照 Elexon 使用结束日期不包含的区间；
Elecheck 现货价格会转换为逐日、结束日期包含的请求，保证结果可直接进入专题看板。
不支持日期窗口的 spider 必须使用 `date_mode=none`，并通过参数 JSON 提供月份等数据源专用参数。
失败的 `schedule-run` 会返回非零进程退出码；删除本地任务时，如已安装 Windows 任务，
会先同步卸载，卸载失败则保留本地任务定义。源码模式的 Windows 任务也统一通过
`desktop_launcher.py --headless` 启动，确保工作目录指向项目根目录。

定时任务不保存任何 API key 或 token，采集时仍统一读取 `.auth/credentials.json`。

## 11. Elecheck 电力市场分析 Agent MVP

第十一周在项目内自建了轻量单 Agent 框架，不依赖 OpenAI SDK 或任何 Agent SDK。
模型通过现有 `httpx` 调用硅基流动的 OpenAI 兼容接口；GUI 的
`Elecheck 易能电易查 -> 智能 Agent` 和 CLI 共用同一套工具注册、参数校验、审批、
幂等、持久会话和审计逻辑。

设置硅基流动凭据并检查运行条件：

```powershell
powertrade set-credential siliconflow
powertrade agent config show
powertrade agent doctor --json
```

默认保留两个可切换配置：

- 免费档位：`Qwen/Qwen2.5-7B-Instruct`
- 高质量档位：`deepseek-ai/DeepSeek-V4-Flash`

模型 ID、Endpoint、档位和工具协议都可修改，不依赖固定免费模型：

```powershell
powertrade agent config set --profile free
powertrade agent config set --profile advanced --reasoning-effort high
```

常用 Agent 命令：

```powershell
powertrade agent chat --message "分析江苏最新可用日的现货价格"
powertrade agent chat --message "帮我把 Elecheck 现货数据更新到今天"
powertrade agent sessions list
powertrade agent approvals list
powertrade agent approvals approve TOOL_CALL_ID
powertrade agent tools list
powertrade agent eval --mode offline --json
powertrade agent eval --mode live --limit 12 --output reports/agent-live-eval.json
```

只读查询、分析和写入预设目录 `exports/agent/<session>/` 的 CSV/PNG 导出可自动执行。
采集、创建/运行/启停 Elecheck 定时任务需要用户审批；安装或卸载 Windows 计划任务
需要二次确认。Agent 提供仅限 Elecheck 业务表的受控只读 SQL 查询，用于最高、最低、
排名、计数和筛选；查询使用表/字段/函数白名单、只读连接、单语句、超时和 200 行上限。
月度现货日均价最高/最低日期使用专用统计工具：先计算各地区日均价，再做地区等权平均，
并返回日期及地区覆盖，避免模型为常见业务问题反复试探 SQL。
Agent 不拥有 SQL 写入、删除业务数据、删除本地任务、Shell、任意文件访问、数据库维护
或凭据修改能力。API Key 不进入 SQLite、提示词、任务参数、日志或评测报告。

Agent 执行全部现货更新时会弹出下载进度对话框，按地区和逐日请求显示抓取/写入数量；
可选择“结束并保存”停止后续请求，已完成的逐日数据会保留。

完整架构、工具清单、审批规则和评测说明见：

```text
docs/AGENT_MVP_GUIDE.md
docs/AGENT_SECURITY_REVIEW.md
docs/AGENT_THREAT_MODEL.md
```


