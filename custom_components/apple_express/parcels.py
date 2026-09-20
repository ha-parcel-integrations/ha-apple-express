"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Carrier-specific mapping is kept here; the timestamp parsing, history builder,
sort contract, delivered filter and one-shot warning are suite-wide machinery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-apple-express/issues/new"
    "?template=unrecognised_status.yml"
)

_STATUS_MAP: dict[str, ParcelStatus] = {
    "Delivered": ParcelStatus.DELIVERED,
    "Waiting for Parcel": ParcelStatus.REGISTERED,
    "Pending Delivery Notification Sent": ParcelStatus.REGISTERED,
    "Delivery Notification Sent": ParcelStatus.REGISTERED,
    "Package Picked Up": ParcelStatus.IN_TRANSIT,
    "Received at Local Sort Facility": ParcelStatus.IN_TRANSIT,
    "Parcel Received, Out for Delivery Soon": ParcelStatus.IN_TRANSIT,
    "Out for Delivery": ParcelStatus.OUT_FOR_DELIVERY,
    "Exception - No Answer": ParcelStatus.PROBLEM,
    "Exception - No Suite or Unit Number": ParcelStatus.PROBLEM,
    "Returned for Next Attempt": ParcelStatus.IN_TRANSIT,
    "Second Attempt Planned": ParcelStatus.IN_TRANSIT,
}

# Each distinct carrier status is logged once per Home Assistant session. Status
# labels are needed to extend the map; tracking and location data are redacted
# elsewhere and are never included in this warning.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str, *, source: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Apple Express status — help us map it. Open an issue "
        "and paste this line: %s\n  source=%s status=%r → reported as 'unknown'",
        NEW_ISSUE_URL,
        source,
        code,
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code, source="current")
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, sharing the current-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code, source="history")
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _status_change_timestamp(status_change_text: Any, prefix: str) -> str | None:
    """Extract a local timestamp following an exact Apple Express text prefix."""
    if not isinstance(status_change_text, str) or not status_change_text.startswith(prefix):
        return None
    try:
        return datetime.strptime(
            status_change_text.removeprefix(prefix).strip(), "%B %d, %Y %I:%M %p"
        ).isoformat()
    except ValueError:
        return None


def estimated_delivery_timestamp(status_change_text: Any) -> str | None:
    """Extract Apple Express's point ETA from its display-only status text.

    The API has no structured ETA and does not attach a timezone to this
    sentence.  Keep a successfully parsed value as a naive ISO timestamp,
    matching the carrier's other local timestamps.  Text such as ``To Be
    Determined`` deliberately remains ``None``.
    """
    return _status_change_timestamp(status_change_text, "Estimated Delivery:")


def delivered_timestamp(status_change_text: Any) -> str | None:
    """Extract the delivered-at time from Apple Express's display status."""
    return _status_change_timestamp(status_change_text, "Delivered ")


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is the carrier's own text, or
    its event code when the API has no human-readable text. Sorted oldest →
    newest and capped to the most recent ``max_events``.

    Apple Express supplies local ISO timestamps without a consistent offset.
    Preserve the carrier's ``generatedAt`` value verbatim rather than inventing
    a timezone.
    """
    # The carrier returns its events newest first, so reversing yields the
    # suite's canonical oldest-to-newest order.
    history: list[dict] = []
    for event in reversed(events or []):
        if not isinstance(event, dict):
            continue
        entry = {
            "timestamp": to_iso_timestamp(event.get("generatedAt")),
            "status": map_event_status(event.get("status")),
            # statusDescriptionOverride carries the carrier's own sentence on
            # exception events and is null everywhere else.
            "raw_status": event.get("statusDescriptionOverride") or event.get("status"),
        }
        history.append(entry)
    return history[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=quote(tracking_code, safe=""))


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    Apple Express supplies its ETA as display text without a timezone. A
    recognised value is the end of its six-hour delivery window; other
    timestamp semantics remain unconfirmed.
    """
    order = raw.get("orderDetails") if isinstance(raw.get("orderDetails"), dict) else {}
    tracking_code = order.get("appleTrackingNumber")
    raw_status = order.get("currentStatus")
    status = map_parcel_status(raw_status)
    delivered = status is ParcelStatus.DELIVERED
    delivered_at = (
        delivered_timestamp(order.get("statusChangeText")) if delivered else None
    )
    planned_from = (
        None
        if delivered
        else estimated_delivery_timestamp(order.get("statusChangeText"))
    )
    planned_to = planned_from
    planned_from = (
        (datetime.fromisoformat(planned_to) - timedelta(hours=6)).isoformat()
        if planned_to
        else None
    )

    return {
        "carrier": "Apple Express",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": False,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(raw.get("statusDetails")) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
