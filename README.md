# Powertrade Crawler

一个面向国内外电力交易数据网站的可扩展爬虫项目骨架。

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

GridStatus 时间参数统一使用 UTC。当前客户端会保证请求间隔至少 `GRIDSTATUS_MIN_INTERVAL_SECONDS` 秒，默认 1.1 秒，避免超过“每秒 1 次请求”的限制。

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

查看三种凭据是否已经配置，不会显示具体内容：

```powershell
powertrade credentials-status
```

凭据统一保存在 `.auth/credentials.json`：

```json
{
  "gridstatus_api_key": "",
  "elecheck_authorization": "",
  "entsoe_security_token": ""
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

也可以直接传 EIC：

```powershell
powertrade crawl entsoe_day_ahead_prices --area-code 10Y1001A1001A82H --start-date 2026-06-01 --end-date 2026-06-02
```

API 使用的核心参数：

- `documentType=A44`：Price Document，用于日日前价格。
- `contract_MarketAgreement.type=A01`：日拍卖 / day-ahead。
- `in_Domain` / `out_Domain`：Bidding Zone 的 EIC code。
- `periodStart` / `periodEnd`：UTC 时间，格式 `yyyyMMddHHmm`。
- `securityToken`：ENTSO-E 账号生成的 API token。

完整的中英文调用说明、每个数据集的固定 API 参数、PSR 发电类型和跨境方向说明见：

```text
docs/ENTSOE_API_GUIDE.md
```


