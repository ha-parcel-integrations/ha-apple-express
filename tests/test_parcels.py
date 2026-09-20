"""Tests for the Apple Express response mapping."""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.apple_express.const import (
    CAPABILITIES,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.apple_express.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_sample,
    delivered_sample,
    pickup_sample,
)


def test_confirmed_statuses_are_mapped():
    assert map_parcel_status("Delivered") is ParcelStatus.DELIVERED
    assert map_parcel_status("Waiting for Parcel") is ParcelStatus.REGISTERED
    assert map_parcel_status("Received at Local Sort Facility") is ParcelStatus.IN_TRANSIT
    assert map_parcel_status("Out for Delivery") is ParcelStatus.OUT_FOR_DELIVERY
    assert map_parcel_status("Exception - No Answer") is ParcelStatus.PROBLEM
    assert map_event_status("Package Picked Up") is ParcelStatus.IN_TRANSIT


def test_unknown_warning_is_value_free_and_one_shot(caplog):
    map_parcel_status("Private carrier phrase")
    map_parcel_status("Private carrier phrase")
    assert caplog.text.count("Unrecognised Apple Express status") == 1
    assert "Private carrier phrase" in caplog.text
    assert "source=current" in caplog.text


def test_helpers_handle_timestamp_and_dimensions_edges():
    assert parse_iso("2026-01-01T00:00:00Z").tzinfo is not None
    assert parse_iso("not-a-date") is None
    assert to_iso_timestamp(0) == "1970-01-01T00:00:00+00:00"
    assert to_iso_timestamp(10**20) is None
    assert format_dimensions(1, 2, 3)["text"] == "1 x 2 x 3 cm"
    assert format_dimensions(1, None, 3) is None


def test_history_keeps_all_events_oldest_first_with_carrier_timestamps():
    history = build_history(delivered_sample()["statusDetails"])
    assert [item["raw_status"] for item in history] == [
        "Waiting for Parcel",
        "Package Picked Up",
        "Delivered",
    ]
    assert history[0]["timestamp"] == "2026-01-02T13:07:48"
    assert history[-1]["timestamp"] == "2026-01-03T15:04:12"
    assert history[0]["status"] is ParcelStatus.REGISTERED
    assert history[1]["status"] is ParcelStatus.IN_TRANSIT
    assert history[-1]["status"] is ParcelStatus.DELIVERED
    assert build_history(["bad", {}]) == [{"timestamp": None, "status": None, "raw_status": None}]


def test_tracking_link_encodes_the_configured_code():
    assert tracking_url("AB 12/3") == (
        "https://track.appleexpress.com/en-US/orderNumber/AB%2012%2F3"
    )
    assert tracking_url(None) is None


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample."""
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_response_has_canonical_shape():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert parcel["carrier"] == "Apple Express"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"
    assert parcel["delivered"] is True
    assert parcel["url"].endswith(DELIVERED_CODE)
    assert parcel["history"] is not None
    for key in ("sender", "receiver", "delivered_at", "planned_from", "planned_to", "weight", "dimensions"):
        assert parcel[key] is None
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_normalize_active_response_maps_its_status_and_history_is_opt_in():
    parcel = normalize_parcel(active_sample())
    assert parcel["barcode"] == ACTIVE_CODE
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert parcel["delivered"] is False
    assert parcel["history"] is None
    assert CAPABILITIES == frozenset({"url", "history"})


def test_sort_and_delivered_filter_keep_missing_values_last():
    assert [p["barcode"] for p in sort_parcels_by_ts(
        [{"barcode": "b", "planned_from": None}, {"barcode": "a", "planned_from": "2026-01-01T00:00:00Z"}],
        "planned_from",
    )] == ["a", "b"]
    entry = MockConfigEntry(domain=DOMAIN, options={"delivered_filter_type": "parcels", "delivered_filter_amount": 1})
    assert len(apply_delivered_filter([{"delivered_at": "2026-01-01T00:00:00Z"}, {"delivered_at": None}], entry)) == 1
    now_entry = MockConfigEntry(domain=DOMAIN, options={"delivered_filter_type": "days", "delivered_filter_amount": 1})
    assert apply_delivered_filter([{"delivered_at": None}], now_entry) == [{"delivered_at": None}]
