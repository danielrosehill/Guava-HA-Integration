"""Small async client for the Guava Health API. No Home Assistant imports.

API behaviour this relies on is recorded with dates in the Guava-Health-Plugin repo,
docs/api-notes.md.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp

from .const import API_BASE, AUTHORIZE_URL, SCOPES, USER_AGENT

REFRESH_MARGIN = 120
MAX_FHIR_PAGES = 20
TIMEOUT = aiohttp.ClientTimeout(total=30)


class GuavaError(Exception):
    """A Guava request failed."""


class GuavaAuthError(GuavaError):
    """The grant is no longer usable: the user has to authorise again."""


def environment_for(client_id: str) -> str:
    """Test clients only work on the sandbox host (tested 2026-11-03)."""
    return "test" if client_id.startswith("guava_test_") else "live"


def _basic(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def _call(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    authorization: str,
    form: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": authorization,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        async with session.request(
            method, url, data=form, json=body, headers=headers, timeout=TIMEOUT
        ) as resp:
            text = await resp.text()
            status = resp.status
    except (aiohttp.ClientError, TimeoutError) as err:
        raise GuavaError(f"Could not reach Guava: {err}") from err

    if status >= 400:
        if status == 401 or "invalid_grant" in text:
            raise GuavaAuthError(f"HTTP {status}: {text[:200]}")
        raise GuavaError(f"HTTP {status}: {text[:200]}")
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError as err:
        raise GuavaError(f"Unexpected response: {text[:200]}") from err


@dataclass
class PendingAuth:
    """One authorisation in progress. The authorize URL dies after 600 seconds."""

    url: str
    verifier: str
    state: str
    redirect_uri: str


async def begin_authorization(
    session: aiohttp.ClientSession,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> PendingAuth:
    """Push the authorisation request (PAR, PKCE S256) and return the consent URL."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    # PAR needs response_type and client_id in the body even with Basic auth
    # (tested 2026-11-03; neither is in Guava's docs).
    par = await _call(
        session,
        "POST",
        API_BASE[environment_for(client_id)] + "/oauth/par",
        authorization=_basic(client_id, client_secret),
        form={
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": " ".join(SCOPES),
            "state": state,
            "redirect_uri": redirect_uri,
        },
    )
    if "request_uri" not in par:
        raise GuavaError("Guava returned no request_uri")
    query = urlencode({"client_id": client_id, "request_uri": par["request_uri"]})
    return PendingAuth(f"{AUTHORIZE_URL}?{query}", verifier, state, redirect_uri)


async def finish_authorization(
    session: aiohttp.ClientSession,
    client_id: str,
    client_secret: str,
    pending: PendingAuth,
    returned_url: str,
) -> dict[str, Any]:
    """Exchange the address the browser landed on for a token."""
    query = dict(parse_qsl(urlsplit(returned_url.strip()).query))
    if "error" in query:
        raise GuavaError(f"Authorisation refused: {query['error']}")
    if query.get("state") != pending.state or "code" not in query:
        raise GuavaError("That address does not belong to this authorisation")
    token = await _call(
        session,
        "POST",
        API_BASE[environment_for(client_id)] + "/oauth/token",
        authorization=_basic(client_id, client_secret),
        form={
            "grant_type": "authorization_code",
            "code": query["code"],
            "code_verifier": pending.verifier,
            "redirect_uri": pending.redirect_uri,
        },
    )
    token["obtained_at"] = time.time()
    return token


def resolve_medication(name: str, known: list[str]) -> str | None:
    """Find the exact stored name, ignoring case and surrounding spaces.

    Guava matches by exact name and creates a medication for a new one, and app-entered
    names can carry trailing spaces, so a dose must use the stored spelling.
    """
    wanted = name.strip().casefold()
    return next((med for med in known if med.strip().casefold() == wanted), None)


class GuavaClient:
    """Access to one grant, which means one person's profile."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
        token: dict[str, Any],
        on_token_update: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = token
        self._on_token_update = on_token_update
        self._lock = asyncio.Lock()
        self.environment = environment_for(client_id)
        self._base = API_BASE[self.environment]

    async def _access_token(self, stale: str | None = None) -> str:
        async with self._lock:
            token = self._token
            age = time.time() - token.get("obtained_at", 0)
            current = token.get("access_token")
            if (
                current
                and current != stale
                and age < token.get("expires_in", 3600) - REFRESH_MARGIN
            ):
                return current
            if current != stale and stale is not None:
                return current  # Another caller refreshed while this one waited.
            if not token.get("refresh_token"):
                raise GuavaAuthError("No refresh token: offline_access was not granted")
            # Refresh tokens rotate: the new pair is saved before either is used, because
            # the old one stops working and a lost new one means a fresh consent.
            new = await _call(
                self._session,
                "POST",
                self._base + "/oauth/token",
                authorization=_basic(self._client_id, self._client_secret),
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": token["refresh_token"],
                },
            )
            new["obtained_at"] = time.time()
            await self._on_token_update(new)
            self._token = new
            return new["access_token"]

    async def _api(
        self, method: str, url: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        access = await self._access_token()
        try:
            return await _call(
                self._session, method, url, authorization=f"Bearer {access}", body=body
            )
        except GuavaAuthError:
            # A rejected bearer wrote nothing, so one retry cannot double-log.
            access = await self._access_token(stale=access)
            return await _call(
                self._session, method, url, authorization=f"Bearer {access}", body=body
            )

    async def me(self) -> dict[str, Any]:
        """Return /v1/me: {user: {email}, profile: {id, given_name, family_name}}."""
        return await self._api("GET", self._base + "/v1/me")

    async def active_medications(self) -> list[str]:
        """Return active medication names exactly as stored, spaces included."""
        url: str | None = self._base + "/fhir/R4/MedicationRequest"
        names: list[str] = []
        for _ in range(MAX_FHIR_PAGES):
            if url is None:
                break
            page = await self._api("GET", url)
            for item in page.get("entry", []):
                resource = item.get("resource", {})
                concept = resource.get("medicationCodeableConcept") or {}
                coding = concept.get("coding") or [{}]
                name = concept.get("text") or coding[0].get("display")
                if name and resource.get("status") == "active" and name not in names:
                    names.append(name)
            url = next(
                (
                    link["url"]
                    for link in page.get("link", [])
                    if link.get("relation") == "next"
                ),
                None,
            )
        return names

    async def log_dose(
        self,
        medication: str,
        when: str,
        identifier: str,
        quantity: float | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Write one med_usage entry. The API has no undo: only the app can delete it."""
        entry: dict[str, Any] = {
            "time": when,
            "medication_name": medication,
            "identifier": [{"value": identifier, "system": "guava-ha-integration"}],
        }
        if quantity is not None:
            entry["quantity"] = quantity
        if notes:
            entry["notes"] = notes
        result = await self._api(
            "POST",
            self._base + "/v1/data-entries",
            {"type": "med_usage", "entries": [entry]},
        )
        if not result.get("accepted"):
            raise GuavaError(f"Guava did not accept the dose: {result}")
        return result
