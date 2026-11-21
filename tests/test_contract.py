"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util
import pytest

from custom_components.apple_express.const import ParcelStatus
from custom_components.apple_express.parcels import (
    is_pickup,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import active_sample, delivered_sample

ZONE = ZoneInfo("America/Toronto")

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


@pytest.fixture(autouse=True)
def _zone():
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(ZONE)
    yield
    dt_util.set_default_time_zone(previous)


def _with_status(status: str, text: str) -> dict:
    sample = active_sample()
    sample["orderDetails"]["currentStatus"] = status
    sample["orderDetails"]["statusChangeText"] = text
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "out_for_delivery": lambda: _with_status(
        "Out for Delivery", "Estimated Delivery: September 20, 2099 9:00 PM"
    ),
    "delivered_with_estimate": lambda: _with_status(
        "Delivered", "Estimated Delivery: September 20, 2099 9:00 PM"
    ),
    "problem": lambda: _with_status(
        "Exception - No Answer", "Estimated Delivery: September 20, 2099 9:00 PM"
    ),
    "unmapped": lambda: _with_status("Something New", "Estimated Delivery: Soon"),
    "bare": dict,
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_constant_false_without_a_point(parcel):
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_delivered_parcel_has_no_window_and_no_delivery_time_without_its_text():
    parcel = normalize_parcel(SAMPLES["delivered_with_estimate"]())
    assert parcel["delivered"] is True
    assert parcel["planned_from"] is None
    assert parcel["delivered_at"] is None


def test_the_estimate_is_a_six_hour_window_in_the_home_zone():
    parcel = normalize_parcel(active_sample())
    assert parcel["planned_from"] == datetime(2099, 9, 20, 15, tzinfo=ZONE).isoformat()
    assert parcel["planned_to"] == datetime(2099, 9, 20, 21, tzinfo=ZONE).isoformat()


def test_delivered_at_carries_the_home_zone():
    parcel = normalize_parcel(delivered_sample())
    assert (
        parcel["delivered_at"] == datetime(2026, 1, 3, 15, 4, tzinfo=ZONE).isoformat()
    )


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZONE)
    assert start == "2099-04-29T13:00:00Z"
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == datetime(2099, 4, 29).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=ZONE)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()


def test_pickup_is_sticky_once_a_parcel_has_reached_a_point():
    assert is_pickup(None, ParcelStatus.DELIVERED, [ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(True, ParcelStatus.IN_TRANSIT)
    assert is_pickup(None, ParcelStatus.AT_PICKUP_POINT)
    assert not is_pickup(None, ParcelStatus.DELIVERED, [None, ParcelStatus.DELIVERED])


@pytest.mark.parametrize(
    "status",
    [ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED],
)
def test_the_window_is_emptied_for_a_parcel_that_is_done_or_waiting(status):
    start, end = planned_window("2099-04-29T13:00:00", None, zone=ZONE)
    assert planned_for_status(status, start, end) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, start, end) == (start, end)


def test_planned_window_edge_cases():
    assert planned_window(None, None, zone=ZONE) == (None, None)
    assert planned_window("nope", None, zone=ZONE) == (None, None)
    first, last = planned_window("2099-04-29", "2099-04-30", zone=ZONE)
    assert first.startswith("2099-04-29T00:00:00")
    assert last.startswith("2099-04-30T23:59:59")
    reversed_first, _ = planned_window(
        "2099-04-29T14:00:00", "2099-04-29T10:00:00", zone=ZONE
    )
    assert reversed_first.startswith("2099-04-29T10:00:00")
    assert planned_window("2099-04-29T23:59:59", None, zone=ZONE)[1] is None
