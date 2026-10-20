"""Keep Home Assistant's Ring push events alive.

ring-doorbell 0.9.14 (`RingEventListener._get_ring_event`) reads
`event["ding"]["id"]` unguarded. Since mid-2026 Ring sends some FCM pushes
(e.g. `com.ring.pn.live-event.motion`) without that id. Each one raises inside the
firebase_messaging callback, and after 3 in a row the push receiver shuts down for
every Ring device until Home Assistant restarts.
Upstream: python-ring-doorbell issue #537, PRs #540 / #556.

This integration does two things:

1. Parser patch. A missing `ding.id` gets a deterministic synthetic id (hash of
   device id + created_at + subtype), so repeated deliveries of one event are
   still flagged `is_update`. A missing `created_at` falls back to
   `eventito.timestamp`. Any other parse error is logged and the message skipped,
   so one odd payload can't kill the receiver. If the installed library no
   longer has the unguarded lookup, nothing is patched and a log line says this
   integration can be removed.
2. Receiver watchdog. Every 5 minutes, if a Ring entry's push receiver has
   stopped (this bug, repeated network errors) while HA still expects it to run,
   the Ring config entry is reloaded, at most once per 30 min per entry.

Enable with `ring_push_fix:` in configuration.yaml.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import inspect
import json
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ring_push_fix"
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

CHECK_INTERVAL = timedelta(minutes=5)
RELOAD_COOLDOWN = 1800  # seconds between watchdog reloads of one Ring entry
_UNGUARDED = 'event["ding"]["id"]'
_PATCH_FLAG = "_ring_push_fix_original"


def synthetic_id(device_id: Any, created_at: Any, subtype: Any) -> int:
    """Stable positive int (fits in 63 bits) for an event that came without an id."""
    digest = hashlib.sha1(f"{device_id}|{created_at}|{subtype}".encode()).hexdigest()
    return int(digest[:15], 16)


def repair_msg_data(msg_data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (msg_data, changed). Fills ding.id / ding.created_at when missing."""
    data_str = msg_data.get("data")
    if not isinstance(data_str, str):
        return msg_data, False
    data = json.loads(data_str)
    event = data.get("event") or {}
    ding = event.get("ding")
    if not isinstance(ding, dict):
        return msg_data, False
    changed = False
    if not ding.get("created_at"):
        ts = (event.get("eventito") or {}).get("timestamp")
        if isinstance(ts, int | float):
            ts = ts / 1000 if ts > 1e11 else ts  # ms or s
            # Ring's own format; ring_doorbell.util.parse_datetime rejects "+00:00".
            ding["created_at"] = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            changed = True
    if ding.get("id") is None:
        device_id = (data.get("device") or {}).get("id")
        ding["id"] = synthetic_id(device_id, ding.get("created_at"), ding.get("subtype"))
        changed = True
    if not changed:
        return msg_data, False
    return {**msg_data, "data": json.dumps(data)}, True


def apply_patch() -> str:
    """Patch RingEventListener._get_ring_event. Returns 'applied', 'already', or 'not_needed'."""
    from ring_doorbell.listen.eventlistener import RingEventListener  # noqa: PLC0415

    current = RingEventListener._get_ring_event
    if hasattr(current, _PATCH_FLAG):
        return "already"
    try:
        source = inspect.getsource(current)
    except (OSError, TypeError):
        source = _UNGUARDED  # can't inspect: patching is harmless, do it
    if _UNGUARDED not in source:
        return "not_needed"

    original = current

    def _get_ring_event(self, msg_data: dict) -> Any:
        try:
            fixed, changed = repair_msg_data(msg_data)
            if changed:
                _LOGGER.debug("Filled missing ding.id/created_at in a Ring push")
            return original(self, fixed)
        except Exception:  # noqa: BLE001 - one bad payload must not stop the receiver
            category = None
            try:
                category = json.loads(msg_data.get("android_config") or "{}").get("category")
            except (ValueError, TypeError, AttributeError):
                pass
            _LOGGER.warning("Skipped a Ring push it couldn't parse (category=%s)", category, exc_info=True)
            return None

    setattr(_get_ring_event, _PATCH_FLAG, original)
    RingEventListener._get_ring_event = _get_ring_event
    return "applied"


def _receiver_dead(entry) -> bool:
    """True if the entry's push receiver has terminated while HA still wants it."""
    from firebase_messaging.fcmpushclient import FcmPushClientRunState  # noqa: PLC0415

    data = getattr(entry, "runtime_data", None)
    listener = getattr(getattr(data, "listen_coordinator", None), "event_listener", None)
    receiver = getattr(listener, "_receiver", None)
    if listener is None or receiver is None or not getattr(listener, "started", False):
        return False  # not started (no event entities in use) or a different library layout
    return receiver.run_state in (FcmPushClientRunState.STOPPING, FcmPushClientRunState.STOPPED)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Patch the parser and start the receiver watchdog."""
    try:
        result = await hass.async_add_executor_job(apply_patch)  # getsource reads files
    except ImportError:
        _LOGGER.error("ring_push_fix: ring-doorbell isn't installed; set up the Ring integration first")
        return True
    if result == "not_needed":
        _LOGGER.warning(
            "ring_push_fix: the installed ring-doorbell already handles events without ding.id; "
            "the parser patch was not applied and this integration can probably be removed"
        )
    else:
        _LOGGER.info("ring_push_fix: parser patch %s", result)

    last_reload: dict[str, float] = {}

    async def _check(_now=None) -> None:
        for entry in hass.config_entries.async_entries("ring"):
            if entry.state is not ConfigEntryState.LOADED or not _receiver_dead(entry):
                continue
            if time.monotonic() - last_reload.get(entry.entry_id, -RELOAD_COOLDOWN) < RELOAD_COOLDOWN:
                continue
            last_reload[entry.entry_id] = time.monotonic()
            _LOGGER.warning("ring_push_fix: Ring push receiver for %s had stopped; reloading Ring", entry.title)
            hass.async_create_task(hass.config_entries.async_reload(entry.entry_id), "ring_push_fix reload")

    @callback
    def _start(_event=None) -> None:
        hass.async_create_task(_check(), "ring_push_fix first check")
        async_track_time_interval(hass, _check, CHECK_INTERVAL, cancel_on_shutdown=True)

    if hass.state is CoreState.running:
        _start()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start)
    return True
