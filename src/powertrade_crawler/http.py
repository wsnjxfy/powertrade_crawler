from time import sleep

import httpx
from loguru import logger

from powertrade_crawler.config import get_settings
from powertrade_crawler.network_errors import (
    NetworkFailure,
    failure_from_exception,
    failure_from_response,
    retry_after_seconds,
)


class HttpClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.retry_times = settings.request_retry_times
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )

    def get_text(self, url: str, **kwargs) -> str:
        response = self.get(url, **kwargs)
        return response.text

    def get_json(self, url: str, **kwargs):
        response = self.get(url, **kwargs)
        return response.json()

    def get(self, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self.retry_times + 1):
            try:
                response = self.client.get(url, **kwargs)
                if response.is_error:
                    failure = failure_from_response(
                        "公开网站",
                        response,
                        attempts=attempt + 1,
                    )
                    if failure.retryable and attempt < self.retry_times:
                        delay = retry_after_seconds(response, default=1 + attempt)
                        logger.warning(
                            "GET {} failed on attempt {}: {}",
                            url,
                            attempt + 1,
                            failure.kind.value,
                        )
                        sleep(delay)
                        continue
                    raise failure
                return response
            except NetworkFailure:
                raise
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                failure = failure_from_exception("公开网站", exc, attempts=attempt + 1)
                logger.warning(
                    "GET {} failed on attempt {}: {}",
                    url,
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
