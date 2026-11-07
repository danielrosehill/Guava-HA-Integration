"""Config flow: one entry per person, using a paste-back OAuth step.

Home Assistant's OAuth2 helper can't be used: Guava requires PAR, and the app's redirect
URI is a fixed localhost address that nothing here listens on. The browser lands on a
page that fails to load, and the user pastes that address back.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import tag_links, token_store
from .api import (
    GuavaClient,
    GuavaError,
    PendingAuth,
    begin_authorization,
    environment_for,
    finish_authorization,
)
from .const import (
    CONF_CALLBACK_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_COOLDOWN,
    CONF_MEDICATIONS,
    CONF_REDIRECT_URI,
    CONF_TOKEN,
    DEFAULT_COOLDOWN,
    DEFAULT_REDIRECT_URI,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _medication_field(names: list[str]) -> SelectSelector:
    # Values keep the stored spelling; labels hide trailing spaces.
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=n, label=n.strip()) for n in names],
            multiple=True,
        )
    )


def _cooldown_field() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=3600,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


class GuavaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Guava authorisation for one person."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._pending: PendingAuth | None = None
        self._token: dict[str, Any] = {}
        self._title = ""
        self._medications: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> GuavaOptionsFlow:
        return GuavaOptionsFlow()

    async def _begin(self) -> None:
        self._pending = await begin_authorization(
            async_get_clientsession(self.hass),
            self._credentials[CONF_CLIENT_ID],
            self._credentials[CONF_CLIENT_SECRET],
            self._credentials[CONF_REDIRECT_URI],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the app's key. A second person reuses the first entry's key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._credentials = {
                key: value.strip() for key, value in user_input.items()
            }
            try:
                await self._begin()
            except GuavaError as err:
                _LOGGER.warning("Guava rejected the authorisation request: %s", err)
                errors["base"] = "par_failed"
            else:
                return await self.async_step_authorize()

        existing = self._async_current_entries()
        suggested = dict(existing[0].data) if existing else {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CLIENT_ID, default=suggested.get(CONF_CLIENT_ID, vol.UNDEFINED)
                ): str,
                vol.Required(
                    CONF_CLIENT_SECRET,
                    default=suggested.get(CONF_CLIENT_SECRET, vol.UNDEFINED),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(
                    CONF_REDIRECT_URI,
                    default=suggested.get(CONF_REDIRECT_URI, DEFAULT_REDIRECT_URI),
                ): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the consent link and take the address the browser landed on."""
        errors: dict[str, str] = {}
        assert self._pending is not None
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                self._token = await finish_authorization(
                    session,
                    self._credentials[CONF_CLIENT_ID],
                    self._credentials[CONF_CLIENT_SECRET],
                    self._pending,
                    user_input[CONF_CALLBACK_URL],
                )
                return await self._after_token(session)
            except GuavaError as err:
                _LOGGER.warning("Guava authorisation failed: %s", err)
                errors["base"] = "authorization_failed"
                # Codes and request URIs are single use: offer a fresh link.
                try:
                    await self._begin()
                except GuavaError:
                    return self.async_abort(reason="cannot_connect")

        return self.async_show_form(
            step_id="authorize",
            data_schema=vol.Schema({vol.Required(CONF_CALLBACK_URL): str}),
            description_placeholders={"url": self._pending.url},
            errors=errors,
        )

    async def _after_token(self, session: Any) -> ConfigFlowResult:
        async def keep(token: dict[str, Any]) -> None:
            self._token = token

        client = GuavaClient(
            session,
            self._credentials[CONF_CLIENT_ID],
            self._credentials[CONF_CLIENT_SECRET],
            self._token,
            keep,
        )
        me = await client.me()
        profile = me.get("profile") or {}
        person = profile.get("id") or self._token.get("patient")
        if not person:
            raise GuavaError("Guava did not say which profile this grant is for")
        environment = environment_for(self._credentials[CONF_CLIENT_ID])
        unique_id = f"{environment}:{person}"
        self._title = " ".join(
            filter(None, [profile.get("given_name"), profile.get("family_name")])
        ) or str(person)
        if environment == "test":
            self._title += " (sandbox)"

        await self.async_set_unique_id(unique_id)
        if self.source == "reauth":
            self._abort_if_unique_id_mismatch(reason="wrong_profile")
            entry = self._get_reauth_entry()
            # The stored token is the dead one: entry.data is the fresh source after this.
            await token_store(self.hass, entry.entry_id).async_remove()
            return self.async_update_reload_and_abort(
                entry, data={**entry.data, CONF_TOKEN: self._token}
            )
        self._abort_if_unique_id_configured()

        self._medications = await client.active_medications()
        return await self.async_step_medications()

    async def async_step_medications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which medications get a button."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data={**self._credentials, CONF_TOKEN: self._token},
                options={
                    CONF_MEDICATIONS: user_input.get(CONF_MEDICATIONS, []),
                    CONF_COOLDOWN: int(user_input[CONF_COOLDOWN]),
                },
            )
        schema = vol.Schema(
            {
                vol.Optional(CONF_MEDICATIONS, default=[]): _medication_field(
                    self._medications
                ),
                vol.Required(CONF_COOLDOWN, default=DEFAULT_COOLDOWN): _cooldown_field(),
            }
        )
        return self.async_show_form(
            step_id="medications",
            data_schema=schema,
            description_placeholders={"person": self._title},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start again for the same person, with the key already held."""
        self._credentials = {
            key: entry_data[key]
            for key in (CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_REDIRECT_URI)
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the user: a consent link made now could expire before it is opened."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
                description_placeholders={"person": self._get_reauth_entry().title},
            )
        try:
            await self._begin()
        except GuavaError:
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_authorize()


class GuavaOptionsFlow(OptionsFlow):
    """Change the buttons and the cooldown, or show the links for Guava Tags."""

    async def _medication_names(self) -> list[str]:
        """Return the chosen medications and the active ones, as fresh as Guava allows."""
        profile = self.config_entry.runtime_data
        try:
            await profile.refresh_known()
        except GuavaError as err:
            _LOGGER.warning("Could not refresh the medication list: %s", err)
        chosen = self.config_entry.options.get(CONF_MEDICATIONS, [])
        return list(dict.fromkeys([*chosen, *profile.known_medications]))

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["buttons", "tag_links"])

    async def async_step_buttons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self.config_entry.options
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_MEDICATIONS: user_input.get(CONF_MEDICATIONS, []),
                    CONF_COOLDOWN: int(user_input[CONF_COOLDOWN]),
                }
            )
        chosen = options.get(CONF_MEDICATIONS, [])
        schema = vol.Schema(
            {
                vol.Optional(CONF_MEDICATIONS, default=chosen): _medication_field(
                    await self._medication_names()
                ),
                vol.Required(
                    CONF_COOLDOWN, default=options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
                ): _cooldown_field(),
            }
        )
        return self.async_show_form(step_id="buttons", data_schema=schema)

    async def async_step_tag_links(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the links. Nothing to change, so submitting leaves the options as they are."""
        if user_input is not None:
            return self.async_create_entry(data=dict(self.config_entry.options))
        links = await tag_links(
            self.hass, self.config_entry.entry_id, await self._medication_names()
        )
        return self.async_show_form(
            step_id="tag_links",
            data_schema=vol.Schema({}),
            description_placeholders={
                "person": self.config_entry.title,
                "links": "\n".join(f"- {name}: `{url}`" for name, url in links.items())
                or "No active medications.",
            },
        )
