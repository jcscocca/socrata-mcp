"""HTTP layer: polite throttling, retries with backoff, portal-error surfacing.

All portal traffic goes through HttpClient.get_json so behavior (token header,
throttle, retry) is uniform and tests can inject an httpx.MockTransport.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx

from .config import Config
from .errors import PortalError

_log = logging.getLogger(__name__)

RETRYABLE_STATUSES = {429, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_BACKOFF = 30.0


def _portal_message(response: httpx.Response) -> str:
    """Extract the portal's actual error message from a response body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("message", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value
    text = (response.text or "").strip()
    return text[:500] if text else f"HTTP {response.status_code}"


class HttpClient:
    def __init__(
        self,
        config: Config,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self._last_request_at: float | None = None
        headers = {}
        if config.app_token:
            headers["X-App-Token"] = config.app_token
        self._client = httpx.Client(
            transport=transport, headers=headers, timeout=config.timeout,
            follow_redirects=True,
        )

    def _throttle(self) -> None:
        if self.config.throttle_interval <= 0 or self._last_request_at is None:
            return
        elapsed = self.clock() - self._last_request_at
        remaining = self.config.throttle_interval - elapsed
        if remaining > 0:
            self.sleep(remaining)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        last_error: str = ""
        last_status: int | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            self._last_request_at = self.clock()
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error, last_status = str(exc), None
                _log.warning("Transport error for %s (attempt %d): %s", url, attempt + 1, exc)
                self.sleep(min(0.5 * 2**attempt, MAX_BACKOFF))
                continue
            if response.status_code in RETRYABLE_STATUSES:
                last_error, last_status = _portal_message(response), response.status_code
                retry_after = response.headers.get("Retry-After")
                try:
                    backoff = float(retry_after) if retry_after else 0.5 * 2**attempt
                except ValueError:
                    backoff = 0.5 * 2**attempt
                _log.warning(
                    "HTTP %d for %s (attempt %d), backing off %.1fs",
                    response.status_code, url, attempt + 1, backoff,
                )
                self.sleep(min(backoff, MAX_BACKOFF))
                continue
            if response.is_error:
                raise PortalError(
                    _portal_message(response), status=response.status_code, url=url
                )
            return response.json()
        raise PortalError(
            f"Giving up after {MAX_ATTEMPTS} attempts: {last_error}",
            status=last_status,
            url=url,
        )
