from io import BytesIO
from datetime import datetime, timedelta
from time import monotonic, sleep
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import httpx
from loguru import logger

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential


class EntsoeClient:
    base_url = "https://web-api.tp.entsoe.eu/api"

    def __init__(self, security_token: str | None = None) -> None:
        settings = get_settings()
        self.security_token = get_credential(
            "entsoe_security_token",
            override=security_token,
        )
        if not self.security_token or self.security_token.startswith("replace-with-"):
            raise RuntimeError(
                "ENTSO-E security token is required in .auth/credentials.json."
            )

        self.min_interval_seconds = settings.entsoe_min_interval_seconds
        self.retry_times = settings.request_retry_times
        self.last_request_at = 0.0
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            trust_env=False,
        )

    def query_day_ahead_prices(
        self,
        bidding_zone_eic: str,
        period_start: datetime,
        period_end: datetime,
    ) -> list[dict[str, Any]]:
        params = {
            "securityToken": self.security_token,
            "documentType": "A44",
            "contract_MarketAgreement.type": "A01",
            "in_Domain": bidding_zone_eic,
            "out_Domain": bidding_zone_eic,
            "periodStart": self.format_period(period_start),
            "periodEnd": self.format_period(period_end),
        }
        response = self.get(params=params)
        return self.parse_day_ahead_prices(response.text, bidding_zone_eic)

    def query_document(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = self.get(params={"securityToken": self.security_token, **params})
        if response.content.startswith(b"PK"):
            return self.parse_zip_document(response.content)
        return self.parse_time_series_document(response.text)

    def parse_zip_document(self, content: bytes) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            with ZipFile(BytesIO(content)) as archive:
                for member_name in archive.namelist():
                    if not member_name.lower().endswith(".xml"):
                        continue
                    xml_text = archive.read(member_name).decode("utf-8-sig")
                    for row in self.parse_time_series_document(xml_text):
                        row["archive_member"] = member_name
                        rows.append(row)
        except BadZipFile as exc:
            raise RuntimeError("ENTSO-E returned an invalid ZIP response.") from exc
        return rows

    def get(self, params: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retry_times + 1):
            self.wait_for_rate_limit()
            try:
                response = self.client.get(self.base_url, params=params)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "60"))
                    logger.warning("ENTSO-E rate limited; sleeping {} seconds", retry_after)
                    sleep(retry_after)
                    continue
                if 400 <= response.status_code < 500:
                    details = self.extract_error_details(response.text)
                    raise RuntimeError(
                        f"ENTSO-E rejected request with HTTP {response.status_code}: {details}"
                    )
                response.raise_for_status()
                return response
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "ENTSO-E GET failed on attempt {} with {}",
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < self.retry_times:
                    sleep(1 + attempt)
        raise RuntimeError("ENTSO-E GET failed after retries") from last_error

    def extract_error_details(self, response_text: str) -> str:
        try:
            root = ElementTree.fromstring(response_text)
        except ElementTree.ParseError:
            return "the server returned a non-XML error response"

        reason_code = self.find_text_by_path(root, ["Reason", "code"])
        reason_text = self.find_text_by_path(root, ["Reason", "text"])
        details = " ".join(part for part in (reason_code, reason_text) if part)
        return details or "no error details were provided"

    def wait_for_rate_limit(self) -> None:
        elapsed = monotonic() - self.last_request_at
        if elapsed < self.min_interval_seconds:
            sleep(self.min_interval_seconds - elapsed)
        self.last_request_at = monotonic()

    def parse_day_ahead_prices(
        self,
        xml_text: str,
        bidding_zone_eic: str,
    ) -> list[dict[str, Any]]:
        root = ElementTree.fromstring(xml_text)
        self.raise_if_acknowledgement_rejected(root)
        records: list[dict[str, Any]] = []

        for time_series in self.findall_by_local_name(root, "TimeSeries"):
            currency = self.find_text_by_local_name(time_series, "currency_Unit.name") or "EUR"
            unit = self.find_text_by_local_name(time_series, "price_Measure_Unit.name") or "MWh"
            for period in self.findall_by_local_name(time_series, "Period"):
                start_text = self.find_text_by_path(period, ["timeInterval", "start"])
                end_text = self.find_text_by_path(period, ["timeInterval", "end"])
                resolution = self.find_text_by_local_name(period, "resolution") or "PT60M"
                if not start_text or not end_text:
                    continue
                interval_start = self.parse_entsoe_datetime(start_text)
                interval_end = self.parse_entsoe_datetime(end_text)
                step = self.parse_duration(resolution)

                for point in self.findall_by_local_name(period, "Point"):
                    position_text = self.find_text_by_local_name(point, "position")
                    price_text = self.find_text_by_local_name(point, "price.amount")
                    if not position_text or not price_text:
                        continue
                    position = int(position_text)
                    point_start = interval_start + step * (position - 1)
                    point_end = point_start + step
                    records.append(
                        {
                            "bidding_zone_eic": bidding_zone_eic,
                            "position": position,
                            "interval_start_utc": self.format_response_time(point_start),
                            "interval_end_utc": self.format_response_time(point_end),
                            "period_start_utc": self.format_response_time(interval_start),
                            "period_end_utc": self.format_response_time(interval_end),
                            "resolution": resolution,
                            "price": float(price_text),
                            "currency": currency,
                            "unit": f"{currency}/{unit}",
                        }
                    )

        return records

    def parse_time_series_document(self, xml_text: str) -> list[dict[str, Any]]:
        root = ElementTree.fromstring(xml_text)
        self.raise_if_acknowledgement_rejected(root)
        document = self.flatten_leaf_values(root, excluded_names={"TimeSeries"})
        rows: list[dict[str, Any]] = []

        for series_index, time_series in enumerate(
            self.findall_by_local_name(root, "TimeSeries"),
            start=1,
        ):
            period_names = {"Period", "Available_Period"}
            series = self.flatten_leaf_values(time_series, excluded_names=period_names)
            periods = [
                element
                for element in time_series.iter()
                if self.local_name(element.tag) in period_names
            ]
            if not periods:
                rows.append(
                    {
                        "document": document,
                        "series": series,
                        "period": {},
                        "point": {},
                        "series_index": series_index,
                    }
                )
                continue

            for period_index, period in enumerate(periods, start=1):
                period_values = self.flatten_leaf_values(period, excluded_names={"Point"})
                start_text = self.find_text_by_path(period, ["timeInterval", "start"])
                end_text = self.find_text_by_path(period, ["timeInterval", "end"])
                resolution = self.find_text_by_local_name(period, "resolution")
                points = self.findall_by_local_name(period, "Point")
                if not points:
                    rows.append(
                        {
                            "document": document,
                            "series": series,
                            "period": period_values,
                            "point": {},
                            "series_index": series_index,
                            "period_index": period_index,
                            "period_start_utc": start_text,
                            "period_end_utc": end_text,
                            "resolution": resolution,
                        }
                    )
                    continue

                for point in points:
                    point_values = self.flatten_leaf_values(point)
                    position = self.parse_optional_int(
                        self.find_text_by_local_name(point, "position")
                    )
                    point_start, point_end = self.resolve_point_interval(
                        start_text=start_text,
                        resolution=resolution,
                        position=position,
                    )
                    value_field, value = self.resolve_numeric_value(point_values)
                    rows.append(
                        {
                            "document": document,
                            "series": series,
                            "period": period_values,
                            "point": point_values,
                            "series_index": series_index,
                            "period_index": period_index,
                            "position": position,
                            "period_start_utc": start_text,
                            "period_end_utc": end_text,
                            "interval_start_utc": point_start,
                            "interval_end_utc": point_end,
                            "resolution": resolution,
                            "value_field": value_field,
                            "value": value,
                        }
                    )

        return rows

    def flatten_leaf_values(
        self,
        element: ElementTree.Element,
        excluded_names: set[str] | None = None,
    ) -> dict[str, Any]:
        excluded_names = excluded_names or set()
        values: dict[str, Any] = {}

        def visit(current: ElementTree.Element, path: list[str]) -> None:
            for child in current:
                local_name = self.local_name(child.tag)
                if local_name in excluded_names:
                    continue
                child_path = [*path, local_name]
                if len(child) == 0:
                    if child.text and child.text.strip():
                        self.add_flattened_value(values, ".".join(child_path), child.text.strip())
                    continue
                visit(child, child_path)

        visit(element, [])
        return values

    def add_flattened_value(self, values: dict[str, Any], key: str, value: str) -> None:
        existing = values.get(key)
        if existing is None:
            values[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            values[key] = [existing, value]

    def resolve_numeric_value(
        self,
        point_values: dict[str, Any],
    ) -> tuple[str | None, float | None]:
        preferred_names = (
            "price.amount",
            "quantity",
            "procuredCapacity",
            "availableCapacity",
            "unavailableCapacity",
            "imbalance_Price.amount",
            "energy_Price.amount",
            "reserveBid_Price.amount",
        )
        for preferred_name in preferred_names:
            for key, raw_value in point_values.items():
                if key == preferred_name or key.endswith(f".{preferred_name}"):
                    parsed = self.parse_optional_float(raw_value)
                    if parsed is not None:
                        return key, parsed

        for key, raw_value in point_values.items():
            if key.endswith("position"):
                continue
            parsed = self.parse_optional_float(raw_value)
            if parsed is not None:
                return key, parsed
        return None, None

    def resolve_point_interval(
        self,
        start_text: str | None,
        resolution: str | None,
        position: int | None,
    ) -> tuple[str | None, str | None]:
        if not start_text or not resolution or position is None:
            return None, None
        start = self.parse_entsoe_datetime(start_text)
        step = self.parse_duration(resolution)
        point_start = start + step * (position - 1)
        return self.format_response_time(point_start), self.format_response_time(point_start + step)

    def parse_optional_int(self, value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def parse_optional_float(self, value: Any) -> float | None:
        if isinstance(value, list) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def raise_if_acknowledgement_rejected(self, root: ElementTree.Element) -> None:
        reason_text = self.find_text_by_path(root, ["Reason", "text"])
        reason_code = self.find_text_by_path(root, ["Reason", "code"])
        if self.local_name(root.tag).endswith("Acknowledgement_MarketDocument") or reason_text:
            details = f"{reason_code or ''} {reason_text or ''}".strip()
            raise RuntimeError(f"ENTSO-E rejected request: {details}")

    def findall_by_local_name(
        self,
        element: ElementTree.Element,
        local_name: str,
    ) -> list[ElementTree.Element]:
        return [child for child in element.iter() if self.local_name(child.tag) == local_name]

    def find_text_by_local_name(
        self,
        element: ElementTree.Element,
        local_name: str,
    ) -> str | None:
        for child in element.iter():
            if self.local_name(child.tag) == local_name and child.text:
                return child.text.strip()
        return None

    def find_text_by_path(
        self,
        element: ElementTree.Element,
        local_names: list[str],
    ) -> str | None:
        current = element
        for local_name in local_names:
            next_child = None
            for child in current:
                if self.local_name(child.tag) == local_name:
                    next_child = child
                    break
            if next_child is None:
                return None
            current = next_child
        return current.text.strip() if current.text else None

    def local_name(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def format_period(self, value: datetime) -> str:
        return value.strftime("%Y%m%d%H%M")

    def parse_entsoe_datetime(self, value: str) -> datetime:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).replace(tzinfo=None)

    def format_response_time(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%MZ")

    def parse_duration(self, value: str) -> timedelta:
        if not value.startswith("PT"):
            raise ValueError(f"Unsupported ENTSO-E duration: {value}")
        if value.endswith("M"):
            return timedelta(minutes=int(value[2:-1]))
        if value.endswith("H"):
            return timedelta(hours=int(value[2:-1]))
        raise ValueError(f"Unsupported ENTSO-E duration: {value}")

    def close(self) -> None:
        self.client.close()
