from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from powertrade_crawler.clients.elecheck import ElecheckClient
from powertrade_crawler.config import get_settings
from powertrade_crawler.elecheck_auth import (
    cache_elecheck_authorization,
    get_valid_cached_elecheck_authorization,
)
from powertrade_crawler.models import (
    ElecheckAreaRecord,
    ElecheckClearPriceRecord,
    ElecheckMechanismElectricityPriceRecord,
    ElecheckPurchasingProvinceRecord,
    ElecheckPurchasingRecord,
)
from powertrade_crawler.spiders.elecheck import (
    ElecheckClearPriceSpider,
    ElecheckMechanismElectricityPriceSpider,
    ElecheckPurchasingNationalMonthSpider,
    ElecheckPurchasingNationalRangeSpider,
    ElecheckPurchasingProvinceListSpider,
    ElecheckPurchasingProvinceMonthSpider,
)
from powertrade_crawler.storage import (
    ElecheckAreaRecordRow,
    ElecheckClearPriceRecordRow,
    ElecheckMechanismElectricityPriceRecordRow,
    ElecheckPurchasingProvinceRecordRow,
    ElecheckPurchasingRecordRow,
    get_session,
    init_db,
    resolve_elecheck_area_code,
    upsert_elecheck_area_records,
    upsert_elecheck_clear_price_records,
    upsert_elecheck_mechanism_electricity_price_records,
    upsert_elecheck_purchasing_province_records,
    upsert_elecheck_purchasing_records,
)


