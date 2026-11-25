"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.apple_express.const import (
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.apple_express.coordinator import AppleExpressCoordinator

from .payloads import ACTIVE_CODE, DELIVERED_CODE, active_sample

CLIENT = "custom_components.apple_express.api.AppleExpressApiClient"
REFRESH = f"{AppleExpressCoordinator.__module__}.AppleExpressCoordinator"
UNTRACKED = "90000555"
STORE_KEY = f"{DOMAIN}.contents.%s"


def _config_entry(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


async def _setup(hass, codes: list[str] | None = None) -> MockConfigEntry:
    entry = _config_entry([{CONF_TRACKING_CODE: code} for code in codes or []])
    entry.add_to_hass(hass)
    with patch(
        f"{CLIENT}.async_get_parcel", new=AsyncMock(return_value=active_sample())
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _call(hass, service: str, data: dict) -> None:
    with patch(
        f"{CLIENT}.async_get_parcel",
        new=AsyncMock(side_effect=lambda code: active_sample(code)),
    ):
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
        await hass.async_block_till_done()
        # A refresh requested inside the debouncer's cooldown runs after it.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    parcels = {p["barcode"]: p for p in entry.runtime_data.coordinator.data}
    return parcels[code]["contents"]


async def test_a_parcel_has_no_contents_without_a_stored_value(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_sets_replaces_and_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": " Running  shoes "},
    )
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Winter socks"},
    )
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "  "}
    )
    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_without_text_clears(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert _contents(entry, ACTIVE_CODE) is None


async def test_update_contents_normalizes_the_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: f" {ACTIVE_CODE} ", "contents": "Running shoes"},
    )
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_update_contents_works_for_a_code_that_is_not_tracked(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: UNTRACKED, "contents": "Running shoes"},
    )

    assert entry.runtime_data.coordinator.contents_store.get(UNTRACKED) == (
        "Running shoes"
    )
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_update_contents_never_fetches_or_refreshes(hass):
    await _setup(hass, [ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    fetch = AsyncMock(side_effect=lambda code: active_sample(code))
    with (
        patch(f"{CLIENT}.async_get_parcel", new=fetch),
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{REFRESH}.async_reapply_contents") as reapply,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        assert reapply.call_count == 1
    assert refresh.await_count == 0
    assert fetch.await_count == 0


async def test_update_contents_requests_nothing_when_unchanged(hass):
    await _setup(hass, [ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    await _call(hass, "update_contents", data)
    fetch = AsyncMock(side_effect=lambda code: active_sample(code))
    with (
        patch(f"{CLIENT}.async_get_parcel", new=fetch),
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
        patch(f"{REFRESH}.async_reapply_contents") as reapply,
    ):
        await hass.services.async_call(DOMAIN, "update_contents", data, blocking=True)
        await hass.services.async_call(
            DOMAIN, "update_contents", {CONF_TRACKING_CODE: UNTRACKED}, blocking=True
        )
    assert reapply.call_count == 0
    assert refresh.await_count == 0
    assert fetch.await_count == 0


async def test_reapply_contents_updates_active_and_delivered_without_fetching(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    coordinator = entry.runtime_data.coordinator
    coordinator.delivered = [{"barcode": DELIVERED_CODE, "contents": "stale"}]
    coordinator.contents_store.async_set(ACTIVE_CODE, "Running shoes")
    coordinator.contents_store.async_set(DELIVERED_CODE, "Socks")
    seen = []
    coordinator.async_add_listener(lambda: seen.append(1))
    fetch = AsyncMock()
    with patch(f"{CLIENT}.async_get_parcel", new=fetch):
        coordinator.async_reapply_contents()
        coordinator.contents_store.async_set(DELIVERED_CODE, None)
        coordinator.async_reapply_contents()

    assert _contents(entry, ACTIVE_CODE) == "Running shoes"
    assert coordinator.delivered[0]["contents"] is None
    assert len(seen) == 2
    assert fetch.await_count == 0


async def test_a_newly_tracked_code_still_refreshes_through_the_options_listener(
    hass,
):
    entry = await _setup(hass)
    fetch = AsyncMock(side_effect=lambda code: active_sample(code))
    with patch(f"{CLIENT}.async_get_parcel", new=fetch):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
            blocking=True,
        )
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()
    assert fetch.await_count >= 1
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_update_contents_rejects_an_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ""})


async def test_update_contents_needs_a_loaded_entry(hass):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
        )


async def test_track_parcel_stores_contents_for_a_new_code(hass):
    entry = await _setup(hass)
    await _call(
        hass,
        "track_parcel",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_track_parcel_without_contents_is_unchanged(hass):
    entry = await _setup(hass)
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert _contents(entry, ACTIVE_CODE) is None


async def test_track_parcel_replaces_and_clears_contents_of_a_tracked_code(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    track = {CONF_TRACKING_CODE: ACTIVE_CODE}

    await _call(hass, "track_parcel", {**track, "contents": "Running shoes"})
    assert _contents(entry, ACTIVE_CODE) == "Running shoes"

    await _call(hass, "track_parcel", {**track, "contents": "Winter socks"})
    assert _contents(entry, ACTIVE_CODE) == "Winter socks"

    await _call(hass, "track_parcel", {**track, "contents": ""})
    assert _contents(entry, ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: ACTIVE_CODE}]


async def test_track_parcel_on_a_tracked_code_never_refreshes(hass):
    await _setup(hass, [ACTIVE_CODE])
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"}
    fetch = AsyncMock(side_effect=lambda code: active_sample(code))
    with (
        patch(f"{CLIENT}.async_get_parcel", new=fetch),
        patch(f"{REFRESH}.async_request_refresh", new=AsyncMock()) as refresh,
    ):
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
        await hass.services.async_call(DOMAIN, "track_parcel", data, blocking=True)
        await hass.async_block_till_done()
    assert refresh.await_count == 0
    assert fetch.await_count == 0
    assert _contents(await _entry_of(hass), ACTIVE_CODE) == "Running shoes"


async def _entry_of(hass):
    return hass.config_entries.async_entries(DOMAIN)[0]


async def test_untrack_parcel_drops_the_contents(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.runtime_data.coordinator.contents_store.get(ACTIVE_CODE) is None
    assert entry.options[CONF_PARCELS] == []


async def test_a_stored_value_survives_a_restart_of_the_entry(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    await entry.runtime_data.coordinator.contents_store._store.async_save(
        entry.runtime_data.coordinator.contents_store._data_to_save()
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(
        f"{CLIENT}.async_get_parcel", new=AsyncMock(return_value=active_sample())
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _contents(entry, ACTIVE_CODE) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage):
    entry = await _setup(hass, [ACTIVE_CODE])
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass):
    await _setup(hass, [ACTIVE_CODE])
    events = []
    for name in ("status_changed", "registered", "delivered", "delivery_time_changed"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)

    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Running shoes"},
    )

    assert events == []


async def test_untrack_parcel_works_when_the_entry_is_not_loaded(hass):
    entry = await _setup(hass, [ACTIVE_CODE])
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})

    assert entry.options[CONF_PARCELS] == []
