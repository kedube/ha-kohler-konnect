"""Config flow for Kohler Konnect.

One entry per Kohler account, covering every supported device on it. Two steps:

1. ``user`` — email and password; sign-in runs server-side, with no browser round trip.
2. ``valve`` — the valve model, which sets how many outlets each zone has. Skipped when the
   split is detected (the valve's ``gcsadvancestate``, else a controller's configuration)
   and on a faucet-only account. A controller-only account still needs it: the
   controller's per-zone outlet arrays use the same split.

Only the refresh token is stored, never the password. When it expires, reauth asks for the
password again and replaces only the credential (and the email, if it changed).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_MAX_RUN_MINUTES,
    CONF_REFRESH_TOKEN,
    CONF_SIDEBAR_PANEL,
    CONF_TEMPERATURE_UNIT,
    CONF_TENANT_ID,
    CONF_VALVE_MODEL,
    CONF_WATER_UNITS,
    CONF_ZONE_GROUPING,
    CONF_ZONE_OUTLETS,
    DEFAULT_MAX_RUN_MINUTES,
    DEFAULT_SIDEBAR_PANEL,
    DEFAULT_ZONE_GROUPING,
    DOMAIN,
    ZONE_GROUPING_NUMBERED,
    ZONE_GROUPING_OUTLET_LABELS,
    ZONE_GROUPING_SUBDEVICES,
)
from .konnect import (
    AuthError,
    InvalidCredentials,
    KohlerAuth,
    KohlerClient,
    KohlerError,
    SignInBlocked,
    describe_topology,
    model_for_topology,
    topology_from_hub_configuration,
    topology_from_valve_settings,
)
from .konnect.models import (
    DEFAULT_VALVE_MODEL,
    VALVE_MODELS,
    get_valve_model,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


def _valve_schema() -> vol.Schema:
    """Dropdown of valve models, labelled by outlet count."""
    options = [
        SelectOptionDict(
            value=model.sku,
            label=f"{model.sku} — {model.total_outlets} outlet"
            f"{'s' if model.total_outlets != 1 else ''}",
        )
        for model in sorted(VALVE_MODELS.values(), key=lambda m: m.total_outlets)
    ]
    return vol.Schema(
        {
            vol.Required(CONF_VALVE_MODEL, default=DEFAULT_VALVE_MODEL): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        }
    )


MAX_RUN_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0, max=120, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min"
    )
)


def _options_schema(
    options: dict[str, Any], *, showers: bool, faucets: bool
) -> vol.Schema:
    """The options that apply to the devices on this account.

    * Multi-zone grouping, for valves and controllers (labels in `strings.json`,
      `selector.zone_grouping`).
    * The water safety limit, for faucets.
    * The sidebar panel, for every account.
    """
    fields: dict[Any, Any] = {}
    if showers:
        fields[
            vol.Required(
                CONF_ZONE_GROUPING,
                default=options.get(CONF_ZONE_GROUPING, DEFAULT_ZONE_GROUPING),
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    ZONE_GROUPING_SUBDEVICES,
                    ZONE_GROUPING_OUTLET_LABELS,
                    ZONE_GROUPING_NUMBERED,
                ],
                mode=SelectSelectorMode.LIST,
                translation_key=CONF_ZONE_GROUPING,
            )
        )
    if faucets:
        fields[
            vol.Required(
                CONF_MAX_RUN_MINUTES,
                default=options.get(CONF_MAX_RUN_MINUTES, DEFAULT_MAX_RUN_MINUTES),
            )
        ] = MAX_RUN_SELECTOR
    fields[
        vol.Required(
            CONF_SIDEBAR_PANEL,
            default=options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL),
        )
    ] = BooleanSelector()
    return vol.Schema(fields)


def options_schema(hass: HomeAssistant, entry: ConfigEntry) -> vol.Schema:
    """The options form for one account, filled in as the account is set.

    Both kinds' options are offered until the entry has loaded; an unused one is harmless.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    showers = coordinator is None or bool(coordinator.valves or coordinator.controllers)
    faucets = coordinator is None or bool(coordinator.faucets)
    return _options_schema(dict(entry.options), showers=showers, faucets=faucets)


def _stored(key: str, value: Any) -> Any:
    """A form value as the entry stores it: the safety limit in whole minutes."""
    return int(value) if key == CONF_MAX_RUN_MINUTES else value


