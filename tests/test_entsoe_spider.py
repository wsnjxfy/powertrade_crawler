from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from powertrade_crawler.clients.entsoe import EntsoeClient
from powertrade_crawler.spiders.entsoe import (
    EntsoeDayAheadPricesSpider,
    build_entsoe_spider_classes,
)


DAY_AHEAD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0">
  <TimeSeries>
    <currency_Unit.name>EUR</currency_Unit.name>
    <price_Measure_Unit.name>MWH</price_Measure_Unit.name>
    <Period>
      <timeInterval>
        <start>2026-06-01T00:00Z</start>
        <end>2026-06-01T02:00Z</end>
      </timeInterval>
      <resolution>PT60M</resolution>
      <Point>
        <position>1</position>
        <price.amount>51.23</price.amount>
      </Point>
      <Point>
        <position>2</position>
        <price.amount>47.89</price.amount>
      </Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>
"""


LOAD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
  <type>A65</type>
  <process.processType>A16</process.processType>
  <TimeSeries>
    <mRID>load-series-1</mRID>
    <businessType>A04</businessType>
    <quantity_Measure_Unit.name>MAW</quantity_Measure_Unit.name>
    <Period>
      <timeInterval>
        <start>2026-06-01T00:00Z</start>
        <end>2026-06-01T01:00Z</end>
      </timeInterval>
      <resolution>PT15M</resolution>
      <Point>
        <position>1</position>
        <quantity>54321</quantity>
      </Point>
    </Period>
  </TimeSeries>
</GL_MarketDocument>
"""


OUTAGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Unavailability_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:outagedocument:3:0">
  <type>A80</type>
  <TimeSeries>
    <mRID>outage-series-1</mRID>
    <quantity_Measure_Unit.name>MAW</quantity_Measure_Unit.name>
    <production_RegisteredResource.pSRType.psrType>B10</production_RegisteredResource.pSRType.psrType>
    <Available_Period>
      <timeInterval>
        <start>2026-06-01T00:00Z</start>
        <end>2026-06-01T01:00Z</end>
      </timeInterval>
      <resolution>PT60M</resolution>
      <Point>
        <position>1</position>
        <quantity>230</quantity>
      </Point>
    </Available_Period>
  </TimeSeries>
