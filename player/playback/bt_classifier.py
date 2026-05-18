"""Pure-function helpers for attributing GStreamer errors to Bluetooth sinks.

Lives in its own module so it can be imported and unit-tested without
needing GStreamer or any pipeline state.
"""

from __future__ import annotations

from typing import Optional

_BT_SPECIFIC_KEYWORDS = ("bluez", "bluetooth", "a2dp", "avdtp")


def classify_bt_error(
    err_msg: str,
    configured_bt_sinks: list[str],
) -> Optional[list[str]]:
    """Return the list of BT sinks the error should be attributed to, or None.

    The classification rules are:
      * If no BT sinks are configured, return None (never attribute to BT).
      * If the error message contains an explicit BT keyword (bluez/
        bluetooth/a2dp/avdtp), return all configured BT sinks.
      * Otherwise, only attribute the error to BT if a configured BT sink
        name appears verbatim in the message — this avoids false positives
        for generic pulse errors like "connection terminated" or
        "pa_context" which can fire for any sink.
    """
    if not configured_bt_sinks:
        return None
    err_lower = err_msg.lower()
    if any(kw in err_lower for kw in _BT_SPECIFIC_KEYWORDS):
        return list(configured_bt_sinks)
    matched = [name for name in configured_bt_sinks if name.lower() in err_lower]
    return matched or None
