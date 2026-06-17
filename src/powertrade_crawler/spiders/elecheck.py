from collections.abc import Iterable
import calendar
from datetime import date, timedelta
from typing import Any

from powertrade_crawler.clients.elecheck import ElecheckClient
from powertrade_crawler.config import get_settings
from powertrade_crawler.models import (
    ElecheckClearPriceRecord,
    ElecheckPurchasingProvinceRecord,
    ElecheckPurchasingRecord,
)
from powertrade_crawler.models import ElecheckMechanismElectricityPriceRecord
from powertrade_crawler.spiders.base import BaseSpider


class ElecheckClearPriceSpider(BaseSpider):
    name = "elecheck_clear_price"
    source = "易能电易查"
    source_url = "https://elecheck.aienertech.cn"

    detail_price_fields = {
        "avgDayAheadPrice": "avg_day_ahead_price",
        "avgRealTimePrice": "avg_real_time_price",
    }
    statistics_scalar_fields = {
        "negativePriceDateCount": "negative_price_date_count",
        "negativePriceTimeCount": "negative_price_time_count",
        "zeroPriceDateCount": "zero_price_date_count",
        "zeroPriceTimeCount": "zero_price_time_count",
        "maxPriceDateCount": "max_price_date_count",
        "maxPriceTimeCount": "max_price_time_count",
        "dayAheadAvgPrice": "day_ahead_avg_price",
        "realTimeAvgPrice": "real_time_avg_price",
    }
    supported_periods = {"recent", "7d", "30d", "half-year"}

    def __init__(
        self,
        client: ElecheckClient | None = None,
        *,
        area_code: str | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        period: str | None = None,
        daily: bool = False,
    ) -> None:
        settings = get_settings()
        today = date.today()
        self.area_code = area_code or settings.elecheck_area_code
        configured_end_date = (
            self.parse_date(end_date)
            or self.parse_date(settings.elecheck_end_date)
            or today
        )
        self.end_date = configured_end_date
        self.start_date = (
            self.parse_date(start_date)
            or self.parse_date(settings.elecheck_start_date)
            or self.resolve_period_start_date(period, configured_end_date)
        )
        self.daily = daily
        self.client = client or ElecheckClient()

    def crawl(self) -> Iterable[ElecheckClearPriceRecord]:
        for request_start_date, request_end_date in self.iter_request_date_ranges():
            yield from self.crawl_date_range(request_start_date, request_end_date)

    def iter_request_date_ranges(self) -> Iterable[tuple[date, date]]:
        if self.start_date > self.end_date:
            raise ValueError("Elecheck start_date must be before or equal to end_date.")
        if not getattr(self, "daily", False):
            yield self.start_date, self.end_date
            return

        current_date = self.start_date
        while current_date <= self.end_date:
            yield current_date, current_date
            current_date += timedelta(days=1)

    def crawl_date_range(
        self,
        request_start_date: date,
        request_end_date: date,
    ) -> Iterable[ElecheckClearPriceRecord]:
        detail_rows = self.client.fetch_clear_price_detail(
            area_code=self.area_code,
            start_date=request_start_date.isoformat(),
            end_date=request_end_date.isoformat(),
        )
        for row in detail_rows:
            yield from self.detail_row_to_records(row, request_start_date, request_end_date)

        statistics = self.client.fetch_clear_price_statistics(
            area_code=self.area_code,
            start_date=request_start_date.isoformat(),
            end_date=request_end_date.isoformat(),
        )
        yield from self.statistics_to_records(statistics, request_start_date, request_end_date)

    def detail_row_to_records(
        self,
        row: dict[str, Any],
        request_start_date: date | None = None,
        request_end_date: date | None = None,
    ) -> Iterable[ElecheckClearPriceRecord]:
        record_start_date = request_start_date or self.start_date
        record_end_date = request_end_date or self.end_date
        time96 = self.optional_str(row.get("time96"))
        for field_name, metric in self.detail_price_fields.items():
            value = self.optional_float(row.get(field_name))
            if value is None:
                continue
            yield ElecheckClearPriceRecord(
                endpoint="detail",
                area_code=self.area_code,
                start_date=record_start_date,
                end_date=record_end_date,
                time96=time96,
                metric=metric,
                value=value,
                unit="CNY/MWh",
                currency="CNY",
                raw=row,
            )

    def statistics_to_records(
        self,
        statistics: dict[str, Any],
        request_start_date: date | None = None,
        request_end_date: date | None = None,
    ) -> Iterable[ElecheckClearPriceRecord]:
        record_start_date = request_start_date or self.start_date
        record_end_date = request_end_date or self.end_date
        for field_name, metric in self.statistics_scalar_fields.items():
            value = self.optional_float(statistics.get(field_name))
            if value is None:
                continue
            yield ElecheckClearPriceRecord(
                endpoint="statistics",
                area_code=self.area_code,
                start_date=record_start_date,
                end_date=record_end_date,
                metric=metric,
                value=value,
                unit=self.resolve_statistics_unit(metric),
                currency="CNY" if metric.endswith("_price") else None,
                raw=statistics,
            )

    def resolve_statistics_unit(self, metric: str) -> str:
        if metric.endswith("_price"):
            return "CNY/MWh"
        if metric.endswith("_date_count"):
            return "day"
        if metric.endswith("_time_count"):
            return "time96"
        return "count"

    def resolve_period_start_date(self, period: str | None, end_date: date) -> date:
        selected_period = period or "7d"
        if selected_period == "recent":
            return end_date
        if selected_period == "7d":
            return end_date - timedelta(days=7)
        if selected_period == "30d":
            return self.add_months(end_date, -1)
        if selected_period == "half-year":
            return self.add_months(end_date, -6)
        supported = ", ".join(sorted(self.supported_periods))
        raise ValueError(f"Unsupported Elecheck period: {selected_period}. Supported: {supported}")

    def add_months(self, value: date, months: int) -> date:
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    def parse_date(self, value: str | date | None) -> date | None:
        if not value:
            return None
        if isinstance(value, date):
            return value
        return date.fromisoformat(value)

    def optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    def optional_str(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value)

    def close(self) -> None:
        self.client.close()


