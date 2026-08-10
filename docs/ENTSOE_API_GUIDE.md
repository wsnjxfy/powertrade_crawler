# ENTSO-E REST API 常用数据调用指南

# ENTSO-E REST API Guide for Common Datasets

本项目使用 ENTSO-E Transparency Platform 官方 REST API：

This project uses the official ENTSO-E Transparency Platform REST API:

```text
https://web-api.tp.entsoe.eu/api
```

官方资料 / Official references:

- [Transparency Platform Help](https://transparencyplatform.zendesk.com/hc/en-us/articles/17260622859412-Transparency-Platform-Help-page)
- [REST API Sitemap](https://transparencyplatform.zendesk.com/hc/en-us/articles/15692855254548-Sitemap-for-Restful-API-Integration)
- [Official Postman collection](https://documenter.getpostman.com/view/7009892/2s93JtP3F6)
- [Area List with EIC codes](https://transparencyplatform.zendesk.com/hc/en-us/articles/15885757676308-Area-List-with-Energy-Identification-Code-EIC)

## 1. 准备 / Setup

使用隐藏输入命令配置 token：

Configure the token with the hidden-input credential command:

```powershell
powertrade set-credential entsoe
```

token 会保存在 `.auth/credentials.json`，整个 `.auth/` 目录已被 Git 忽略。

The token is stored in `.auth/credentials.json`. The entire `.auth/` directory is ignored by Git.

不要把 token 写进普通命令参数、代码、截图或 Git。

Do not put the token in normal command arguments, source code, screenshots, or Git.

查看已经实现的数据集：

List implemented datasets:

```powershell
powertrade entsoe-datasets
```

查看某个数据集的中英文说明和固定 API 参数：

Describe one dataset and its fixed API parameters:

```powershell
powertrade entsoe-describe entsoe_actual_total_load
```

查看项目内置的区域别名和 EIC：

List bundled area aliases and EIC codes:

```powershell
powertrade entsoe-areas
```

## 2. 通用命令结构 / General Command Structure

单区域数据 / Single-area data:

```powershell
powertrade crawl DATASET `
  --area DE-LU `
  --start-date 2026-06-01 `
  --end-date 2026-06-02 `
  --dry-run
```

跨境数据 / Cross-border data:

```powershell
powertrade crawl DATASET `
  --in-area FR `
  --out-area DE-LU `
  --start-date 2026-06-01 `
  --end-date 2026-06-02 `
  --dry-run
```

直接使用 EIC / Use EIC codes directly:

```powershell
powertrade crawl DATASET `
  --area-code 10Y1001A1001A82H `
  --start-date 2026-06-01 `
  --end-date 2026-06-02 `
  --dry-run
```

```powershell
powertrade crawl DATASET `
  --in-area-code 10YFR-RTE------C `
  --out-area-code 10Y1001A1001A82H `
  --start-date 2026-06-01 `
  --end-date 2026-06-02 `
  --dry-run
```

`start-date` 和 `end-date` 按 UTC 发送，`end-date` 是不包含的区间终点。日期范围过长时，爬虫会依据数据集配置自动分段。

`start-date` and `end-date` are sent as UTC. `end-date` is the exclusive interval boundary. Long ranges are automatically split according to the dataset configuration.

去掉 `--dry-run` 后，通用 ENTSO-E 记录写入 `entsoe_records` 表。兼容命令 `entsoe_day_ahead_prices` 仍写入原有的 `market_records` 表。

Without `--dry-run`, generic ENTSO-E records are written to `entsoe_records`. The compatibility command `entsoe_day_ahead_prices` still writes to `market_records`.

兼容日前电价命令会明确请求 classification sequence 1。ENTSO-E 可能为同一报价区和时段发布多个拍卖序列；固定标准序列可以避免不同价格在 `market_records` 中相互覆盖。

The compatibility day-ahead command explicitly requests classification sequence 1. ENTSO-E may publish multiple auction sequences for the same bidding zone and interval; keeping the standard sequence separate prevents different prices from overwriting one another in `market_records`.

## 3. 市场数据 / Market Data

### 3.1 日前电价 / Day-ahead Energy Prices

命令 / Command:

```powershell
powertrade crawl entsoe_day_ahead_prices --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

固定参数 / Fixed parameters:

```text
documentType=A44
contract_MarketAgreement.Type=A01
in_Domain=<area EIC>
out_Domain=<same area EIC>
```

中文：返回报价区每个市场时段的日前批发电力出清价格。它可用于分析现货价格、峰谷价差、负价格和价格波动。

English: Returns cleared day-ahead wholesale electricity prices for each market time unit. It supports spot-price, spread, negative-price, and volatility analysis.

### 3.2 日前可供交易的跨境容量 / Day-ahead Offered Transfer Capacity

命令 / Command:

```powershell
powertrade crawl entsoe_offered_transfer_capacity --in-area FR --out-area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

固定参数 / Fixed parameters:

```text
documentType=A31
auction.Type=A01
contract_MarketAgreement.Type=A01
in_Domain=<from EIC>
out_Domain=<to EIC>
```

中文：返回跨境边界向日前市场提供的可交易传输容量，反映市场耦合可使用的网络空间。

English: Returns transfer capacity offered to the day-ahead market on a border, representing network capacity available to market coupling.

## 4. 负荷数据 / Load Data

### 4.1 实际总负荷 / Actual Total Load

```powershell
powertrade crawl entsoe_actual_total_load --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A65
processType=A16
outBiddingZone_Domain=<area EIC>
```

中文：实际发生的系统总用电负荷，用于绘制负荷曲线、寻找峰值需求并分析供需平衡。

English: Measured total system demand, used for load curves, peak-demand detection, and supply-demand analysis.

### 4.2 日前总负荷预测 / Day-ahead Total Load Forecast

```powershell
powertrade crawl entsoe_day_ahead_load_forecast --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A65
processType=A01
outBiddingZone_Domain=<area EIC>
```

中文：系统运营商日前发布的负荷预测。与实际总负荷比较可计算预测误差。

English: Day-ahead demand forecast published by the system operator. Compare it with actual load to measure forecast error.

### 4.3 周前总负荷预测 / Week-ahead Total Load Forecast

```powershell
powertrade crawl entsoe_week_ahead_load_forecast --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-08 --dry-run
```

```text
documentType=A65
processType=A31
outBiddingZone_Domain=<area EIC>
```

中文：未来一周的总负荷预测，适用于周度运行计划和需求趋势分析。

English: Forecast total demand for the coming week, useful for weekly operations and demand-trend analysis.

### 4.4 月前总负荷预测 / Month-ahead Total Load Forecast

```powershell
powertrade crawl entsoe_month_ahead_load_forecast --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-07-01 --dry-run
```

```text
documentType=A65
processType=A32
outBiddingZone_Domain=<area EIC>
```

中文：未来一个月的总负荷预测，用于月度电量安排和中期需求判断。

English: Month-ahead total-demand forecast for monthly energy planning and medium-term demand assessment.

### 4.5 年前总负荷预测 / Year-ahead Total Load Forecast

```powershell
powertrade crawl entsoe_year_ahead_load_forecast --area DE-LU `
  --start-date 2026-01-01 --end-date 2027-01-01 --dry-run
```

```text
documentType=A65
processType=A33
outBiddingZone_Domain=<area EIC>
```

中文：未来一年的长期负荷预测，用于年度供需平衡和资源规划。

English: Long-term demand forecast for annual adequacy analysis and resource planning.

### 4.6 年前容量裕度预测 / Year-ahead Forecast Margin

```powershell
powertrade crawl entsoe_year_ahead_forecast_margin --area DE-LU `
  --start-date 2026-01-01 --end-date 2027-01-01 --dry-run
```

```text
documentType=A70
processType=A33
outBiddingZone_Domain=<area EIC>
```

中文：预计可用发电资源与需求之间的裕度，数值较低时可能表示资源充裕度风险。

English: Expected margin between available generation and demand. A low margin may indicate adequacy risk.

## 5. 发电数据 / Generation Data

### 5.1 按发电类型划分的装机容量 / Installed Capacity per Production Type

```powershell
powertrade crawl entsoe_installed_capacity_by_type --area DE-LU `
  --start-date 2026-01-01 --end-date 2027-01-01 --dry-run
```

```text
documentType=A68
processType=A33
in_Domain=<area EIC>
```

中文：按核电、燃气、煤电、水电、风电、光伏等类型统计装机容量，用于研究电源结构。

English: Installed capacity grouped by nuclear, gas, coal, hydro, wind, solar, and other production types.

### 5.2 按发电类型划分的实际发电量 / Actual Generation per Production Type

```powershell
powertrade crawl entsoe_actual_generation_by_type --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A75
processType=A16
in_Domain=<area EIC>
```

中文：返回各能源类型的实际发电出力，可用于计算新能源占比、火电调节和电源组合。

English: Returns actual output by production type for renewable share, thermal flexibility, and generation-mix analysis.

限定一种能源类型 / Filter one production type:

```powershell
powertrade crawl entsoe_actual_generation_by_type --area DE-LU --psr-type B16 `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

### 5.3 按发电机组划分的实际发电量 / Actual Generation per Generation Unit

```powershell
powertrade crawl entsoe_actual_generation_by_unit --area BE `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A73
processType=A16
in_Domain=<area EIC>
```

中文：在披露门槛允许时返回单台机组的实际出力。并非所有区域和机组都会提供数据。

English: Returns actual output of individual generation units where disclosure thresholds permit. Coverage varies by area and unit.

### 5.4 日前发电预测 / Day-ahead Generation Forecast

```powershell
powertrade crawl entsoe_day_ahead_generation_forecast --area BE `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A71
processType=A01
in_Domain=<area EIC>
```

中文：区域日前总发电预测，可与实际发电和负荷预测结合分析计划偏差。

English: Aggregate day-ahead generation forecast, useful with actual generation and load forecasts for schedule-deviation analysis.

### 5.5 风电和光伏发电预测 / Wind and Solar Generation Forecast

```powershell
powertrade crawl entsoe_wind_solar_forecast --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A69
processType=A01
in_Domain=<area EIC>
```

中文：返回风电和光伏预测出力。响应中的 `psr_type` 用于区分陆上风电、海上风电和光伏。

English: Returns wind and solar generation forecasts. The response `psr_type` distinguishes onshore wind, offshore wind, and solar.

### 5.6 水库与水电储能 / Water Reservoirs and Hydro Storage

```powershell
powertrade crawl entsoe_water_reservoirs --area NO1 `
  --start-date 2026-06-01 --end-date 2026-07-01 --dry-run
```

```text
documentType=A72
processType=A16
in_Domain=<area EIC>
```

中文：返回水库和水电储能设施的蓄能或库容信息，用于分析水电季节性和可调节能力。

English: Returns reservoir and hydro-storage energy information for seasonal hydro and flexibility analysis.

## 6. 输电和跨境交换 / Transmission and Cross-border Exchange

跨境命令的方向是：

The border direction is:

```text
--in-area  = source/from domain
--out-area = destination/to domain
```

反向流量需要交换两个参数。

Reverse the two parameters to query the opposite direction.

### 6.1 跨境物理潮流 / Cross-border Physical Flows

```powershell
powertrade crawl entsoe_cross_border_physical_flows --in-area FR --out-area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A11
in_Domain=<from EIC>
out_Domain=<to EIC>
```

中文：两个区域之间实际测量的物理电力流。它反映电网真实运行，不等同于商业交易计划。

English: Measured physical power flow between two domains. It represents grid operation and is not necessarily equal to commercial schedules.

### 6.2 跨境商业计划交换 / Commercial Schedules

```powershell
powertrade crawl entsoe_commercial_schedules --in-area FR --out-area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A09
in_Domain=<from EIC>
out_Domain=<to EIC>
```

中文：市场参与者计划执行的跨境商业电力交换。它与物理潮流的差异可能来自环流和电网物理约束。

English: Scheduled commercial exchange between two domains. Differences from physical flow can arise from loop flows and network physics.

### 6.3 预测跨境传输容量 / Forecasted Transfer Capacity

```powershell
powertrade crawl entsoe_forecasted_transfer_capacity --in-area FR --out-area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A61
contract_MarketAgreement.Type=A01
in_Domain=<from EIC>
out_Domain=<to EIC>
```

中文：日前市场周期内预计可用的跨境传输容量，用于判断交易边界是否紧张。

English: Forecast transfer capacity for the day-ahead horizon, useful for identifying constrained borders.

## 7. 平衡市场 / Balancing

平衡数据通常使用控制区而不是报价区。项目允许使用同一个区域别名，但某些国家可能需要直接传入官方控制区 EIC。

Balancing data usually uses a control area rather than a bidding zone. The same alias may work for some countries; otherwise pass the official control-area EIC directly.

### 7.1 不平衡价格 / Imbalance Prices

```powershell
powertrade crawl entsoe_imbalance_prices --area AT `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A85
controlArea_Domain=<area EIC>
```

中文：正负不平衡电量的结算价格，用于分析偏差成本和实时市场风险。

English: Settlement prices for positive and negative imbalances, used for deviation-cost and real-time risk analysis.

### 7.2 总不平衡电量 / Total Imbalance Volumes

```powershell
powertrade crawl entsoe_total_imbalance_volumes --area AT `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A86
controlArea_Domain=<area EIC>
```

中文：控制区内正负不平衡电量总量，反映实时供需偏差的方向和规模。

English: Total positive and negative imbalance volumes, indicating the direction and magnitude of real-time system imbalance.

### 7.3 已激活平衡能源价格 / Prices of Activated Balancing Energy

```powershell
powertrade crawl entsoe_activated_balancing_energy_prices --area BE `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A84
processType=A16
businessType=A96
controlArea_Domain=<area EIC>
```

中文：系统运营商实际激活的平衡能源价格，用于分析实时调节资源成本。

English: Prices of balancing energy activated by the system operator, used to study real-time flexibility costs.

### 7.4 已签约备用容量及价格 / Volumes and Prices of Contracted Reserves

```powershell
powertrade crawl entsoe_contracted_reserves --area CZ `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A81
businessType=B95
Type_MarketAgreement.Type=A01
processType=A52
controlArea_Domain=<area EIC>
```

中文：系统运营商采购的备用容量数量和价格，用于研究辅助服务采购及容量成本。

English: Procured reserve-capacity volumes and prices for balancing services and reserve-cost analysis.

## 8. 停运和不可用数据 / Outages and Unavailability

停运数据可能包含计划停运、非计划停运、修订版本和取消记录。应结合响应中的业务类型、状态、创建时间、修订号和可用容量字段判断。

Outage data may contain planned outages, unplanned outages, revisions, and cancellations. Interpret it together with business type, status, creation time, revision number, and available-capacity fields.

### 8.1 生产单元不可用 / Unavailability of Production Units

```powershell
powertrade crawl entsoe_production_unit_outages --area BE `
  --start-date 2026-06-01 --end-date 2026-07-01 --dry-run
```

```text
documentType=A77
BiddingZone_Domain=<area EIC>
```

中文：生产单元计划或非计划不可用及能力下降信息，用于评估潜在供应缺口。

English: Planned and unplanned production-unit unavailability and derating information for supply-risk analysis.

### 8.2 发电机组不可用 / Unavailability of Generation Units

```powershell
powertrade crawl entsoe_generation_unit_outages --area BE `
  --start-date 2026-06-01 --end-date 2026-07-01 --dry-run
```

```text
documentType=A80
BiddingZone_Domain=<area EIC>
```

中文：达到披露门槛的具体发电机组停运信息。它通常比生产类型聚合数据更细。

English: Unit-level generation outages where disclosure thresholds are met. It is generally more granular than production-type aggregation.

### 8.3 用电单元聚合不可用 / Aggregated Unavailability of Consumption Units

```powershell
powertrade crawl entsoe_consumption_unit_outages --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-07-01 --dry-run
```

```text
documentType=A76
BiddingZone_Domain=<area EIC>
```

中文：大型用电单元不可用能力的聚合披露，可用于分析工业负荷中断对需求的影响。

English: Aggregated unavailability of large consumption units, useful for assessing industrial-demand interruptions.

### 8.4 输电基础设施不可用 / Unavailability of Transmission Infrastructure

```powershell
powertrade crawl entsoe_transmission_outages --in-area BE --out-area FR `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

```text
documentType=A78
in_Domain=<from EIC>
out_Domain=<to EIC>
```

中文：影响跨区输电设施的计划和非计划停运，用于分析联络线容量下降和区域价差风险。

English: Planned and unplanned transmission outages affecting a border, useful for capacity and price-spread risk analysis.

### 8.5 海上电网不可用 / Unavailability of Offshore Grid Infrastructure

```powershell
powertrade crawl entsoe_offshore_grid_outages --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-07-01 --dry-run
```

```text
documentType=A79
BiddingZone_Domain=<area EIC>
```

中文：海上电网及其连接资源的不可用信息，用于分析海上风电送出和并网风险。

English: Offshore-grid unavailability affecting connected resources, useful for offshore-wind export and connection-risk analysis.

## 9. 常用 PSR 发电类型 / Common PSR Production Types

`--psr-type` 会向 API 添加 `psrType=<code>`。常用代码如下：

`--psr-type` adds `psrType=<code>` to the API request. Common codes:

| Code | 中文 | English |
|---|---|---|
| `B01` | 生物质 | Biomass |
| `B02` | 褐煤 | Fossil Brown coal/Lignite |
| `B03` | 煤制气 | Fossil Coal-derived gas |
| `B04` | 天然气 | Fossil Gas |
| `B05` | 硬煤 | Fossil Hard coal |
| `B06` | 石油 | Fossil Oil |
| `B09` | 地热 | Geothermal |
| `B10` | 抽水蓄能 | Hydro Pumped Storage |
| `B11` | 径流式及水库水电 | Hydro Run-of-river and poundage |
| `B12` | 水库式水电 | Hydro Water Reservoir |
| `B14` | 核电 | Nuclear |
| `B15` | 其他可再生 | Other renewable |
| `B16` | 光伏 | Solar |
| `B17` | 废弃物发电 | Waste |
| `B18` | 海上风电 | Wind Offshore |
| `B19` | 陆上风电 | Wind Onshore |
| `B20` | 其他 | Other |

示例：只查询光伏实际发电量：

Example: query solar generation only:

```powershell
powertrade crawl entsoe_actual_generation_by_type --area DE-LU --psr-type B16 `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

## 10. 原始参数覆盖 / Raw Parameter Overrides

对官方新增参数或尚未专门做成选项的过滤条件，可以重复使用：

For new official parameters or filters without a dedicated option, repeat:

```powershell
--entsoe-param KEY=VALUE
```

例如 / Example:

```powershell
powertrade crawl entsoe_actual_generation_by_type --area DE-LU `
  --start-date 2026-06-01 --end-date 2026-06-02 `
  --entsoe-param psrType=B19 --dry-run
```

原始参数会覆盖数据集配置中的同名参数。只有在对照官方文档后才应覆盖 `documentType`、`processType` 等核心字段。

Raw parameters override same-named catalog parameters. Override core fields such as `documentType` or `processType` only after checking the official documentation.

对于尚未加入数据集目录的官方 API，可以直接构造完整参数：

For an official API not yet included in the dataset catalog, construct the full parameter set directly:

```powershell
powertrade entsoe-query `
  --param documentType=A25 `
  --param businessType=B10 `
  --param contract_MarketAgreement.Type=A01 `
  --param in_Domain=10YAT-APG------L `
  --param out_Domain=10YAT-APG------L `
  --start-date 2026-06-01 `
  --end-date 2026-06-02 `
  --output exports/entsoe_custom.jsonl
```

该命令让项目能够访问官方 Postman 集合中的冷门或新增接口，而无需先修改 Python 代码。

This command provides access to uncommon or newly added endpoints from the official Postman collection without changing Python code first.

## 11. 输出字段 / Output Fields

`entsoe_records` 的主要字段：

Main fields in `entsoe_records`:

| Field | 中文意义 | English meaning |
|---|---|---|
| `dataset` | 本项目数据集命令名 | Project dataset command name |
| `category` | market/load/generation/transmission/balancing/outages | Dataset category |
| `document_type` | ENTSO-E 文档类型 | ENTSO-E document type |
| `process_type` | 日前、实际、周前等过程类型 | Process type such as actual or day-ahead |
| `business_type` | 业务数据类型 | Business data type |
| `area` | 命令使用的区域别名 | Area alias used by the command |
| `in_domain` | 输入或来源区域 EIC | Input/source domain EIC |
| `out_domain` | 输出或目的区域 EIC | Output/destination domain EIC |
| `time_series_id` | 官方时间序列 ID | Official time-series ID |
| `psr_type` | 发电能源类型 | Production-source type |
| `interval_start_utc` | 数据时段开始时间 | Interval start in UTC |
| `interval_end_utc` | 数据时段结束时间 | Interval end in UTC |
| `resolution` | PT15M、PT60M 等分辨率 | Resolution such as PT15M or PT60M |
| `value` | 自动识别的主数值 | Automatically selected primary numeric value |
| `value_field` | 主数值在 XML 中的字段名 | XML field selected as the primary value |
| `unit` | MW、MWh、EUR/MWh 等 | Unit such as MW, MWh, or EUR/MWh |
| `raw_json` | 完整文档、序列、时段和点上下文 | Full document, series, period, and point context |

不同 ENTSO-E 文档的 XML 结构并不完全相同。`value` 便于快速分析，`raw_json` 是最终解释复杂业务字段的权威保留内容。

ENTSO-E XML structures differ across document types. `value` is convenient for quick analysis; `raw_json` preserves the authoritative context needed for complex interpretation.

## 12. 数据可用性注意事项 / Availability Notes

- 并非每个区域都发布每种数据。
- Not every area publishes every dataset.
- 报价区、控制区和同步区可能使用不同 EIC。
- Bidding zones, control areas, and synchronous areas may use different EIC codes.
- 单机组和停运信息受披露门槛影响。
- Unit-level and outage data are subject to disclosure thresholds.
- 查询成功但返回 “No matching data found” 不代表 token 失效。
- “No matching data found” does not mean the token is invalid.
- API 总体历史从 2015 年附近开始，但具体起始时间取决于区域、数据类型和区域历史。
- API history generally starts around 2015, but actual availability depends on area, dataset, and historical zone changes.
- `DE-LU` 当前报价区从 2018-10-01 开始；更早德国数据通常涉及旧 `DE-AT-LU` 区域。
- The current `DE-LU` bidding zone starts on 2018-10-01; earlier German data generally uses the former `DE-AT-LU` zone.
