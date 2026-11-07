"""Shared fixtures. All names are fictional."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guava_health.const import DOMAIN

BASE = "https://api-test.guavahealth.com"
ME = {"user": {"email": "a@example.com"}, "profile": {"id": "p1", "given_name": "Sam"}}
MEDS = {
    "resourceType": "Bundle",
    "entry": [
        {"resource": {"status": "active", "medicationCodeableConcept": {"text": "Paracetamol "}}},
        {"resource": {"status": "active", "medicationCodeableConcept": {"text": "Ibuprofen"}}},
        {"resource": {"status": "stopped", "medicationCodeableConcept": {"text": "Old Med"}}},
    ],
}


def token(**extra):
    return {
        "access_token": "at1",
        "refresh_token": "rt1",
        "expires_in": 3600,
        "obtained_at": time.time(),
        **extra,
    }


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, hass):
    # The real http and frontend components aren't part of the test harness.
    hass.config.components.update({"http", "frontend"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch("custom_components.guava_health.add_extra_js_url") as add_js:
        hass.data["test_add_js"] = add_js
        yield


@pytest.fixture
def entry(hass):
    item = MockConfigEntry(
        domain=DOMAIN,
        title="Sam (sandbox)",
        unique_id="test:p1",
        data={
            "client_id": "guava_test_client_x",
            "client_secret": "s",
            "redirect_uri": "https://localhost:8765/callback",
            "token": token(),
        },
        options={"medications": ["Paracetamol "], "cooldown_seconds": 60},
    )
    item.add_to_hass(hass)
    return item