class ElecheckPurchasingBaseSpider(BaseSpider):
    source = "易能电易查"
    source_url = "https://elecheck.aienertech.cn"

    purchasing_metrics = {
        "purchasingPrice": "purchasing_price",
        "purchasingSystemOperatingCost": "purchasing_system_operating_cost",
        "lineLossCost": "line_loss_cost",
        "purchasingSum": "purchasing_sum",
    }
    purchasing_type_metrics = {
        "代理购电价": "purchasing_price",
        "系统运行费折价": "purchasing_system_operating_cost",
        "线损折价": "line_loss_cost",
        "合计": "purchasing_sum",
    }

    def __init__(
        self,
        client: ElecheckClient | None = None,
        *,
        data_month: str | None = None,
        province_name: str | None = None,
    ) -> None:
        self.client = client or ElecheckClient()
        self.data_month = data_month or self.resolve_latest_data_month(province_name or "全国")
        self.province_name = province_name

    def resolve_latest_data_month(self, province_name: str) -> str:
        month_rows = self.client.fetch_purchasing_province_month_list()
        for row in month_rows:
            if row.get("provinceName") == province_name:
                months = row.get("dataMonthList") or []
                if months:
                    return str(months[0])
        raise RuntimeError(f"Cannot resolve latest Elecheck purchasing month for {province_name}.")

    def metric_from_type(self, value: str) -> str:
        return self.purchasing_type_metrics.get(value, value)

    def optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    def add_months_to_month(self, value: str, months: int) -> str:
        year, month = (int(part) for part in value.split("-"))
        month_index = month - 1 + months
        new_year = year + month_index // 12
        new_month = month_index % 12 + 1
        return f"{new_year:04d}-{new_month:02d}"

    def close(self) -> None:
        self.client.close()


