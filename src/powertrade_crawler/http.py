from time import sleep

import httpx
from loguru import logger

from powertrade_crawler.config import get_settings


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
        last_error: Exception | None = None
        for attempt in range(self.retry_times + 1):
            try:
                response = self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                logger.warning("GET {} failed on attempt {}: {}", url, attempt + 1, exc)
                if attempt < self.retry_times:
                    sleep(1 + attempt)
        raise RuntimeError(f"GET {url} failed after retries") from last_error

    def close(self) -> None:
        self.client.close()
