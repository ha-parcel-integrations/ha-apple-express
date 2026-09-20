"""Redacted Apple Express responses shared by the test modules.

The shape follows the carrier's live responses. Identifiers, references,
locations and timestamps are synthetic; no delivery data is kept.
"""
from __future__ import annotations

ACTIVE_CODE = "90000002"
DELIVERED_CODE = "90000001"


def _event(status: str, timestamp: str) -> dict:
    return {
        "status": status,
        "statusDescriptionOverride": None,
        "statusIconId": 1,
        "generatedAt": timestamp,
        "generatedAtTimeZone": "",
        "location": "Example, ON",
    }


def event(status: str, timestamp: str, _description: str = "") -> dict:
    """Build a synthetic event for generic history-helper tests."""
    return _event(status, timestamp)


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A redacted delivered response."""
    return {
        "orderDetails": {
            "isECommerce": True,
            "isNfo": False,
            "reference": "0000000000000",
            "appleTrackingNumber": code,
            "lastUpdated": "2026-01-03T15:04:12",
            "lastUpdatedTimeZone": "",
            "pickupCity": "Example",
            "pickupProvince": "ON",
            "deliveryCity": "Example",
            "deliveryProvince": "ON",
            "statusId": 9,
            "currentStatus": "Delivered",
            "statusIconId": 5,
            "statusChangeText": "Delivered January 3, 2026 3:04 PM",
            "cancelledBy": None,
            "cancellationReason": None,
            "deliveryInstructions": "",
            "deliveryTime": "",
        },
        "statusDetails": [
            _event("Delivered", "2026-01-03T15:04:12"),
            _event("Package Picked Up", "2026-01-03T15:04:12"),
            _event("Waiting for Parcel", "2026-01-02T13:07:48"),
        ],
        "screenDetails": {"progressPercentage": 100},
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A redacted response with an intentionally unmapped current status."""
    return {
        "orderDetails": {
            "reference": "0000000000001",
            "appleTrackingNumber": code,
            "lastUpdated": "2026-09-19T19:46:49",
            "lastUpdatedTimeZone": "",
            "pickupCity": "Example",
            "pickupProvince": "ON",
            "deliveryCity": "Example",
            "deliveryProvince": "ON",
            "statusId": 16,
            "currentStatus": "Received at Local Sort Facility",
            "statusIconId": 2,
            "statusChangeText": "Estimated Delivery: September 20, 2026 9:00 PM",
            "cancelledBy": None,
            "cancellationReason": None,
            "deliveryInstructions": "",
            "deliveryTime": "",
        },
        "statusDetails": [
            _event("Parcel Received, Out for Delivery Soon", "2026-09-19T19:46:49"),
            _event("Waiting for Parcel", "2026-09-18T18:29:37"),
        ],
        "screenDetails": {"progressPercentage": 0},
    }


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """Compatibility helper: Apple Express has no confirmed pickup response."""
    return active_sample(code)


def not_found_sample() -> dict:
    """The carrier's answer for a code it does not know: 200, all sections null."""
    return {"orderDetails": None, "statusDetails": None, "screenDetails": None}