class FakeElecheckClient:
    def __init__(self) -> None:
        self.closed = False
        self.detail_calls = []
        self.statistics_calls = []

    def fetch_clear_price_detail(self, *, area_code: str, start_date: str, end_date: str):
        self.detail_calls.append(
            {
                "area_code": area_code,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        return [
            {
                "time96": "00:15",
                "avgDayAheadPrice": 12.5,
                "avgRealTimePrice": "-3.2",
            }
        ]

    def fetch_clear_price_statistics(self, *, area_code: str, start_date: str, end_date: str):
        self.statistics_calls.append(
            {
                "area_code": area_code,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        return {
            "negativePriceDateCount": 1,
            "negativePriceTimeCount": 2,
            "negativePriceList": [{"date": "2026-05-28"}],
            "zeroPriceDateCount": 0,
            "zeroPriceTimeCount": 0,
            "zeroPriceList": [],
            "maxPriceDateCount": 1,
            "maxPriceTimeCount": 1,
            "maxPriceList": [{"time96": "10:00"}],
            "dayAheadAvgPrice": 20.1,
            "realTimeAvgPrice": "18.4",
        }

    def close(self) -> None:
        self.closed = True


class FakePurchasingClient:
    def __init__(self) -> None:
        self.closed = False
        self.list_calls = []
        self.chart_calls = []

    def fetch_purchasing_province_month_list(self):
        return [
            {"provinceName": "全国", "dataMonthList": ["2026-06", "2026-05"]},
            {"provinceName": "江苏", "dataMonthList": ["2026-06", "2026-05"]},
        ]

    def fetch_purchasing_province_list(self):
        return ["全国", "江苏", "北京", "天津", "冀北"]

    def fetch_purchasing_list(self, *, province: str, latest_data_month: str):
        self.list_calls.append({"province": province, "latest_data_month": latest_data_month})
        if province:
            return {
                "latestDataMonth": latest_data_month,
                "provinceDataList": [
                    {"type": "代理购电价", "value": 0.373, "diffValue": 0.0201},
                    {"type": "系统运行费折价", "value": 0.0833, "diffValue": 0.0083},
                    {"type": "线损折价", "value": 0.0128, "diffValue": 0.0008},
                    {"type": "合计", "value": 0.4691, "diffValue": 0.0292},
                ],
            }
        return {
            "latestDataMonth": latest_data_month,
            "topDataList": [
                {
                    "type": "代理购电价",
                    "maxProvinceName": "海南",
                    "maxValue": 0.476635,
                    "minProvinceName": "甘肃",
                    "minValue": 0.149332,
                }
            ],
            "tableDataList": [
                {
                    "provinceName": "山东",
                    "purchasingPrice": 0.3391,
                    "purchasingSystemOperatingCost": 0.0804,
                    "lineLossCost": 0.0118,
                    "purchasingSum": 0.4313,
                }
            ],
        }

    def fetch_purchasing_chart_data(self, *, province: str, start_month: str, end_month: str):
        self.chart_calls.append(
            {"province": province, "start_month": start_month, "end_month": end_month}
        )
        return {
            "legendDataList": ["代理购电价"],
            "xAxisData": ["2026-05", "2026-06"],
            "series": [{"name": "代理购电价", "data": [0.3529, 0.373]}],
        }

    def close(self) -> None:
        self.closed = True


class FakeMechanismElectricityPriceClient:
    def __init__(self) -> None:
        self.closed = False

    def fetch_mechanism_electricity_price_list(self):
        return [
            {
                "regionName": "江苏",
                "region": "jiangsu",
                "category": "光伏",
                "price": 0.391,
                "clearPrice": 0.34,
            },
            {
                "regionName": "蒙东",
                "region": "mengdong",
                "category": "风电-陆上",
                "price": 0.3035,
                "clearPrice": None,
            },
        ]

    def close(self) -> None:
        self.closed = True


def test_elecheck_client_builds_clear_price_payload():
    client = object.__new__(ElecheckClient)

    payload = client.build_payload("320000000000", "2026-05-28", "2026-06-04")

    assert payload == {
        "areaCode": "320000000000",
        "startDate": "2026-05-28",
        "endDate": "2026-06-04",
    }


def test_elecheck_client_fetches_purchasing_list_with_query_params():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 200, "data": {"latestDataMonth": "2026-06"}}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.calls = []

        def get(self, path, params):
            self.calls.append({"path": path, "params": params})
            return FakeResponse()

    client = object.__new__(ElecheckClient)
    client.client = FakeHttpClient()
    client.retry_times = 0

    data = client.fetch_purchasing_list(province="江苏", latest_data_month="2026-06")

    assert data == {"latestDataMonth": "2026-06"}
    assert client.client.calls == [
        {
            "path": "/electricCheckApi/queryData/purchasing/list",
            "params": {"province": "江苏", "latestDataMonth": "2026-06"},
        }
    ]


def test_elecheck_client_fetches_purchasing_province_list():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 200, "data": ["全国", "江苏", "北京"]}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.calls = []

        def get(self, path, params):
            self.calls.append({"path": path, "params": params})
            return FakeResponse()

    client = object.__new__(ElecheckClient)
    client.client = FakeHttpClient()
    client.retry_times = 0

    data = client.fetch_purchasing_province_list()

    assert data == ["全国", "江苏", "北京"]
    assert client.client.calls == [
        {
            "path": "/electricCheckApi/queryData/purchasing/provinceList",
            "params": {},
        }
    ]


def test_elecheck_client_fetches_mechanism_electricity_price_list():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 200, "data": [{"regionName": "江苏"}]}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.calls = []

        def get(self, path, params):
            self.calls.append({"path": path, "params": params})
            return FakeResponse()

    client = object.__new__(ElecheckClient)
    client.client = FakeHttpClient()
    client.retry_times = 0

    data = client.fetch_mechanism_electricity_price_list()

    assert data == [{"regionName": "江苏"}]
    assert client.client.calls == [
        {
            "path": "/electricCheckApi/query/mechanismElectricityPrice/list",
            "params": {},
        }
    ]


def test_elecheck_client_omits_authorization_header_when_not_configured():
    client = object.__new__(ElecheckClient)
    client.authorization = None

    headers = client.build_headers("test-agent")

    assert "authorization" not in headers
    assert headers["User-Agent"] == "test-agent"


def test_elecheck_client_includes_authorization_header_when_configured():
    client = object.__new__(ElecheckClient)
    client.authorization = "jwt-token"

    headers = client.build_headers("test-agent")

    assert headers["authorization"] == "jwt-token"


def test_elecheck_spider_resolves_period_dates_from_end_date():
    fake_client = FakeElecheckClient()

    recent = ElecheckClearPriceSpider(
        client=fake_client,
        end_date="2026-06-10",
        period="recent",
    )
    seven_days = ElecheckClearPriceSpider(
        client=fake_client,
        end_date="2026-06-10",
        period="7d",
    )
    thirty_days = ElecheckClearPriceSpider(
        client=fake_client,
        end_date="2026-06-10",
        period="30d",
    )
    half_year = ElecheckClearPriceSpider(
        client=fake_client,
        end_date="2026-06-10",
        period="half-year",
    )

    assert recent.start_date == date(2026, 6, 10)
    assert seven_days.start_date == date(2026, 6, 3)
    assert thirty_days.start_date == date(2026, 5, 10)
    assert half_year.start_date == date(2025, 12, 10)


def test_elecheck_spider_custom_start_date_overrides_period():
    spider = ElecheckClearPriceSpider(
        client=FakeElecheckClient(),
        start_date="2026-05-28",
        end_date="2026-06-10",
        period="recent",
    )

    assert spider.start_date == date(2026, 5, 28)
    assert spider.end_date == date(2026, 6, 10)


def test_elecheck_spider_generates_detail_and_statistics_records():
    fake_client = FakeElecheckClient()
    spider = object.__new__(ElecheckClearPriceSpider)
    spider.area_code = "320000000000"
    spider.start_date = date(2026, 5, 28)
    spider.end_date = date(2026, 6, 4)
    spider.client = fake_client

    records = list(spider.crawl())

    assert fake_client.detail_calls == [
        {
            "area_code": "320000000000",
            "start_date": "2026-05-28",
            "end_date": "2026-06-04",
        }
    ]
    assert fake_client.statistics_calls == fake_client.detail_calls
    assert len(records) == 10

    day_ahead = records[0]
    assert day_ahead.endpoint == "detail"
    assert day_ahead.time96 == "00:15"
    assert day_ahead.metric == "avg_day_ahead_price"
    assert day_ahead.value == 12.5
    assert day_ahead.unit == "CNY/MWh"
    assert day_ahead.currency == "CNY"

    negative_date_count = next(
        record for record in records if record.metric == "negative_price_date_count"
    )
    assert negative_date_count.endpoint == "statistics"
    assert negative_date_count.value == 1
    assert negative_date_count.unit == "day"
    assert negative_date_count.raw["negativePriceList"] == [{"date": "2026-05-28"}]


def test_elecheck_spider_daily_mode_requests_each_day_and_keeps_daily_dates():
    fake_client = FakeElecheckClient()
    spider = ElecheckClearPriceSpider(
        client=fake_client,
        area_code="320000000000",
        start_date="2026-05-28",
        end_date="2026-05-30",
        daily=True,
    )

    records = list(spider.crawl())

    assert fake_client.detail_calls == [
        {
            "area_code": "320000000000",
            "start_date": "2026-05-28",
            "end_date": "2026-05-28",
        },
        {
            "area_code": "320000000000",
            "start_date": "2026-05-29",
            "end_date": "2026-05-29",
        },
        {
            "area_code": "320000000000",
            "start_date": "2026-05-30",
            "end_date": "2026-05-30",
        },
    ]
    assert fake_client.statistics_calls == fake_client.detail_calls
    assert len(records) == 30
    assert {record.start_date for record in records} == {
        date(2026, 5, 28),
        date(2026, 5, 29),
        date(2026, 5, 30),
    }
    assert all(record.start_date == record.end_date for record in records)


def test_elecheck_purchasing_national_spider_generates_table_and_top_records():
    fake_client = FakePurchasingClient()
    spider = ElecheckPurchasingNationalMonthSpider(client=fake_client, data_month="2026-06")

    records = list(spider.crawl())

    assert fake_client.list_calls == [{"province": "", "latest_data_month": "2026-06"}]
    assert len(records) == 6
    table_record = next(
        record
        for record in records
        if record.data_kind == "national_table" and record.metric == "purchasing_price"
    )
    assert table_record.province_name == "山东"
    assert table_record.value == 0.3391
    max_record = next(record for record in records if record.statistic == "max")
    assert max_record.data_kind == "national_top"
    assert max_record.metric == "purchasing_price"
    assert max_record.related_province_name == "海南"
    assert max_record.value == 0.476635


def test_elecheck_purchasing_national_range_spider_generates_each_month():
    fake_client = FakePurchasingClient()
    spider = ElecheckPurchasingNationalRangeSpider(
        client=fake_client,
        start_month="2026-05",
        end_month="2026-06",
    )

    records = list(spider.crawl())

    assert fake_client.list_calls == [
        {"province": "", "latest_data_month": "2026-05"},
        {"province": "", "latest_data_month": "2026-06"},
    ]
    assert len(records) == 12
    assert sorted({record.data_month for record in records}) == ["2026-05", "2026-06"]
    assert all(record.data_kind in {"national_table", "national_top"} for record in records)


def test_elecheck_purchasing_province_list_spider_generates_records():
    spider = ElecheckPurchasingProvinceListSpider(client=FakePurchasingClient())

    records = list(spider.crawl())

    assert [record.province_name for record in records] == ["全国", "江苏", "北京", "天津", "冀北"]
    assert all(record.source == "易能电易查" for record in records)
    assert records[2].raw == {"provinceName": "北京"}


def test_elecheck_purchasing_province_spider_generates_summary_and_trend_records():
    fake_client = FakePurchasingClient()
    spider = ElecheckPurchasingProvinceMonthSpider(
        client=fake_client,
        data_month="2026-06",
        province_name="江苏",
    )

    records = list(spider.crawl())

    assert fake_client.list_calls == [{"province": "江苏", "latest_data_month": "2026-06"}]
    assert fake_client.chart_calls == [
        {"province": "江苏", "start_month": "2025-07", "end_month": "2026-06"}
    ]
    assert len(records) == 6
    summary = next(record for record in records if record.data_kind == "province_summary")
    assert summary.metric == "purchasing_price"
    assert summary.value == 0.373
    assert summary.diff_value == 0.0201
    trend = [
        record for record in records if record.data_kind == "province_trend"
    ]
    assert [record.data_month for record in trend] == ["2026-05", "2026-06"]
    assert trend[-1].value == 0.373


def test_elecheck_purchasing_province_spider_uses_custom_chart_month_range():
    fake_client = FakePurchasingClient()
    spider = ElecheckPurchasingProvinceMonthSpider(
        client=fake_client,
        province_name="江苏",
        start_month="2025-01",
        end_month="2025-12",
    )

    list(spider.crawl())

    assert fake_client.list_calls == [{"province": "江苏", "latest_data_month": "2025-12"}]
    assert fake_client.chart_calls == [
        {"province": "江苏", "start_month": "2025-01", "end_month": "2025-12"}
    ]


def test_elecheck_mechanism_electricity_price_spider_generates_records():
    fake_client = FakeMechanismElectricityPriceClient()
    spider = ElecheckMechanismElectricityPriceSpider(client=fake_client)

    records = list(spider.crawl())

    assert len(records) == 2
    assert records[0].region_name == "江苏"
    assert records[0].region == "jiangsu"
    assert records[0].category == "光伏"
    assert records[0].price == 0.391
    assert records[0].clear_price == 0.34
    assert records[1].region_name == "蒙东"
    assert records[1].clear_price is None


def test_elecheck_spider_skips_blank_values():
    spider = object.__new__(ElecheckClearPriceSpider)
    spider.area_code = "320000000000"
    spider.start_date = date(2026, 5, 28)
    spider.end_date = date(2026, 6, 4)

    records = list(
        spider.detail_row_to_records(
            {
                "time96": "00:00",
                "avgDayAheadPrice": "",
                "avgRealTimePrice": None,
            }
        )
    )

    assert records == []


def test_elecheck_clear_price_records_upsert_to_sqlite(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    record = ElecheckClearPriceRecord(
        endpoint="detail",
        area_code="320000000000",
        start_date=date(2026, 5, 28),
        end_date=date(2026, 6, 4),
        time96="00:15",
        metric="avg_day_ahead_price",
        value=12.5,
        unit="CNY/MWh",
        currency="CNY",
        raw={"time96": "00:15", "avgDayAheadPrice": 12.5},
    )

    assert upsert_elecheck_clear_price_records([record]) == 1
    updated = record.model_copy(update={"value": 13.5})
    assert upsert_elecheck_clear_price_records([updated]) == 1

    with get_session() as session:
        rows = session.query(ElecheckClearPriceRecordRow).all()

    assert len(rows) == 1
    assert rows[0].value == 13.5


def test_elecheck_clear_price_daily_records_upsert_to_sqlite(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    day_one = ElecheckClearPriceRecord(
        endpoint="detail",
        area_code="320000000000",
        start_date=date(2026, 5, 28),
        end_date=date(2026, 5, 28),
        time96="00:15",
        metric="avg_day_ahead_price",
        value=12.5,
        unit="CNY/MWh",
        currency="CNY",
        raw={"time96": "00:15", "avgDayAheadPrice": 12.5},
    )
    day_two = day_one.model_copy(
        update={
            "start_date": date(2026, 5, 29),
            "end_date": date(2026, 5, 29),
            "value": 14.5,
        }
    )

    assert upsert_elecheck_clear_price_records([day_one, day_two]) == 2

    with get_session() as session:
        rows = (
            session.query(ElecheckClearPriceRecordRow)
            .order_by(ElecheckClearPriceRecordRow.start_date)
            .all()
        )

    assert len(rows) == 2
    assert [row.start_date for row in rows] == [date(2026, 5, 28), date(2026, 5, 29)]
    assert [row.value for row in rows] == [12.5, 14.5]


def test_elecheck_purchasing_records_upsert_to_sqlite(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    record = ElecheckPurchasingRecord(
        endpoint="list",
        data_kind="province_summary",
        data_month="2026-06",
        province_name="江苏",
        metric="purchasing_price",
        value=0.373,
        diff_value=0.0201,
        raw={"type": "代理购电价"},
    )

    assert upsert_elecheck_purchasing_records([record]) == 1
    updated = record.model_copy(update={"value": 0.38})
    assert upsert_elecheck_purchasing_records([updated]) == 1

    with get_session() as session:
        rows = session.query(ElecheckPurchasingRecordRow).all()

    assert len(rows) == 1
    assert rows[0].value == 0.38


def test_elecheck_purchasing_province_records_upsert_to_sqlite(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    record = ElecheckPurchasingProvinceRecord(
        province_name="北京",
        raw={"provinceName": "北京"},
    )

    assert upsert_elecheck_purchasing_province_records([record]) == 1
    updated = record.model_copy(update={"raw": {"provinceName": "北京", "updated": True}})
    assert upsert_elecheck_purchasing_province_records([updated]) == 1

    with get_session() as session:
        rows = session.query(ElecheckPurchasingProvinceRecordRow).all()

    assert len(rows) == 1
    assert rows[0].province_name == "北京"
    assert '"updated": true' in rows[0].raw_json


def test_elecheck_mechanism_electricity_price_records_upsert_to_sqlite(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    record = ElecheckMechanismElectricityPriceRecord(
        region_name="江苏",
        region="jiangsu",
        category="光伏",
        price=0.391,
        clear_price=0.34,
        raw={"regionName": "江苏"},
    )

    assert upsert_elecheck_mechanism_electricity_price_records([record]) == 1
    updated = record.model_copy(update={"clear_price": 0.35})
    assert upsert_elecheck_mechanism_electricity_price_records([updated]) == 1

    with get_session() as session:
        rows = session.query(ElecheckMechanismElectricityPriceRecordRow).all()

    assert len(rows) == 1
    assert rows[0].clear_price == 0.35


def test_init_db_seeds_elecheck_area_records(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()

    with get_session() as session:
        rows = {
            row.area_name: row
            for row in session.query(ElecheckAreaRecordRow)
            .order_by(ElecheckAreaRecordRow.area_name)
            .all()
        }

    assert rows["山西"].area_code == "140000000000"
    assert rows["山西"].detail_point_count == 96
    assert rows["山东"].area_code == "370000000000"
    assert rows["山东"].detail_granularity == "hourly"
    assert rows["蒙西"].area_code == "150000000000"
    assert rows["河北南网"].area_code == "130000000001"
    assert rows["蒙东"].area_code == "150000000001"
    assert rows["辽宁"].area_code == "210000000000"
    assert rows["吉林"].area_code == "220000000000"
    assert rows["黑龙江"].area_code == "230000000000"
    assert rows["安徽"].area_code == "340000000000"
    assert rows["江苏"].area_code == "320000000000"
    assert rows["浙江"].area_code == "330000000000"
    assert rows["浙江"].detail_point_count == 48
    assert rows["浙江"].detail_granularity == "30min"
    assert rows["福建"].area_code == "350000000000"
    assert rows["上海"].area_code == "310000000000"
    assert rows["湖北"].area_code == "420000000000"
    assert rows["湖北"].detail_granularity == "hourly"
    assert rows["湖南"].area_code == "430000000000"
    assert rows["江西"].area_code == "360000000000"
    assert rows["江西"].detail_point_count == 288
    assert rows["江西"].detail_granularity == "5min"
    assert rows["河南"].area_code == "410000000000"
    assert rows["新疆"].area_code == "650000000000"
    assert rows["陕西"].area_code == "610000000000"
    assert rows["宁夏"].area_code == "640000000000"
    assert rows["宁夏"].detail_granularity == "hourly"
    assert rows["甘肃"].area_code == "620000000000"
    assert rows["青海"].area_code == "630000000000"
    assert rows["四川"].area_code == "510000000000"
    assert rows["重庆"].area_code == "500000000000"
    assert rows["广东"].area_code == "440000000000"
    assert rows["广东"].detail_granularity == "hourly"
    assert rows["广西"].area_code == "450000000000"
    assert rows["云南"].area_code == "530000000000"
    assert rows["贵州"].area_code == "520000000000"
    assert rows["海南"].area_code == "460000000000"


def test_resolve_elecheck_area_code_from_seeded_area_table(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()

    assert resolve_elecheck_area_code("山东") == "370000000000"
    assert resolve_elecheck_area_code("蒙东") == "150000000001"
    assert resolve_elecheck_area_code("浙江") == "330000000000"
    assert resolve_elecheck_area_code("江西") == "360000000000"
    assert resolve_elecheck_area_code("新疆") == "650000000000"
    assert resolve_elecheck_area_code("重庆") == "500000000000"
    assert resolve_elecheck_area_code("广东") == "440000000000"


def test_elecheck_area_records_upsert_updates_existing_area(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    assert upsert_elecheck_area_records(
        [
            ElecheckAreaRecord(
                area_name="山东",
                area_code="370000000000",
                detail_point_count=96,
                detail_granularity="15min",
                note="updated",
            )
        ]
    ) == 1

    with get_session() as session:
        row = session.query(ElecheckAreaRecordRow).filter_by(area_code="370000000000").one()

    assert row.area_name == "山东"
    assert row.detail_point_count == 96
    assert row.detail_granularity == "15min"
    assert row.note == "updated"


def test_elecheck_area_records_preserve_existing_earliest_clear_price_date(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "powertrade.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    init_db()
    with get_session() as session:
        row = session.query(ElecheckAreaRecordRow).filter_by(area_code="320000000000").one()
        row.earliest_clear_price_date = date(2020, 11, 1)
        session.commit()

    assert upsert_elecheck_area_records(
        [
            ElecheckAreaRecord(
                area_name="江苏",
                area_code="320000000000",
                detail_point_count=96,
                detail_granularity="15min",
            )
        ]
    ) == 1

    with get_session() as session:
        row = session.query(ElecheckAreaRecordRow).filter_by(area_code="320000000000").one()

    assert row.earliest_clear_price_date == date(2020, 11, 1)


def test_elecheck_authorization_cache_keeps_token(tmp_path: Path):
    now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    cache_path = tmp_path / ".elecheck_authorization_cache.json"

    cache_elecheck_authorization("jwt-token", cache_path=cache_path, now=now)

    assert (
        get_valid_cached_elecheck_authorization(
            cache_path=cache_path,
            now=now + timedelta(hours=23, minutes=59),
        )
        == "jwt-token"
    )


def test_elecheck_authorization_cache_ignores_legacy_expires_at(tmp_path: Path):
    cache_path = tmp_path / ".elecheck_authorization_cache.json"
    cache_path.write_text(
        '{"authorization": "jwt-token", "expires_at": "2026-06-08T12:00:00+00:00"}',
        encoding="utf-8",
    )

    assert (
        get_valid_cached_elecheck_authorization(
            cache_path=cache_path,
            now=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
        )
        == "jwt-token"
    )
