"""Cloud reachability for the Anthem valve — the one fact MQTT cannot carry.

Grep marker: ``CLOUD CONNECTION WATCH`` (the constants in `const.py`, the wiring in
`coordinator.py`, the entity in `binary_sensor.py`).

The valve can drop off Kohler's cloud, and stay off until it is power-cycled, while still
working from the wall. MQTT cannot report this: every message is published by the valve, so a
disconnect is silence, and the valve is usually silent anyway. (`GCS_SOLO_STS.IoTActive`
is no help, for the same reason.)

Healthy silences of many hours are normal (`docs/protocol/platform.md` §4.2), so no
silence threshold separates an outage from a quiet valve. Silence only decides *when* to
read ``connectionState`` from ``gcs-state`` (`docs/protocol/platform.md` §5); the answer
comes from that field. The exception: a valve message proves reachability, so it marks a
valve reported offline reachable without asking, and a ``Disconnected`` from a read sent
before that message does not undo it.

Two triggers decide when to ask:

* **A — contradiction** (accounts with a controller): the controller reports a zone
  ``ON`` and no valve message arrives within :data:`CLOUD_CHECK_PAIR_WINDOW_SECONDS`.
  ⚠️ Zone ``ON`` only: the controller republishes all-``OFF`` cards with no valve action
  (for example around favorite activity), so a silent valve after one means nothing.
* **B — prolonged quiet** (any account with a valve): no valve message for
  :data:`CLOUD_CHECK_QUIET_SECONDS`, then again on that interval while the quiet lasts.
  It covers controller-free accounts and outages the controller does not reveal.

This is not polling: trigger A is driven by arriving messages, and trigger B fires only
after a full quiet window since the last valve message. ``gcs-state`` reads made for
other reasons also feed the answer, at no cost (`note_rest_payload`).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import (
    CLOUD_CHECK_COOLDOWN_SECONDS,
    CLOUD_CHECK_PAIR_WINDOW_SECONDS,
    CLOUD_CHECK_QUIET_SECONDS,
)
from .konnect import AuthError, KohlerError
from .konnect.const import MSG_HUB_SHOWER_VALVE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .coordinator import KohlerKonnectCoordinator, Valve
    from .konnect.mqtt import Envelope

_LOGGER = logging.getLogger(__name__)

#: ``connectionState`` values, compared case-insensitively. As in the Konnect app, a device
#: is reachable unless it reads ``Disconnected`` (`docs/protocol/platform.md` §5); an
#: unfamiliar value is logged once.
CONNECTED = "connected"
DISCONNECTED = "disconnected"


def _utc_iso(stamp: float | None) -> str | None:
    """Wall-clock seconds to the ISO-8601 Z form the journals and raw capture use."""
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat().replace("+00:00", "Z")


class CloudConnectionWatch:
    """Decides when to ask Kohler whether the valve is still reachable, and remembers.

    One per valve, owned by its :class:`~.coordinator.Valve`. Trigger A is wired only when
    the account also has a controller.

    ⚠️ Nothing here changes valve state: the only network call is a GET of
    ``gcs-state``, whose payload is never applied to :class:`GcsState`
    (see :meth:`_async_check`).
    """

    def __init__(self, coordinator: KohlerKonnectCoordinator, valve: Valve) -> None:
        self._coordinator = coordinator
        self._valve = valve
        self._hass = coordinator.hass

        # Answer state. `_connected` is None until the first successful read: "not asked"
        # and "said no" must not render the same.
        self._connected: bool | None = None
        self._reported: str | None = None
        self._checked_at: float | None = None
        self._trigger: str | None = None
        self._checks = 0
        self._last_error: str | None = None
        # The last unfamiliar `connectionState` logged, so each new one is said once.
        self._unfamiliar: str | None = None
        # Why `_connected` is True although Kohler's last answer said `Disconnected`: a
        # valve message overruled it (`note_gcs_message`, `note_rest_payload`). Cleared by
        # the next answer that is not itself out of date.
        self._overruled_because: str | None = None
        # `lastConnected`, if `gcs-state` carries it.
        self._last_connected_epoch: Any = None

        # Clocks. Monotonic for every interval decision, so a system clock step cannot make
        # the valve look absent or suppress a check.
        self._last_gcs_at: float | None = None
        self._last_check_at: float | None = None

        self._pair_cancel: Any = None
        self._quiet_cancel: Any = None
        self._task: Any = None
        self._stopped = False

    # ------------------------------------------------------------------ #
    # What entities read
    # ------------------------------------------------------------------ #
    @property
    def connected(self) -> bool | None:
        """True/False from the last successful read, None before there has been one.

        A failed read does not move this: "we could not reach Kohler" is not "Kohler cannot
        reach the valve". A valve message after an offline answer sets it True
        (`note_gcs_message`), and a read sent before that message cannot set it False
        again (`note_rest_payload`); `reachable_because` in :attr:`attributes` says so.
        """
        return self._connected

    @property
    def attributes(self) -> dict[str, Any]:
        """Everything needed to judge how much the boolean above is worth."""
        now = time.monotonic()
        quiet_for = (
            None if self._last_gcs_at is None else round(now - self._last_gcs_at, 1)
        )
        return {
            "connection_state": self._reported,
            "last_checked": _utc_iso(self._checked_at),
            "checked_because": self._trigger,
            "checks": self._checks,
            "last_error": self._last_error,
            "cloud_last_connected": self._last_connected_epoch,
            "seconds_since_valve_message": quiet_for,
            "contradiction_watch": bool(self._coordinator.controllers),
            # None while the verdict is Kohler's own; set when a valve message overruled a
            # `Disconnected`, which `connection_state` still shows verbatim.
            "reachable_because": self._overruled_because,
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def async_start(self) -> None:
        """Arm the quiet timer. Idempotent — safe to call again on every reconnect."""
        self._stopped = False
        self._arm_quiet_timer()

    def async_stop(self) -> None:
        """Cancel every pending timer and in-flight read. Called from entry unload."""
        self._stopped = True
        for cancel in (self._pair_cancel, self._quiet_cancel):
            if cancel is not None:
                cancel()
        self._pair_cancel = None
        self._quiet_cancel = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ------------------------------------------------------------------ #
    # Push — fed from the coordinator's envelope handler
    # ------------------------------------------------------------------ #
    def note_gcs_message(self) -> None:
        """A valve message arrived: it is reachable, by definition.

        Settles a pending trigger-A contradiction, and marks a valve reported offline
        reachable again without a read.
        """
        self._last_gcs_at = time.monotonic()
        if self._pair_cancel is not None:
            self._pair_cancel()
            self._pair_cancel = None
        # Armed once, not per message: `_quiet_elapsed` compares against `_last_gcs_at`
        # and re-arms for the remainder, so no timer is rebuilt on every message.
        if self._quiet_cancel is None:
            self._arm_quiet_timer()
        # ⚠️ A valve reported offline is speaking again (typically after a power cycle).
        # Marked reachable here rather than checked: a check is subject to the cooldown,
        # and this message came through Kohler's cloud, which is what reachable means. It
        # cannot be our own outage misread, since a message arrived.
        if self._connected is False and not self._stopped:
            self._connected = True
            self._overruled_because = (
                f"the valve sent a message at {_utc_iso(time.time())} after Kohler's "
                "cloud reported it offline"
            )
            _LOGGER.info(
                "The Anthem valve is talking again after Kohler's cloud reported it "
                "offline; marking it reachable"
            )
            self._coordinator.async_refresh_entities()

    def note_hub_envelope(self, envelope: Envelope) -> None:
        """Trigger A. Only ``SHOWER_VALVE_STS`` with a zone ``ON`` is evidence."""
        if envelope.code != MSG_HUB_SHOWER_VALVE:
            return
        if not any(
            isinstance(item, dict) and item.get("status") == "ON"
            for item in envelope.attributes
        ):
            # An all-OFF card is republished with no valve action: no evidence.
            return
        now = time.monotonic()
        if self._last_gcs_at is not None and now - self._last_gcs_at <= (
            CLOUD_CHECK_PAIR_WINDOW_SECONDS
        ):
            # Already paired by a valve message within the window before it.
            return
        if self._pair_cancel is not None:
            # A check is already pending for an earlier report in the same shower.
            return
        _LOGGER.debug(
            "Controller reports a zone ON with no valve message in %.0fs; watching for %.0fs",
            CLOUD_CHECK_PAIR_WINDOW_SECONDS,
            CLOUD_CHECK_PAIR_WINDOW_SECONDS,
        )
        self._pair_cancel = async_call_later(
            self._hass, CLOUD_CHECK_PAIR_WINDOW_SECONDS, self._pair_window_elapsed
        )

    # ------------------------------------------------------------------ #
    # Timers
    # ------------------------------------------------------------------ #
    #
    # ⚠️ Both timer handlers must carry `@callback`. Without it `async_call_later` runs a
    # plain function in a worker thread (`HassJob` type inference), where
    # `hass.async_create_task` in `_request_check` raises — before `_quiet_elapsed`
    # re-arms, so trigger B would stop for good.
    @callback
    def _pair_window_elapsed(self, _now: Any) -> None:
        """The whole pair window passed with no valve message. Ask the cloud."""
        self._pair_cancel = None
        self._request_check("controller reported a zone ON, valve silent")

    def _arm_quiet_timer(self, seconds: float | None = None) -> None:
        """Start trigger B's countdown, for the full window or the remainder of one."""
        if self._stopped:
            return
        if self._quiet_cancel is not None:
            self._quiet_cancel()
        self._quiet_cancel = async_call_later(
            self._hass,
            CLOUD_CHECK_QUIET_SECONDS if seconds is None else max(seconds, 1.0),
            self._quiet_elapsed,
        )

    @callback
    def _quiet_elapsed(self, _now: Any) -> None:
        """Trigger B. Ask if the valve has been quiet for the full window; else wait it out.

        Valve messages move `_last_gcs_at` without resetting the timer, so it can fire
        early; it then re-arms for the time that remains.
        """
        self._quiet_cancel = None
        # ⚠️ `_last_gcs_at` is None until the valve's first message, which can be hours
        # after setup. Nothing heard since setup counts as a full quiet window; an error
        # here would skip the re-arm and stop trigger B for good.
        if self._last_gcs_at is None:
            self._request_check(
                f"no valve message since setup ({CLOUD_CHECK_QUIET_SECONDS / 3600:.0f}h)"
            )
            self._arm_quiet_timer()
            return
        quiet_for = time.monotonic() - self._last_gcs_at
        if quiet_for >= CLOUD_CHECK_QUIET_SECONDS:
            self._request_check(
                f"no valve message for {CLOUD_CHECK_QUIET_SECONDS / 3600:.0f}h"
            )
            # Re-armed even when the check was skipped or failed: trigger B keeps asking
            # while the valve stays quiet.
            self._arm_quiet_timer()
            return
        self._arm_quiet_timer(CLOUD_CHECK_QUIET_SECONDS - quiet_for)

    # ------------------------------------------------------------------ #
    # The read
    # ------------------------------------------------------------------ #
    def _request_check(self, trigger: str) -> None:
        """Apply the guards, then spawn the read. Never blocks the caller."""
        if self._stopped:
            return

        stream = self._coordinator.stream
        if stream is None or not stream.connected:
            # Our own stream is down, so the silence is ours. The reconnect path reseeds
            # from REST; asking here would report our outage as the valve's.
            _LOGGER.debug("Cloud check skipped (%s): our MQTT stream is down", trigger)
            return

        now = time.monotonic()
        if (
            self._last_check_at is not None
            and now - self._last_check_at < CLOUD_CHECK_COOLDOWN_SECONDS
        ):
            _LOGGER.debug(
                "Cloud check skipped (%s): %.0fs since the last one, cooldown is %.0fs",
                trigger,
                now - self._last_check_at,
                CLOUD_CHECK_COOLDOWN_SECONDS,
            )
            return

        if self._task is not None and not self._task.done():
            # A check is in flight. Return without stamping the cooldown: this call did
            # nothing, and a stamp would let it swallow a later genuine contradiction.
            # Trigger A fires on bursty controller traffic, so overlaps are ordinary.
            return
        self._last_check_at = now
        self._task = self._hass.async_create_task(self._async_check(trigger))

    async def _async_check(self, trigger: str) -> None:
        """One GET of ``gcs-state``, read for ``connectionState`` and nothing else.

        ⚠️ The payload is never applied to :class:`GcsState`: `apply_rest_state` feeds the
        warmup-change machinery, and an unattended reachability check must not be able to
        start a warmup restore.
        """
        device = self._valve.gcs_device
        requested_at = time.monotonic()
        try:
            payload = await self._coordinator.client.async_get_gcs_state(
                device.device_id
            )
        except (AuthError, KohlerError) as err:
            # Not a verdict about the valve: `_connected` keeps its value.
            self._last_error = f"{type(err).__name__}: {err}"
            _LOGGER.debug("Cloud check (%s) could not reach Kohler: %s", trigger, err)
            self._coordinator.async_refresh_entities()
            return

        self.note_rest_payload(payload, trigger, requested_at=requested_at)

    def note_rest_payload(
        self,
        payload: Any,
        trigger: str,
        *,
        notify: bool = True,
        requested_at: float | None = None,
    ) -> None:
        """Take the answer from a ``gcs-state`` payload, whoever fetched it.

        The integration also reads ``gcs-state`` at setup, on every MQTT reconnect, on a
        manual ``update_entity`` and on every warmup write; routing those through here
        gives the sensor a value from startup. They do not touch the cooldown, which governs
        only reads this module starts: a free answer must not suppress a check, since the
        valve can drop off minutes later.

        ``notify`` is False for the setup seed, which pushes its own snapshot afterwards
        and must not notify listeners before the platforms exist. ``requested_at`` is the
        `time.monotonic()` at which the GET was sent, when known: a ``Disconnected`` from a
        read sent before the latest valve message is out of date and overruled. A body that
        is not a JSON object is treated as having no ``connectionState``, never raised on.
        """
        body = payload if isinstance(payload, dict) else {}
        reported = body.get("connectionState")
        self._reported = None if reported is None else str(reported)
        self._last_connected_epoch = body.get("lastConnected")
        self._checked_at = time.time()
        self._trigger = trigger
        self._checks += 1
        self._last_error = None

        if reported is None:
            # The field is always expected, so its absence is reported rather than read as
            # "not connected".
            self._last_error = (
                "gcs-state carried no connectionState field"
                if payload is None or isinstance(payload, dict)
                else f"gcs-state was not a JSON object ({type(payload).__name__})"
            )
            _LOGGER.warning(
                "Kohler gcs-state returned no connectionState; cannot judge reachability"
            )
            if notify:
                self._coordinator.async_refresh_entities()
            return

        value = str(reported).strip().lower()
        if (
            value == DISCONNECTED
            and requested_at is not None
            and (heard_at := self._last_gcs_at) is not None
            and heard_at > requested_at
        ):
            # ⚠️ The valve has spoken since this read was sent, so this `Disconnected` is
            # out of date (`gcs-state` can trail the device). The message is newer evidence
            # and proof of reachability. `connection_state` still shows Kohler's word, and
            # `reachable_because` why the sensor disagrees.
            heard_wall = time.time() - (time.monotonic() - heard_at)
            self._connected = True
            self._overruled_because = (
                f"the valve sent a message at {_utc_iso(heard_wall)}, after this "
                "read was sent, so Kohler's answer was out of date"
            )
            _LOGGER.debug(
                "Cloud check (%s): Kohler reported the valve %s, but it has spoken since "
                "the read was sent; keeping it reachable",
                trigger,
                reported,
            )
            if notify:
                self._coordinator.async_refresh_entities()
            return

        # A current answer from Kohler supersedes any overruling.
        self._overruled_because = None
        was = self._connected
        if value not in (CONNECTED, DISCONNECTED) and value != self._unfamiliar:
            self._unfamiliar = value
            _LOGGER.info(
                "Kohler gcs-state reported an unfamiliar connectionState %r; treating it "
                "as reachable, as the Konnect app does",
                reported,
            )
        self._connected = value != DISCONNECTED
        if self._connected:
            _LOGGER.debug("Cloud check (%s): valve reachable (%s)", trigger, reported)
        elif was is not False:
            # Once per transition, at WARNING: the user cannot see this anywhere else.
            _LOGGER.warning(
                "Kohler's cloud reports the Anthem valve as %s — checked because %s. "
                "This clears on a power cycle of the valve, not from Home Assistant.",
                reported,
                trigger,
            )
        if notify:
            self._coordinator.async_refresh_entities()
