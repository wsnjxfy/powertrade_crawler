# Elexon Insights API 常用数据调用指南

# Elexon Insights API Guide for Common GB Datasets

本项目使用 Elexon Insights Solution 公共 REST API：

```text
https://data.elexon.co.uk/bmrs/api/v1
```

官方资料 / Official references:

- Elexon API 文档入口 / API documentation:
  - https://bmrs.elexon.co.uk/api-documentation/introduction
- Elexon Insights docs GitHub:
  - https://github.com/elexon-data/insights-docs
- OpenAPI / Swagger:
  - https://data.elexon.co.uk/swagger/v1/swagger.json

Elexon 官方文档说明 Insights API 当前不需要 API key。项目仍保留可选凭据槽位，便于以后兼容需要 key 的环境：

```powershell
powertrade set-credential elexon
powertrade credentials-status
```

凭据保存在 `.auth/credentials.json`，不写入 `.env`，不提交 Git。

## 1. 查看数据集

```powershell
powertrade elexon-datasets
powertrade elexon-describe elexon_initial_demand_outturn
```

当前配置的常用英国数据集：

| Command | 中文 | English |
|---|---|---|
| `elexon_generation_by_fuel_instant` | 实时按燃料类型发电 | FUELINST generation by fuel |
| `elexon_generation_by_fuel_half_hourly` | 半小时按燃料类型发电 | FUELHH generation by fuel |
| `elexon_actual_generation_by_type` | 按发电类型实际发电 | AGPT/B1620 generation per type |
| `elexon_initial_demand_outturn` | 初始全国负荷实绩 | INDO demand outturn |
| `elexon_day_ahead_demand_forecast` | 最新日前负荷预测 | NDF/TSDF demand forecast |
| `elexon_wind_generation_forecast` | 最新风电发电预测 | WINDFOR wind forecast |
| `elexon_system_prices` | 结算系统价格 | DISEBSP system prices |
| `elexon_market_index_prices` | 市场指数价格 | MID market index prices |
| `elexon_balancing_physical` | 平衡机制物理申报数据 | PN/QPN/MILS/MELS physical data |
| `elexon_bid_offer_acceptances` | 平衡机制报价接受量 | BOALF acceptances |
| `elexon_loss_of_load_probability` | 供需紧张度：失负荷概率和折减裕度 | LOLP and de-rated margin |
| `elexon_daily_margin_forecast` | 每日系统裕度预测 | Daily margin forecast |
| `elexon_daily_surplus_forecast` | 每日系统剩余容量预测 | Daily surplus forecast |
| `elexon_interconnector_flows` | 半小时互联线潮流 | Half-hourly interconnector flows |
| `elexon_net_balancing_services_adjustment` | 净平衡服务调整 NETBSAD | Net non-BM balancing services adjustment |
| `elexon_disaggregated_balancing_services_adjustment` | 拆分平衡服务调整汇总 DISBSAD | Disaggregated non-BM balancing services adjustment summary |
| `elexon_non_bm_stor` | 非 BM 短期运行备用 STOR | Non-BM short term operating reserve |

## 2. CLI 示例

日期范围中 `end-date` 按项目习惯视为不包含的结束日期。

负荷实绩：

```powershell
powertrade crawl elexon_initial_demand_outturn `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

按燃料类型半小时发电：

```powershell
powertrade crawl elexon_generation_by_fuel_half_hourly `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

只看 CCGT：

```powershell
powertrade crawl elexon_generation_by_fuel_half_hourly `
  --start-date 2026-06-01 --end-date 2026-06-02 `
  --elexon-param fuelType=CCGT --dry-run
```

结算系统价格：

```powershell
powertrade crawl elexon_system_prices `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

只查第 1 个 settlement period：

```powershell
powertrade crawl elexon_system_prices `
  --start-date 2026-06-01 --end-date 2026-06-02 `
  --elexon-param settlementPeriod=1 --dry-run
```

平衡机制物理申报数据默认只查 `dataset=PN` 和 `settlementPeriod=1`，避免一次拉取过大数据量：

```powershell
powertrade crawl elexon_balancing_physical `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

改查 MELS 或指定 BMU：