class ElecheckPurchasingNationalMonthSpider(ElecheckPurchasingBaseSpider):
    name = "elecheck_purchasing_national_month"

    def crawl(self) -> Iterable[ElecheckPurchasingRecord]:
        data = self.client.fetch_purchasing_list(
            province="",
            latest_data_month=self.data_month,
        )
        latest_data_month = str(data.get("latestDataMonth") or self.data_month)
        for row in data.get("tableDataList") or []:
            yield from self.table_row_to_records(row, latest_data_month)
        for row in data.get("topDataList") or []:
            yield from self.top_row_to_records(row, latest_data_month)

    def table_row_to_records(
        self,
        row: dict[str, Any],
        data_month: str,
    ) -> Iterable[ElecheckPurchasingRecord]:
        province_name = row.get("provinceName")
        for field_name, metric in self.purchasing_metrics.items():
            yield ElecheckPurchasingRecord(
                endpoint="list",
                data_kind="national_table",
                data_month=data_month,
                province_name=province_name,
                metric=metric,
                value=self.optional_float(row.get(field_name)),
                raw=row,
            )

    def top_row_to_records(
        self,
        row: dict[str, Any],
        data_month: str,
    ) -> Iterable[ElecheckPurchasingRecord]:
        metric = self.metric_from_type(str(row.get("type") or ""))
        for statistic, province_field, value_field in [
            ("max", "maxProvinceName", "maxValue"),
            ("min", "minProvinceName", "minValue"),
        ]:
            yield ElecheckPurchasingRecord(
                endpoint="list",
                data_kind="national_top",
                data_month=data_month,
                province_name=None,
                metric=metric,
                value=self.optional_float(row.get(value_field)),
                statistic=statistic,
                related_province_name=row.get(province_field),
                raw=row,
            )


class ElecheckPurchasingNationalRangeSpider(ElecheckPurchasingNationalMonthSpider):
    name = "elecheck_purchasing_national_range"
    default_start_month = "2024-02"

    def __init__(
        self,
        client: ElecheckClient | None = None,
        *,
        start_month: str | None = None,
        end_month: str | None = None,
    ) -> None:
        self.client = client or ElecheckClient()
        self.start_month = start_month or self.default_start_month
        self.end_month = end_month or self.resolve_latest_data_month("全国")
        self.validate_month_range(self.start_month, self.end_month)

    def crawl(self) -> Iterable[ElecheckPurchasingRecord]:
        for data_month in self.iter_months(self.start_month, self.end_month):
            data = self.client.fetch_purchasing_list(
                province="",
                latest_data_month=data_month,
            )
            latest_data_month = str(data.get("latestDataMonth") or data_month)
            for row in data.get("tableDataList") or []:
                yield from self.table_row_to_records(row, latest_data_month)
            for row in data.get("topDataList") or []:
                yield from self.top_row_to_records(row, latest_data_month)

    def iter_months(self, start_month: str, end_month: str) -> Iterable[str]:
        start_year, start_month_number = self.parse_month(start_month)
        end_year, end_month_number = self.parse_month(end_month)
        year = start_year
        month = start_month_number
        while (year, month) <= (end_year, end_month_number):
            yield f"{year:04d}-{month:02d}"
            month += 1
            if month > 12:
                year += 1
                month = 1

    def validate_month_range(self, start_month: str, end_month: str) -> None:
        if self.parse_month(start_month) > self.parse_month(end_month):
            raise ValueError("Elecheck purchasing start_month must be before or equal to end_month.")

    def parse_month(self, value: str) -> tuple[int, int]:
        try:
            year_text, month_text = value.split("-", maxsplit=1)
            year = int(year_text)
            month = int(month_text)
        except ValueError as exc:
            raise ValueError(f"Invalid Elecheck purchasing month: {value}. Expected YYYY-MM.") from exc
        if month < 1 or month > 12:
            raise ValueError(f"Invalid Elecheck purchasing month: {value}. Expected YYYY-MM.")
        return year, month


