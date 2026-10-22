"""The MQTT-health entity's shared behaviour, for every device type."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .coordinator import KohlerKonnectCoordinator


class MqttConnectionMixin:
    """The MQTT-health entity, shared by the valve, controller and faucet devices.

    One stream serves the whole account, so every copy agrees; each device page carries
    one because its entities depend on the stream. Mixed in ahead of the device base so
    these properties win the MRO.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def _account(self) -> KohlerKonnectCoordinator:
        """The account's coordinator, which holds the stream and the sign-in."""
        return self.coordinator

    @property
    def _update_device_ids(self) -> frozenset[str] | None:
        """Every message, whichever device sent it: `last_message_at` is the stream's."""
        return None

    @property
    def is_on(self) -> bool | None:
        stream = self._account.stream
        return None if stream is None else bool(stream.connected)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Stream and credential detail, never token material.

        Credentials are reported apart from the connection state: a dead stream and a
        rejected sign-in need different fixes.
        """
        stream = self._account.stream
        auth = self._account.auth
        expires_at = auth.access_token_expires_at
        attributes: dict[str, object] = {
            "credentials_present": auth.has_credentials,
            "access_token_expires_at": (
                datetime.fromtimestamp(expires_at, tz=UTC).isoformat()
                if expires_at
                else None
            ),
        }
        if stream is not None:
            attributes["warming_up"] = stream.warming_up
            attributes["last_message_at"] = (
                datetime.fromtimestamp(stream.last_message_at, tz=UTC).isoformat()
                if stream.last_message_at
                else None
            )
        return attributes