</Unavailability_MarketDocument>
"""


def test_entsoe_client_parses_day_ahead_price_xml():
    client = EntsoeClient.__new__(EntsoeClient)

    rows = client.parse_day_ahead_prices(DAY_AHEAD_XML, "10Y1001A1001A82H")

    assert rows == [
        {
            "bidding_zone_eic": "10Y1001A1001A82H",
            "position": 1,
            "interval_start_utc": "2026-06-01T00:00Z",
            "interval_end_utc": "2026-06-01T01:00Z",
            "period_start_utc": "2026-06-01T00:00Z",
            "period_end_utc": "2026-06-01T02:00Z",
            "resolution": "PT60M",
            "price": 51.23,
            "currency": "EUR",
            "unit": "EUR/MWH",
        },
        {
            "bidding_zone_eic": "10Y1001A1001A82H",
            "position": 2,
            "interval_start_utc": "2026-06-01T01:00Z",
            "interval_end_utc": "2026-06-01T02:00Z",
            "period_start_utc": "2026-06-01T00:00Z",
            "period_end_utc": "2026-06-01T02:00Z",
            "resolution": "PT60M",
            "price": 47.89,
            "currency": "EUR",
            "unit": "EUR/MWH",
        },
    ]


def test_entsoe_client_builds_day_ahead_request_params(monkeypatch):
    captured = {}
    client = EntsoeClient.__new__(EntsoeClient)
    client.security_token = "token"

    def fake_get(params):
        captured.update(params)

        class Response:
            text = DAY_AHEAD_XML

        return Response()

    client.get = fake_get

    rows = client.query_day_ahead_prices(
        bidding_zone_eic="10Y1001A1001A82H",
        period_start=datetime(2026, 6, 1, 0, 0),
        period_end=datetime(2026, 6, 2, 0, 0),
    )

    assert captured == {
        "securityToken": "token",
        "documentType": "A44",
        "contract_MarketAgreement.type": "A01",
        "in_Domain": "10Y1001A1001A82H",
        "out_Domain": "10Y1001A1001A82H",
        "periodStart": "202606010000",
        "periodEnd": "202606020000",
    }
    assert len(rows) == 2


def test_entsoe_client_requests_exact_api_path(monkeypatch):
    requested_urls = []

    def handler(request):
        requested_urls.append(request.url)
        return httpx.Response(200, text=DAY_AHEAD_XML)

    client = EntsoeClient.__new__(EntsoeClient)
    client.retry_times = 0
    client.min_interval_seconds = 0
    client.last_request_at = 0
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    response = client.get({"securityToken": "secret"})

    assert response.status_code == 200
    assert requested_urls[0].path == "/api"
    assert requested_urls[0].query == b"securityToken=secret"


def test_entsoe_client_does_not_retry_or_expose_token_for_client_errors():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            text=(
                "<Acknowledgement_MarketDocument>"
                "<Reason><code>999</code><text>Invalid request</text></Reason>"
                "</Acknowledgement_MarketDocument>"
            ),
        )

    client = EntsoeClient.__new__(EntsoeClient)
    client.retry_times = 2
    client.min_interval_seconds = 0
    client.last_request_at = 0
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="HTTP 400: 999 Invalid request") as exc_info:
        client.get({"securityToken": "secret"})

    assert attempts == 1
    assert "secret" not in str(exc_info.value)


def test_entsoe_times_are_normalized_to_utc_before_storage_and_requests():
    client = EntsoeClient.__new__(EntsoeClient)
    plus_two = timezone(timedelta(hours=2))

    assert client.parse_entsoe_datetime("2026-08-10T02:00:00+02:00") == datetime(
        2026,
        8,
        10,
        0,
    )
    assert client.parse_entsoe_datetime("2026-08-10T00:00:00Z") == datetime(
        2026,
        8,
        10,
        0,
    )
    assert client.format_period(datetime(2026, 8, 10, 2, tzinfo=plus_two)) == "202608100000"
    assert client.format_response_time(datetime(2026, 8, 10, tzinfo=UTC)) == (
        "2026-08-10T00:00Z"
    )


def test_entsoe_client_parses_generic_time_series_document():
    client = EntsoeClient.__new__(EntsoeClient)

    rows = client.parse_time_series_document(LOAD_XML)

    assert len(rows) == 1
    assert rows[0]["document"]["type"] == "A65"
    assert rows[0]["series"]["mRID"] == "load-series-1"
    assert rows[0]["interval_start_utc"] == "2026-06-01T00:00Z"
    assert rows[0]["interval_end_utc"] == "2026-06-01T00:15Z"
    assert rows[0]["value_field"] == "quantity"
    assert rows[0]["value"] == 54321.0


def test_entsoe_client_injects_token_for_generic_queries():
    requested_urls = []

    def handler(request):
        requested_urls.append(request.url)
        return httpx.Response(200, text=LOAD_XML)

    client = EntsoeClient.__new__(EntsoeClient)
    client.security_token = "secret"
    client.retry_times = 0
    client.min_interval_seconds = 0
    client.last_request_at = 0
    client.client = httpx.Client(transport=httpx.MockTransport(handler))

    rows = client.query_document({"documentType": "A65"})

    assert len(rows) == 1
    assert requested_urls[0].params["securityToken"] == "secret"
    assert requested_urls[0].params["documentType"] == "A65"


def test_entsoe_client_parses_available_period_from_zip():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("outage.xml", OUTAGE_XML)
    client = EntsoeClient.__new__(EntsoeClient)

    rows = client.parse_zip_document(buffer.getvalue())

    assert len(rows) == 1
    assert rows[0]["archive_member"] == "outage.xml"
    assert rows[0]["series"]["production_RegisteredResource.pSRType.psrType"] == "B10"
    assert rows[0]["interval_start_utc"] == "2026-06-01T00:00Z"
    assert rows[0]["value"] == 230.0


def test_configured_entsoe_spider_builds_single_area_request(monkeypatch):
    captured = []

    class FakeClient:
        def __init__(self, security_token=None):
            pass

        def format_period(self, value):
            return value.strftime("%Y%m%d%H%M")

        def query_document(self, params):
            captured.append(params)
            return [
                {
                    "document": {"type": "A65"},
                    "series": {
                        "mRID": "load-series-1",
                        "businessType": "A04",
                        "quantity_Measure_Unit.name": "MAW",
                    },
                    "period": {},
                    "point": {"position": "1", "quantity": "54321"},
                    "series_index": 1,
                    "period_index": 1,
                    "position": 1,
                    "period_start_utc": "2026-06-01T00:00Z",
                    "period_end_utc": "2026-06-01T01:00Z",
                    "interval_start_utc": "2026-06-01T00:00Z",
                    "interval_end_utc": "2026-06-01T00:15Z",
                    "resolution": "PT15M",
                    "value_field": "quantity",
                    "value": 54321.0,
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.entsoe.EntsoeClient", FakeClient)
    spider_class = build_entsoe_spider_classes()["entsoe_actual_total_load"]
    spider = spider_class(
        area="DE-LU",
        start_date="2026-06-01",
        end_date="2026-06-02",
        security_token="token",
    )

    records = list(spider.crawl())

    assert captured == [
        {
            "documentType": "A65",
            "processType": "A16",
            "outBiddingZone_Domain": "10Y1001A1001A82H",
            "periodStart": "202606010000",
            "periodEnd": "202606020000",
        }
    ]
    assert len(records) == 1
    assert records[0].dataset == "entsoe_actual_total_load"
    assert records[0].area == "DE-LU"
    assert records[0].value == 54321.0
    assert records[0].unit == "MAW"


def test_configured_entsoe_spider_requires_two_areas_for_border(monkeypatch):
    class FakeClient:
        def __init__(self, security_token=None):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.entsoe.EntsoeClient", FakeClient)
    spider_class = build_entsoe_spider_classes()["entsoe_cross_border_physical_flows"]
    spider = spider_class(
        start_date="2026-06-01",
        end_date="2026-06-02",
        security_token="token",
    )

    with pytest.raises(ValueError, match="--in-area"):
        list(spider.crawl())


def test_entsoe_spider_maps_rows_to_market_records(monkeypatch):
    class FakeClient:
        def __init__(self, security_token=None):
            self.security_token = security_token

        def query_day_ahead_prices(self, bidding_zone_eic, period_start, period_end):
            assert bidding_zone_eic == "10Y1001A1001A82H"
            assert period_start == datetime(2026, 6, 1, 0, 0)
            assert period_end == datetime(2026, 6, 2, 0, 0)
            return [
                {
                    "bidding_zone_eic": bidding_zone_eic,
                    "position": 1,
                    "interval_start_utc": "2026-06-01T00:00Z",
                    "interval_end_utc": "2026-06-01T01:00Z",
                    "period_start_utc": "2026-06-01T00:00Z",
                    "period_end_utc": "2026-06-02T00:00Z",
                    "resolution": "PT60M",
                    "price": 51.23,
                    "currency": "EUR",
                    "unit": "EUR/MWH",
                }
            ]

        def close(self):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.entsoe.EntsoeClient", FakeClient)
    spider = EntsoeDayAheadPricesSpider(
        area="DE-LU",
        start_date="2026-06-01",
        end_date="2026-06-02",
        security_token="token",
    )

    records = list(spider.crawl())

    assert len(records) == 1
    assert records[0].source == "ENTSO-E Transparency Platform"
    assert records[0].market == "day_ahead"
    assert records[0].region == "DE-LU"
    assert records[0].trade_date.isoformat() == "2026-06-01"
    assert records[0].metric == "price_position_001"
    assert records[0].value == 51.23
    assert records[0].unit == "EUR/MWH"
    assert records[0].raw["documentType"] == "A44"
    assert records[0].raw["contract_MarketAgreement.type"] == "A01"


def test_entsoe_spider_rejects_unknown_area(monkeypatch):
    class FakeClient:
        def __init__(self, security_token=None):
            pass

    monkeypatch.setattr("powertrade_crawler.spiders.entsoe.EntsoeClient", FakeClient)

    with pytest.raises(ValueError, match="Unknown ENTSO-E bidding zone"):
        EntsoeDayAheadPricesSpider(area="XX", security_token="token")
