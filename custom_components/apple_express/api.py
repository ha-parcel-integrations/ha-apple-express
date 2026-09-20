"""Apple Express public tracking API client."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class AppleExpressApiError(Exception):
    """Raised when an Apple Express API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Apple Express API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class AppleExpressApiClient:
    """Client for the public Apple Express tracking endpoint.

    No authentication: the endpoint is keyed on one order or reference code.
    A successful body contains ``orderDetails`` and ``statusDetails``.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the complete carrier response on success, or ``None`` when the
        carrier does not know the code — it answers that with ``HTTP 200`` and
        a body whose three sections are all ``null``, never a 404. Every other
        non-200 response stays an explicit update/setup error.
        """
        url = TRACKING_API_URL.format(tracking_code=quote(tracking_code, safe=""))
        async with self._session.get(url) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise AppleExpressApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise AppleExpressApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise AppleExpressApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise AppleExpressApiError("unexpected body (not a JSON object)")

        # The keys are present and explicitly null — an empty body is still
        # malformed, not a not-found.
        if (
            "orderDetails" in payload
            and payload["orderDetails"] is None
            and payload.get("statusDetails") is None
        ):
            return None

        if not isinstance(payload.get("orderDetails"), dict):
            raise AppleExpressApiError("success body without orderDetails")
        if not isinstance(payload.get("statusDetails"), list):
            raise AppleExpressApiError("success body without statusDetails")
        return payload
