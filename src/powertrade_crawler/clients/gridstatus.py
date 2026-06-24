import csv
from io import StringIO
from time import monotonic, sleep
from typing import Any

import httpx
from loguru import logger

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential


class GridStatusClient:
    base_url = "https://api.gridstatus.io"

    def __init__(self) -> None:
        settings = get_settings()
        api_key = get_credential("gridstatus_api_key")
        if not api_key:
            raise RuntimeError(
                "GridStatus API key is required in .auth/credentials.json."
            )

        self.api_key = api_key
        self.min_interval_seconds = settings.gridstatus_min_interval_seconds
        self.retry_times = settings.request_retry_times
        self.last_request_at = 0.0
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            trust_env=False,
        )

    def request_rows(self, request_config: dict[str, Any]) -> list[dict[str, Any]]:
        path = self.build_path(request_config)
        params = dict(request_config.get("params", {}))
        params["api_key"] = self.api_key

        response = self.get(path, params=params)
        if params.get("return_format") == "csv":
            return self.parse_csv(response.text)

        payload = response.json()
        if payload.get("status_code") != 200:
            raise RuntimeError(f"GridStatus API returned non-200 payload: {payload}")
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(f"GridStatus API data field is not a list: {payload}")
        return data

    def build_path(self, request_config: dict[str, Any]) -> str:
        request_type = request_config["type"]
        dataset = request_config.get("dataset")
        location = request_config.get("location")

        if request_type == "datasets":
            return "/v1/datasets"
        if request_type == "dataset_query" and dataset:
            return f"/v1/datasets/{dataset}/query"
        if request_type == "dataset_location_query" and dataset and location:
            return f"/v1/datasets/{dataset}/query/location/{location}"
        if request_type == "dataset_updates" and dataset:
            return f"/v1/dataset-updates/{dataset}"
        raise ValueError(f"Unsupported GridStatus request config: {request_config}")

    def get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retry_times + 1):
            self.wait_for_rate_limit()
            try:
                response = self.client.get(path, params=params)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    logger.warning("GridStatus rate limited; sleeping {} seconds", retry_after)
                    sleep(retry_after)
                    continue
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                logger.warning("GridStatus GET {} failed on attempt {}: {}", path, attempt + 1, exc)
                if attempt < self.retry_times:
                    sleep(1 + attempt)
        raise RuntimeError(f"GridStatus GET {path} failed after retries") from last_error

    def wait_for_rate_limit(self) -> None:
        elapsed = monotonic() - self.last_request_at
        if elapsed < self.min_interval_seconds:
            sleep(self.min_interval_seconds - elapsed)
        self.last_request_at = monotonic()

    def parse_csv(self, text: str) -> list[dict[str, Any]]:
        sample = text[:2048]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        reader = csv.DictReader(StringIO(text), dialect=dialect)
        return [dict(row) for row in reader]

    def close(self) -> None:
        self.client.close()