```powershell
powertrade crawl elexon_balancing_physical `
  --start-date 2026-06-01 --end-date 2026-06-02 `
  --elexon-param dataset=MELS `
  --elexon-param settlementPeriod=10 `
  --elexon-param bmUnit=T_GRAI-8 --dry-run
```

供需紧张度：

```powershell
powertrade crawl elexon_loss_of_load_probability `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

互联线潮流：

```powershell
powertrade crawl elexon_interconnector_flows `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

筛选单条互联线时，在额外参数里传入 `interconnectorName=名称`。可用名称来自 Elexon 参考接口 `/reference/interconnectors/all`；GUI 不单独提供参考数据页面，而是在互联线数据集说明中解释这个参数。

非 BM 平衡服务概览：

```powershell
powertrade crawl elexon_net_balancing_services_adjustment `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl elexon_disaggregated_balancing_services_adjustment `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
powertrade crawl elexon_non_bm_stor `
  --start-date 2026-06-01 --end-date 2026-06-02 --dry-run
```

去掉 `--dry-run` 后，通用 Elexon 记录写入：

```text
elexon_records
```

## 3. GUI 使用

启动：

```powershell
powertrade gui
```

打开顶部页签：

```text
Elexon 英国
```

GUI 支持：

- 查看 API key 状态；当前公开 API 显示 `not required`，不会显示 key 明文。
- 从配置文件选择 Elexon 数据集。
- 查看中英文说明、端点、时间模式、术语解释、主数值字段和默认参数。
- 选择开始日期、结束日期。
- 通过额外参数传入官方参数，例如 `fuelType=CCGT`、`settlementPeriod=10`、`dataset=MELS`、`bmUnit=T_GRAI-8`。
- dry-run 预览请求，不访问 Elexon，也不写数据库。
- 执行爬取并写入 `elexon_records`。
- 按当前筛选刷新、导出 CSV、清除本地数据。

## 4. 输出字段

`elexon_records` 主要字段：

| Field | 中文意义 | English meaning |
|---|---|---|
| `dataset` | 本项目数据集命令名 | Project dataset command name |
| `category` | load/generation/price/balancing/security/interconnector | Dataset category |
| `endpoint` | Elexon API endpoint | Elexon API endpoint |
| `area` | 固定为 GB | GB |
| `settlement_date` | 英国结算日期 | Settlement date |
| `settlement_period` | 结算周期 | Settlement period |
| `publish_time_utc` | 发布时间 UTC | Publish time in UTC |
| `start_time_utc` | 数据时段开始 UTC | Interval start in UTC |
| `end_time_utc` | 数据时段结束 UTC | Interval end in UTC |
| `fuel_type` | 燃料类型、PSR 类型或互联线名称 | Fuel, PSR type, or interconnector name |
| `bm_unit` | Elexon BM Unit | Elexon BM Unit |
| `national_grid_bm_unit` | National Grid BM Unit | National Grid BM Unit |
| `metric` | 统一指标名 | Normalized metric |
| `value` | 主数值 | Primary value |
| `value_field` | 原始 JSON 数值字段 | Source JSON value field |
| `unit` | 单位 | Unit |
| `raw_json` | 原始行和请求参数 | Raw row and request context |

不同端点字段差异较大，`raw_json` 是后续解释复杂业务字段的保留上下文。

## 5. 注意事项

- Elexon 面向英国 GB，可用于补齐 ENTSO-E 中 GB 在 2021-06-15 后停止发布的问题。
- BMU 级平衡机制数据量很大，GUI 和默认配置只查询第 1 个结算周期；需要全量时请分批查询。
- LOLP 表示失负荷概率，de-rated margin 表示折减裕度；前者越高、后者越低，通常代表系统供需越紧。
- NETBSAD、DISBSAD、STOR 是非 BM 平衡服务的汇总视角，适合看系统平衡服务使用情况，不替代逐条 BMU 报价明细。
- API 时间参数有 `publishDateTimeFrom/To`、`from/to`、`settlementDateFrom/To`、路径日期等多种形式，项目通过 `configs/elexon/requests.json` 统一维护。
- 额外参数会覆盖默认参数；覆盖前应对照官方 OpenAPI。
