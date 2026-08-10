from time import sleep
from collections.abc import Callable
from typing import Any

import httpx
from loguru import logger

from powertrade_crawler.config import get_settings
from powertrade_crawler.elecheck_auth import resolve_elecheck_authorization
from powertrade_crawler.network_errors import (
    NetworkFailure,
    NetworkFailureKind,
    failure_from_exception,
    failure_from_response,
    failure_from_status,
    retry_after_seconds,
)


class ElecheckUnauthorizedError(NetworkFailure):
    def __init__(self, *, attempts: int = 1) -> None:
        super().__init__(
            service="Elecheck",
            kind=NetworkFailureKind.UNAUTHORIZED,
            status_code=401,
            retryable=False,
            attempts=attempts,
        )


class ElecheckClient:
    base_url = "https://elecheck.aienertech.cn"
    clear_price_path = "/electricCheckApi/queryData/clearPrice"
    purchasing_path = "/electricCheckApi/queryData/purchasing"
    mechanism_electricity_price_path = "/electricCheckApi/query/mechanismElectricityPrice"
    default_referer = "https://servicewechat.com/wxda1c8f9611eb040a/47/page-frame.html"

    def __init__(self, authorization: str | None = None) -> None:
        settings = get_settings()
        self.authorization = resolve_elecheck_authorization(authorization)

        self.retry_times = settings.request_retry_times
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=settings.request_timeout_seconds,
            headers=self.build_headers(settings.user_agent),
            follow_redirects=True,
            trust_env=False,
        )

    def build_headers(self, user_agent: str) -> dict[str, str]:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json",
            "Referer": self.default_referer,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": user_agent,
            "xweb_xhr": "1",
        }
        if self.authorization:
            headers["authorization"] = self.authorization
        return headers

    def fetch_clear_price_detail(
        self,
        *,
        area_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        payload = self.post_clear_price(
            "detail",
            self.build_payload(area_code, start_date, end_date),
        )
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(f"Elecheck detail data field is not a list: {payload}")
        return data

    def fetch_clear_price_statistics(
        self,
        *,
        area_code: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        payload = self.post_clear_price(
            "statistics",
            self.build_payload(area_code, start_date, end_date),
        )
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError(f"Elecheck statistics data field is not an object: {payload}")
        return data

    def fetch_purchasing_province_month_list(self) -> list[dict[str, Any]]:
        payload = self.get_purchasing("provinceMonthList", params={})
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(f"Elecheck purchasing provinceMonthList data is not a list: {payload}")
        return data

    def fetch_purchasing_province_list(self) -> list[str]:
        payload = self.get_purchasing("provinceList", params={})
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(f"Elecheck purchasing provinceList data is not a list: {payload}")
        return [str(item) for item in data if item not in (None, "")]

    def fetch_purchasing_list(
        self,
        *,
        province: str,
        latest_data_month: str,
    ) -> dict[str, Any]:
        payload = self.get_purchasing(
            "list",
            params={
                "province": province,
                "latestDataMonth": latest_data_month,
            },
        )
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError(f"Elecheck purchasing list data field is not an object: {payload}")
        return data

    def fetch_purchasing_chart_data(
        self,
        *,
        province: str,
        start_month: str,
        end_month: str,
    ) -> dict[str, Any] | None:
        payload = self.get_purchasing(
            "chartData",
            params={
                "endMonth": end_month,
                "startMonth": start_month,
                "province": province,
            },
        )
        data = payload.get("data")
        if data is None:
            return None
        if not isinstance(data, dict):
            raise RuntimeError(f"Elecheck purchasing chartData field is not an object: {payload}")
        return data

    def fetch_mechanism_electricity_price_list(self) -> list[dict[str, Any]]:
        payload = self.get_mechanism_electricity_price("list", params={})
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError(
                f"Elecheck mechanism electricity price data field is not a list: {payload}"
            )
        return data

    def build_payload(self, area_code: str, start_date: str, end_date: str) -> dict[str, str]:
        return {
            "areaCode": area_code,
            "startDate": start_date,
            "endDate": end_date,
        }

    def post_clear_price(self, endpoint: str, payload: dict[str, str]) -> dict[str, Any]:
        path = f"{self.clear_price_path}/{endpoint}"
        return self._request_json(
            method="POST",
            path=path,
            request=lambda: self.client.post(path, json=payload),
        )

    def get_purchasing(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        path = f"{self.purchasing_path}/{endpoint}"
        return self._request_json(
            method="GET",
            path=path,
            request=lambda: self.client.get(path, params=params),
        )

    def get_mechanism_electricity_price(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        path = f"{self.mechanism_electricity_price_path}/{endpoint}"
        return self._request_json(
            method="GET",
            path=path,
            request=lambda: self.client.get(path, params=params),
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        request: Callable[[], httpx.Response],
    ) -> dict[str, Any]:
        for attempt in range(self.retry_times + 1):
            try:
                response = request()
                if response.status_code == 401:
                    raise ElecheckUnauthorizedError(attempts=attempt + 1)
                if response.status_code >= 400:
                    failure = failure_from_response(
                        "Elecheck",
                        response,
                        attempts=attempt + 1,
                    )
                    if failure.retryable and attempt < self.retry_times:
                        delay = retry_after_seconds(response, default=1 + attempt)
                        logger.warning(
                            "Elecheck {} {} failed on attempt {}: {}",
                            method,
                            path,
                            attempt + 1,
                            failure.kind.value,
                        )
                        sleep(delay)
                        continue
                    raise failure
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("response root is not an object")
                payload_code = data.get("code")
                if payload_code != 200:
                    try:
                        status_code = int(payload_code)
                    except (TypeError, ValueError):
                        status_code = 502
                    if status_code == 401:
                        raise ElecheckUnauthorizedError(attempts=attempt + 1)
                    failure = failure_from_status(
                        "Elecheck",
                        status_code,
                        detail=str(data.get("message") or data.get("msg") or ""),
                        attempts=attempt + 1,
                    )
                    if failure.retryable and attempt < self.retry_times:
                        logger.warning(
                            "Elecheck {} {} failed on attempt {}: {}",
                            method,
                            path,
                            attempt + 1,
                            failure.kind.value,
                        )
                        sleep(1 + attempt)
                        continue
                    raise failure
                return data
            except NetworkFailure:
                raise
            except (httpx.TimeoutException, httpx.RequestError, ValueError) as exc:
                failure = failure_from_exception("Elecheck", exc, attempts=attempt + 1)
                logger.warning(
                    "Elecheck {} {} failed on attempt {}: {}",
                    method,
                    path,
                    attempt + 1,
                    failure.kind.value,
                )
                if failure.retryable and attempt < self.retry_times:
                    sleep(1 + attempt)
                    continue
                raise failure from exc
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.client.close()
