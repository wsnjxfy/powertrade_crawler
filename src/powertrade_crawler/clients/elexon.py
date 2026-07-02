from time import monotonic, sleep
from typing import Any

import httpx
from loguru import logger

from powertrade_crawler.config import get_settings
from powertrade_crawler.credentials import get_credential


class ElexonClient:
    base_url = "https://data.elexon.co.uk/bmrs/api/v1"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = get_credential("elexon_api_key", override=api_key)
        self.min_interval_seconds = settings.elexon_min_interval_seconds
        self.retry_times = settings.request_retry_times
        self.last_request_at = 0.0
        headers = {"User-Agent": settings.user_agent}
        if self.api_key:
            headers["Ocp-Apim-Subscription-Key"] = self.api_key
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers=headers,
            follow_redirects=True,
            trust_env=True,
        )

    def query(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        query_params = {"format": "json", **(params or {})}
        response = self.get(path=path, params=query_params)
        payload = response.json()
        return self.extract_rows(payload)

    def get(self, *, path: str, params: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        url = f"{self.base_url}{path}"
        for attempt in range(self.retry_times + 1):
            self.wait_for_rate_limit()
            try:
                response = self.client.get(url, params=params)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "60"))
                    logger.warning("Elexon rate limited; sleeping {} seconds", retry_after)
                    sleep(retry_after)
                    continue
                if 400 <= response.status_code < 500:
                    raise RuntimeError(
                        f"Elexon rejected request with HTTP {response.status_code}: "
                        f"{self.extract_error_details(response)}"
                    )
                response.raise_for_status()
                return response
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Elexon GET failed on attempt {} with {}",
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < self.retry_times:
                    sleep(1 + attempt)
        raise RuntimeError("Elexon GET failed after retries") from last_error

    def extract_rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return self.flatten_nested_data_rows([row for row in payload if isinstance(row, dict)])
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, dict):
            rows = [data]
        elif isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
        else:
            rows = []
        return self.flatten_nested_data_rows(rows)

    def flatten_nested_data_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for row in rows:
            nested = row.get("data")
            if not isinstance(nested, list):
                flattened.append(row)
                continue
            parent = {key: value for key, value in row.items() if key != "data"}
            for child in nested:
                if isinstance(child, dict):
                    flattened.append({**parent, **child})
        return flattened

    def extract_error_details(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return text[:240] if text else "no error details were provided"
        if isinstance(payload, dict):
            for key in ("message", "title", "detail", "error"):
                value = payload.get(key)
                if value:
                    return str(value)[:240]
        return "no error details were provided"

    def wait_for_rate_limit(self) -> None:
        elapsed = monotonic() - self.last_request_at
        if elapsed < self.min_interval_seconds:
            sleep(self.min_interval_seconds - elapsed)
        self.last_request_at = monotonic()

    def close(self) -> None:
        self.client.close()