class ElecheckPurchasingProvinceListSpider(BaseSpider):
    name = "elecheck_purchasing_province_list"
    source = "易能电易查"
    source_url = "https://elecheck.aienertech.cn"

    def __init__(self, client: ElecheckClient | None = None) -> None:
        self.client = client or ElecheckClient()

    def crawl(self) -> Iterable[ElecheckPurchasingProvinceRecord]:
        provinces = self.client.fetch_purchasing_province_list()
        for province_name in provinces:
            yield ElecheckPurchasingProvinceRecord(
                province_name=province_name,
                raw={"provinceName": province_name},
            )

    def close(self) -> None:
        self.client.close()


class ElecheckPurchasingProvinceMonthSpider(ElecheckPurchasingBaseSpider):
    name = "elecheck_purchasing_province_month"

    def __init__(
        self,
        client: ElecheckClient | None = None,
        *,
        data_month: str | None = None,
        province_name: str | None = None,
        start_month: str | None = None,
        end_month: str | None = None,
    ) -> None:
        if not province_name:
            raise ValueError("--area is required for elecheck_purchasing_province_month.")
        self.custom_start_month = start_month
        self.custom_end_month = end_month
        resolved_data_month = data_month or end_month
        super().__init__(
            client=client,
            data_month=resolved_data_month,
            province_name=province_name,
        )

    def crawl(self) -> Iterable[ElecheckPurchasingRecord]:
        data = self.client.fetch_purchasing_list(
            province=self.province_name or "",
            latest_data_month=self.data_month,
        )
        latest_data_month = str(data.get("latestDataMonth") or self.data_month)
        for row in data.get("provinceDataList") or []:
            yield self.province_summary_row_to_record(row, latest_data_month)

        start_month = self.custom_start_month or self.add_months_to_month(latest_data_month, -11)
        end_month = self.custom_end_month or latest_data_month
        chart_data = self.client.fetch_purchasing_chart_data(
            province=self.province_name or "",
            start_month=start_month,
            end_month=end_month,
        )
        if chart_data is not None:
            yield from self.chart_data_to_records(chart_data)

    def province_summary_row_to_record(
        self,
        row: dict[str, Any],
        data_month: str,
    ) -> ElecheckPurchasingRecord:
        return ElecheckPurchasingRecord(
            endpoint="list",
            data_kind="province_summary",
            data_month=data_month,
            province_name=self.province_name,
            metric=self.metric_from_type(str(row.get("type") or "")),
            value=self.optional_float(row.get("value")),
            diff_value=self.optional_float(row.get("diffValue")),
            raw=row,
        )

    def chart_data_to_records(
        self,
        chart_data: dict[str, Any],
    ) -> Iterable[ElecheckPurchasingRecord]:
        months = chart_data.get("xAxisData") or []
        for series in chart_data.get("series") or []:
            metric = self.metric_from_type(str(series.get("name") or ""))
            values = series.get("data") or []
            for month, value in zip(months, values, strict=False):
                yield ElecheckPurchasingRecord(
                    endpoint="chartData",
                    data_kind="province_trend",
                    data_month=str(month),
                    province_name=self.province_name,
                    metric=metric,
                    value=self.optional_float(value),
                    raw={
                        "legendDataList": chart_data.get("legendDataList"),
                        "series": series,
                        "xAxisData": months,
                    },
                )


class ElecheckMechanismElectricityPriceSpider(BaseSpider):
    name = "elecheck_mechanism_electricity_price"
    source = "易能电易查"
    source_url = "https://elecheck.aienertech.cn"

    def __init__(self, client: ElecheckClient | None = None) -> None:
        self.client = client or ElecheckClient()

    def crawl(self) -> Iterable[ElecheckMechanismElectricityPriceRecord]:
        rows = self.client.fetch_mechanism_electricity_price_list()
        for row in rows:
            yield self.row_to_record(row)

    def row_to_record(self, row: dict[str, Any]) -> ElecheckMechanismElectricityPriceRecord:
        return ElecheckMechanismElectricityPriceRecord(
            region_name=str(row["regionName"]),
            region=row.get("region"),
            category=str(row["category"]),
            price=self.optional_float(row.get("price")),
            clear_price=self.optional_float(row.get("clearPrice")),
            raw=row,
        )

    def optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    def close(self) -> None:
        self.client.close()
