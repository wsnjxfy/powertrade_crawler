from powertrade_crawler.clients.elexon import ElexonClient
from powertrade_crawler.spiders.elexon import (
    build_elexon_spider_classes,
    get_elexon_request_config,
)


def test_elexon_client_flattens_nested_data_rows():
    client = ElexonClient.__new__(ElexonClient)
    payload = {
        "data": [
            {
                "startTime": "2026-06-01T00:00:00Z",
                "settlementPeriod": 1,
                "data": [
                    {"psrType": "Solar", "quantity": 10.5},
                    {"psrType": "Wind Offshore", "quantity": 20.0},
                ],
            }
        ]
    }

    rows = client.extract_rows(payload)

    assert rows == [
        {
            "startTime": "2026-06-01T00:00:00Z",
            "settlementPeriod": 1,
            "psrType": "Solar",
            "quantity": 10.5,
        },
        {
            "startTime": "2026-06-01T00:00:00Z",
            "settlementPeriod": 1,
            "psrType": "Wind Offshore",
            "quantity": 20.0,
        },
    ]


def test_elexon_client_accepts_top_level_reference_arrays():
    client = ElexonClient.__new__(ElexonClient)

    rows = client.extract_rows(
        [
            {"interconnectorName": "IFA", "interconnectorBiddingZone": "France"},
            {"interconnectorName": "BritNed", "interconnectorBiddingZone": "Netherlands"},
        ]
    )

    assert rows == [
        {"interconnectorName": "IFA", "interconnectorBiddingZone": "France"},
        {"interconnectorName": "BritNed", "interconnectorBiddingZone": "Netherlands"},
    ]


def test_elexon_spider_builds_publish_range_request_and_records(monkeypatch):
    captured = []

    class FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key

        def query(self, path, params):
            captured.append({"path": path, "params": params})
            return [
                {
                    "dataset": "FUELHH",
                    "publishTime": "2026-06-01T01:00:00Z",
                    "startTime": "2026-06-01T00:30:00Z",
                    "settlementDate": "2026-06-01",
                    "settlementPeriod": 2,
                    "fuelType": "CCGT",
                    "generation": 12345,
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.elexon.ElexonClient", FakeClient)
    spider_class = build_elexon_spider_classes()["elexon_generation_by_fuel_half_hourly"]
    spider = spider_class(
        start_date="2026-06-01",
        end_date="2026-06-02",
        extra_params={"fuelType": "CCGT"},
        api_key="optional-key",
    )

    records = list(spider.crawl())

    assert captured == [
        {
            "path": "/datasets/FUELHH",
            "params": {
                "fuelType": "CCGT",
                "publishDateTimeFrom": "2026-06-01T00:00Z",
                "publishDateTimeTo": "2026-06-02T00:00Z",
            },
        }
    ]
    assert len(records) == 1
    assert records[0].dataset == "elexon_generation_by_fuel_half_hourly"
    assert records[0].area == "GB"
    assert records[0].settlement_date == "2026-06-01"
    assert records[0].settlement_period == 2
    assert records[0].fuel_type == "CCGT"
    assert records[0].metric == "generation"
    assert records[0].value == 12345.0
    assert records[0].unit == "MW"
    assert records[0].raw["request"]["params"]["fuelType"] == "CCGT"


def test_elexon_spider_builds_system_price_period_path(monkeypatch):
    class FakeClient:
        def __init__(self, api_key=None):
            pass

        def close(self):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.elexon.ElexonClient", FakeClient)
    spider_class = build_elexon_spider_classes()["elexon_system_prices"]
    spider = spider_class(
        start_date="2026-06-01",
        end_date="2026-06-02",
        extra_params={"settlementPeriod": "1"},
    )

    specs = spider.build_request_specs()

    assert specs == [
        {
            "path": "/balancing/settlement/system-prices/2026-06-01/1",
            "params": {},
        }
    ]


def test_elexon_spider_builds_snapshot_request(monkeypatch):
    class FakeClient:
        def __init__(self, api_key=None):
            pass

        def close(self):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.elexon.ElexonClient", FakeClient)
    spider_class = build_elexon_spider_classes()["elexon_daily_margin_forecast"]
    spider = spider_class(start_date="2026-06-01", end_date="2026-06-02")

    assert spider.build_request_specs() == [
        {"path": "/forecast/margin/daily", "params": {}}
    ]


def test_elexon_interconnector_flow_records_use_interconnector_as_dimension(monkeypatch):
    class FakeClient:
        def __init__(self, api_key=None):
            pass

        def query(self, path, params):
            assert path == "/generation/outturn/interconnectors"
            assert params == {
                "settlementDateFrom": "2026-06-01",
                "settlementDateTo": "2026-06-01",
            }
            return [
                {
                    "publishTime": "2026-06-01T01:00:00Z",
                    "startTime": "2026-06-01T00:30:00Z",
                    "settlementDate": "2026-06-01",
                    "settlementPeriod": 2,
                    "interconnectorName": "IFA",
                    "generation": -245,
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.elexon.ElexonClient", FakeClient)
    spider_class = build_elexon_spider_classes()["elexon_interconnector_flows"]
    spider = spider_class(start_date="2026-06-01", end_date="2026-06-02")

    records = list(spider.crawl())

    assert len(records) == 1
    assert records[0].dataset == "elexon_interconnector_flows"
    assert records[0].category == "interconnector"
    assert records[0].fuel_type == "IFA"
    assert records[0].metric == "interconnector_flow"
    assert records[0].value == -245.0
    assert records[0].unit == "MW"


def test_elexon_config_catalog_contains_common_gb_replacement_datasets():
    names = set(build_elexon_spider_classes())

    assert {
        "elexon_initial_demand_outturn",
        "elexon_generation_by_fuel_half_hourly",
        "elexon_system_prices",
        "elexon_market_index_prices",
        "elexon_balancing_physical",
        "elexon_loss_of_load_probability",
        "elexon_daily_margin_forecast",
        "elexon_daily_surplus_forecast",
        "elexon_interconnector_flows",
        "elexon_net_balancing_services_adjustment",
        "elexon_disaggregated_balancing_services_adjustment",
        "elexon_non_bm_stor",
    }.issubset(names)


def test_elexon_describe_config_exposes_default_balancing_period():
    config = get_elexon_request_config("elexon_balancing_physical")

    assert config["default_params"] == {"dataset": "PN", "settlementPeriod": "1"}