def option_values(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Every option the form offers this account, as set or by default."""
    return {
        key: _stored(key, value)
        for key, value in options_schema(hass, entry)({}).items()
    }


def validate_options(
    hass: HomeAssistant, entry: ConfigEntry, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Some of an account's options, checked as its options form checks them.

    Raises `vol.Invalid` for a value the form refuses, or an option it doesn't offer this
    account. Returns only the options given: one not given, written out as its default,
    would count as a change and reload the entry.
    """
    checked = options_schema(hass, entry)(dict(changes))
    return {key: checked[key] for key in changes}


def merge_options(entry: ConfigEntry, changes: Mapping[str, Any]) -> dict[str, Any]:
    """The entry's options with ``changes`` applied, as the entry stores them.

    Merged, not replaced: `entry.options` also holds per-valve Warmup Auto-Restore and
    report-log keys.
    """
    return {
        **entry.options,
        **{key: _stored(key, value) for key, value in changes.items()},
    }


def _entry_title(username: str) -> str:
    """The title setup gives an entry, which a changed email carries over to."""
    return f"Kohler Konnect ({username})"


class KohlerKonnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Kohler Konnect config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> KohlerKonnectOptionsFlow:
        """Return the options flow for this handler."""
        return KohlerKonnectOptionsFlow()

    # `_kohler_` prefix: `ConfigFlow` defines read-only properties (e.g. `_reauth_entry_id`),
    # and assigning to one raises AttributeError, seen only as a 500 from the flow endpoint.
    def __init__(self) -> None:
        self._kohler_data: dict[str, Any] = {}
        # Kept so the entry stores the current refresh token; see `async_step_valve`.
        self._kohler_auth: KohlerAuth | None = None
        self._kohler_summary: str = ""
        self._kohler_topology: tuple[int, int] | None = None
        # False on a faucet-only account, which needs no valve model.
        self._kohler_needs_model: bool = True

    # ------------------------------------------------------------------ #
    # Step 1: credentials
    # ------------------------------------------------------------------ #
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_sign_in_errors(
                self._async_sign_in(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            )
            if not errors:
                if not self._kohler_data.get("has_device"):
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(
                        user_input[CONF_USERNAME].strip().lower()
                    )
                    self._abort_if_unique_id_configured()
                    return await self.async_step_valve()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    async def _async_sign_in_errors(sign_in: Awaitable[Any]) -> dict[str, str]:
        """Await a sign-in; return the form errors its failure means, `{}` on success."""
        try:
            await sign_in
        except InvalidCredentials:
            return {"base": "invalid_auth"}
        except SignInBlocked as err:
            _LOGGER.error("Kohler sign-in blocked: %s", err)
            return {"base": "signin_blocked"}
        except (AuthError, KohlerError) as err:
            _LOGGER.error("Kohler sign-in failed: %s", err)
            return {"base": "cannot_connect"}
        return {}

    async def _async_sign_in(self, username: str, password: str) -> None:
        """Sign in, then read the account to see what hardware is on it."""
        session = async_get_clientsession(self.hass)
        auth = KohlerAuth(session)
        tokens = await auth.async_sign_in(username, password)
        self._kohler_auth = auth
        client = KohlerClient(session, auth)
        customer = await client.async_get_customer()

        self._kohler_summary = customer.describe()
        _LOGGER.debug("Kohler account: %s", self._kohler_summary)
        self._kohler_needs_model = customer.has_gcs or customer.has_hub
        self._kohler_topology = (
            await self._async_detect_topology(client, customer)
            if self._kohler_needs_model
            else None
        )
        self._kohler_data = {
            CONF_USERNAME: username,
            CONF_REFRESH_TOKEN: tokens.refresh_token,
            CONF_TENANT_ID: tokens.tenant_id,
            CONF_TEMPERATURE_UNIT: customer.temperature_unit,
            CONF_WATER_UNITS: customer.water_units,
            "has_device": bool(customer.supported_devices),
        }

    async def _async_detect_topology(self, client, customer) -> tuple[int, int] | None:
        """Detect the outlet split from the first valve, else controller, that answers.

        This only sets the entry's model, a fallback: at setup each valve and controller
        reads its own layout.
        """
        for valve in customer.gcs_devices:
            try:
                setting = await client.async_get_gcs_settings(valve.device_id)
                detected = topology_from_valve_settings(setting)
                if detected:
                    _LOGGER.debug(
                        "Topology from valve %s: %s", valve.device_id, detected
                    )
                    return detected
            except (AuthError, KohlerError) as err:
                _LOGGER.debug(
                    "Could not read valve settings for %s: %s", valve.device_id, err
                )

        for controller in customer.hub_devices:
            try:
                config = await client.async_get_hub_configuration(controller.device_id)
                detected = topology_from_hub_configuration(
                    config.get("configuration") or {}
                )
                if detected:
                    _LOGGER.debug(
                        "Topology from controller %s: %s",
                        controller.device_id,
                        detected,
                    )
                    return detected
            except (AuthError, KohlerError) as err:
                _LOGGER.debug(
                    "Could not read controller configuration for %s: %s",
                    controller.device_id,
                    err,
                )

        _LOGGER.debug("Outlet topology could not be detected; asking the user")
        return None

    # ------------------------------------------------------------------ #
    # Step 2: valve model
    # ------------------------------------------------------------------ #
    async def async_step_valve(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None and not self._kohler_needs_model:
            # Faucets only: no valve layout to detect or ask for.
            user_input = {}
        if user_input is not None:
            data = {k: v for k, v in self._kohler_data.items() if k != "has_device"}
            # ⚠️ Store the current token, not the one sign-in returned: any refresh since
            # (on a 401) rotated it, retiring the original.
            if (auth := self._kohler_auth) is not None:
                data[CONF_REFRESH_TOKEN] = auth.refresh_token
                if auth.tenant_id:
                    data[CONF_TENANT_ID] = auth.tenant_id
            if CONF_VALVE_MODEL in user_input:
                data[CONF_VALVE_MODEL] = user_input[CONF_VALVE_MODEL]
                chosen = get_valve_model(user_input[CONF_VALVE_MODEL])
                data[CONF_ZONE_OUTLETS] = [
                    chosen.outlets_valve1,
                    chosen.outlets_valve2,
                ]

            return self.async_create_entry(
                title=_entry_title(data[CONF_USERNAME]), data=data
            )

        # Detected a catalogue model: skip the question.
        detected = self._kohler_topology
        if detected is not None:
            model = model_for_topology(*detected)
            if model.sku in VALVE_MODELS:
                _LOGGER.info(
                    "Detected %s — %s; no valve model needed",
                    model.sku,
                    describe_topology(detected),
                )
                return await self.async_step_valve({CONF_VALVE_MODEL: model.sku})

        return self.async_show_form(
            step_id="valve",
            data_schema=_valve_schema(),
            description_placeholders={"summary": self._kohler_summary},
        )

    # ------------------------------------------------------------------ #
    # Reauth
    # ------------------------------------------------------------------ #
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """The stored refresh token expired or was revoked; ask for the password again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again as the entry's own account, and replace only its credential.

        ⚠️ Self-contained on purpose: it must not route through `async_step_user`, which
        would accept any account and re-run valve detection. The email field is prefilled
        but editable, since a Kohler account's email can change;
        `_async_finish_reauth` decides which account is accepted.
        """
        entry = self._get_reauth_entry()
        username = entry.data.get(CONF_USERNAME) or ""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Re-shown as typed if this fails.
            username = user_input[CONF_USERNAME]
            auth = KohlerAuth(async_get_clientsession(self.hass))
            errors = await self._async_sign_in_errors(
                auth.async_sign_in(username, user_input[CONF_PASSWORD])
            )
            if not errors:
                return await self._async_finish_reauth(entry, username, auth)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=username): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"username": entry.data.get(CONF_USERNAME) or ""},
        )

    async def _async_finish_reauth(
        self, entry: ConfigEntry, username: str, auth: KohlerAuth
    ) -> ConfigFlowResult:
        """Store a sign-in that is the entry's own account, and reload; abort on any other.

        The entry's own account is the same login (the normalised email is the unique id)
        or the same Kohler account (same tenant id). ⚠️ Any other account is refused, so an
        entry can never be bound to an unrelated account. For the same account under a new
        email, the email and unique id move together, unless another entry already has it.
        """
        unique_id = username.strip().lower()
        same_login = unique_id == entry.unique_id
        same_account = bool(auth.tenant_id) and auth.tenant_id == entry.data.get(
            CONF_TENANT_ID
        )
        if not (same_login or same_account):
            return self.async_abort(reason="unique_id_mismatch")
        await self.async_set_unique_id(unique_id)
        if not same_login:
            if (
                self.hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, unique_id
                )
                is not None
            ):
                return self.async_abort(reason="already_configured")
            _LOGGER.info(
                "Signed in again as the same Kohler account under another email; the "
                "entry now uses that email"
            )
        data: dict[str, Any] = {
            **entry.data,
            CONF_USERNAME: username,
            CONF_REFRESH_TOKEN: auth.refresh_token,
        }
        if auth.tenant_id:
            data[CONF_TENANT_ID] = auth.tenant_id
        # The title follows a changed email only if the owner hasn't renamed the entry.
        title = entry.title
        previous = entry.data.get(CONF_USERNAME)
        if previous and username != previous and title == _entry_title(previous):
            title = _entry_title(username)
        self.hass.config_entries.async_update_entry(
            entry, data=data, unique_id=unique_id, title=title
        )
        # ⚠️ Explicit reload required: `_async_update_listener` ignores the refresh token,
        # so otherwise `KohlerAuth` keeps the dead one. Scheduled, not awaited, so the
        # dialog doesn't wait on setup; the reload aborts this flow mid-step, which Home
        # Assistant accepts. Not `async_update_reload_and_abort`: from 2026.10 it reports
        # entries with an update listener, and stops working for them in 2026.12.
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")


class KohlerKonnectOptionsFlow(OptionsFlow):
    """Options for Kohler Konnect: zone grouping, the faucet safety limit, the panel."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options that apply to the devices on this account."""
        if user_input is not None:
            return self.async_create_entry(
                title="", data=merge_options(self.config_entry, user_input)
            )
        return self.async_show_form(
            step_id="init", data_schema=options_schema(self.hass, self.config_entry)
        )
