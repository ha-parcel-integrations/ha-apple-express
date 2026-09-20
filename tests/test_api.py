"""Tests for the Apple Express API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.apple_express.api import (
    AppleExpressApiClient,
    AppleExpressApiError,
)

from .payloads import delivered_sample, not_found_sample


def _session_returning(status: int, body: object, headers: dict | None = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.json = AsyncMock(
        side_effect=json.JSONDecodeError("x", body, 0) if isinstance(body, str) else None,
        return_value=None if isinstance(body, str) else body,
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = context
    return session


async def test_get_parcel_returns_complete_apple_response_and_encodes_code():
    session = _session_returning(200, delivered_sample("AB 12/3"))
    parcel = await AppleExpressApiClient(session).async_get_parcel("AB 12/3")
    assert parcel["orderDetails"]["appleTrackingNumber"] == "AB 12/3"
    assert "/AB%2012%2F3/" in session.get.call_args.args[0]


async def test_get_parcel_returns_none_for_an_unknown_code():
    """The carrier answers an unknown code with 200 and every section null."""
    session = _session_returning(200, not_found_sample())
    assert await AppleExpressApiClient(session).async_get_parcel("90000009") is None


@pytest.mark.parametrize("body", [{}, {"orderDetails": {}}, {"statusDetails": []}, []])
async def test_get_parcel_rejects_incomplete_success_body(body):
    with pytest.raises(AppleExpressApiError):
        await AppleExpressApiClient(_session_returning(200, body)).async_get_parcel("90000001")


async def test_get_parcel_handles_transport_and_body_errors():
    for status in (400, 404, 500):
        with pytest.raises(AppleExpressApiError) as error:
            await AppleExpressApiClient(_session_returning(status, {})).async_get_parcel("90000001")
        assert error.value.status_code == status
    with pytest.raises(AppleExpressApiError):
        await AppleExpressApiClient(_session_returning(200, "not json")).async_get_parcel("90000001")


async def test_get_parcel_exposes_retry_after_and_propagates_network_error():
    with pytest.raises(AppleExpressApiError) as error:
        await AppleExpressApiClient(_session_returning(429, {}, {"Retry-After": "12"})).async_get_parcel("90000001")
    assert error.value.retry_after == 12
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientError("offline")
    with pytest.raises(aiohttp.ClientError):
        await AppleExpressApiClient(session).async_get_parcel("90000001")
