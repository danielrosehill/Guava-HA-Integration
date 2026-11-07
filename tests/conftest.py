"""Shared fixtures. All names are fictional."""

import time

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
def auto_enable(enable_custom_integrations):
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
