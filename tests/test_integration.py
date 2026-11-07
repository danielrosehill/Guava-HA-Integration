"""Flow, button and action behaviour against a mocked Guava API."""

from datetime import datetime
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.guava_health.api import resolve_medication
from custom_components.guava_health.const import DOMAIN, VERSION

from .conftest import BASE, ME, MEDS, token

ACCEPTED = {"accepted": 1, "environment": "test"}
BUTTON = "button.guava_sam_sandbox_log_paracetamol"


def api(mock, write=ACCEPTED, meds=MEDS):
    mock.get(f"{BASE}/fhir/R4/MedicationRequest", json=meds)
    mock.get(f"{BASE}/v1/me", json=ME)
    mock.post(f"{BASE}/v1/data-entries", json=write)


def writes(mock):
    return [
        json.loads(c[2]) if isinstance(c[2], (str, bytes)) else c[2]
        for c in mock.mock_calls
        if str(c[1]).endswith("/v1/data-entries")
    ]


async def load(hass, entry):
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def press(hass):
    await hass.services.async_call(
        "button", "press", {"entity_id": BUTTON}, blocking=True
    )


async def test_flow_creates_entry_with_exact_names(hass, aioclient_mock):
    api(aioclient_mock)
    aioclient_mock.post(f"{BASE}/oauth/par", json={"request_uri": "urn:x", "expires_in": 600})
    aioclient_mock.post(f"{BASE}/oauth/token", json=token())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "client_id": "guava_test_client_x",
            "client_secret": "s",
            "redirect_uri": "https://localhost:8765/callback",
        },
    )
    assert result["step_id"] == "authorize"
    assert "request_uri=urn%3Ax" in result["description_placeholders"]["url"]

    flow = hass.config_entries.flow._progress[result["flow_id"]]
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"callback_url": "https://localhost:8765/callback?code=c&state=no"}
    )
    assert bad["errors"] == {"base": "authorization_failed"}
    state = flow._pending.state
    query = urlencode({"code": "c", "state": state})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"callback_url": f"https://localhost:8765/callback?{query}"}
    )
    assert result["step_id"] == "medications"
    values = [o["value"] for o in result["data_schema"].schema["medications"].config["options"]]
    assert values == ["Paracetamol ", "Ibuprofen"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"medications": ["Paracetamol "], "cooldown_seconds": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sam (sandbox)"
    assert result["result"].unique_id == "test:p1"
    await hass.async_block_till_done()
    assert hass.states.get(BUTTON) is not None


async def test_button_logs_stored_name_then_cooldown_blocks(hass, aioclient_mock, entry):
    api(aioclient_mock)
    await load(hass, entry)
    events = []
    hass.bus.async_listen("guava_health_dose_logged", events.append)
    await press(hass)
    await hass.async_block_till_done()
    sent = writes(aioclient_mock)
    assert len(sent) == 1
    assert sent[0]["type"] == "med_usage"
    item = sent[0]["entries"][0]
    assert item["medication_name"] == "Paracetamol "
    assert item["identifier"][0]["value"].startswith("ha-")
    assert datetime.fromisoformat(item["time"]).tzinfo is not None
    assert events[0].data["medication"] == "Paracetamol"

    with pytest.raises(HomeAssistantError, match="cooldown"):
        await press(hass)
    assert len(writes(aioclient_mock)) == 1


async def test_log_dose_passes_quantity_and_records_it(hass, aioclient_mock, entry):
    api(aioclient_mock)
    await load(hass, entry)
    await hass.services.async_call(
        DOMAIN, "log_dose", {"quantity": 2}, target={"entity_id": BUTTON}, blocking=True
    )
    await hass.async_block_till_done()
    item = writes(aioclient_mock)[0]["entries"][0]
    assert item["medication_name"] == "Paracetamol "
    assert item["quantity"] == 2
    attributes = hass.states.get(BUTTON).attributes
    assert attributes["last_quantity"] == 2
    assert attributes["last_logged"] == item["time"]


async def test_failed_dose_is_not_recorded(hass, aioclient_mock, entry):
    api(aioclient_mock, write={"accepted": 0})
    await load(hass, entry)
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await hass.services.async_call(
            DOMAIN, "log_dose", {"quantity": 2}, target={"entity_id": BUTTON}, blocking=True
        )
    await hass.async_block_till_done()
    assert hass.states.get(BUTTON).attributes["last_logged"] is None


async def test_failed_write_does_not_start_cooldown(hass, aioclient_mock, entry):
    api(aioclient_mock, write={"accepted": 0})
    await load(hass, entry)
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await press(hass)
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await press(hass)
    assert len(writes(aioclient_mock)) == 2


async def test_action_matches_loosely_and_refuses_unknown(hass, aioclient_mock, entry):
    api(aioclient_mock)
    await load(hass, entry)
    await hass.services.async_call(
        DOMAIN, "log_medication", {"medication": "ibuprofen", "quantity": 2}, blocking=True
    )
    item = writes(aioclient_mock)[0]["entries"][0]
    assert item["medication_name"] == "Ibuprofen"
    assert item["quantity"] == 2

    with pytest.raises(ServiceValidationError, match="not an active"):
        await hass.services.async_call(
            DOMAIN, "log_medication", {"medication": "Ibu"}, blocking=True
        )
    assert len(writes(aioclient_mock)) == 1

    await hass.services.async_call(
        DOMAIN,
        "log_medication",
        {"medication": "Ibu", "allow_new": True},
        blocking=True,
    )
    assert writes(aioclient_mock)[1]["entries"][0]["medication_name"] == "Ibu"


async def test_two_profiles_need_a_target(hass, aioclient_mock, entry):
    api(aioclient_mock)
    second = type(entry)(
        domain=DOMAIN, title="Kid", unique_id="test:p2",
        data=dict(entry.data), options={"medications": []},
    )
    second.add_to_hass(hass)
    await load(hass, entry)
    assert second.state is ConfigEntryState.LOADED
    with pytest.raises(ServiceValidationError, match="config_entry_id"):
        await hass.services.async_call(
            DOMAIN, "log_medication", {"medication": "Ibuprofen"}, blocking=True
        )
    await hass.services.async_call(
        DOMAIN,
        "log_medication",
        {"medication": "Ibuprofen", "config_entry_id": second.entry_id},
        blocking=True,
    )
    assert len(writes(aioclient_mock)) == 1


async def _second_person(hass, aioclient_mock, person):
    aioclient_mock.post(f"{BASE}/oauth/par", json={"request_uri": "urn:x", "expires_in": 600})
    aioclient_mock.post(f"{BASE}/oauth/token", json=token())
    aioclient_mock.get(f"{BASE}/v1/me", json={"profile": {"id": person, "given_name": "Kid"}})
    aioclient_mock.get(f"{BASE}/fhir/R4/MedicationRequest", json=MEDS)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    fields = {str(k): k.default() for k in result["data_schema"].schema}
    assert fields["client_id"] == "guava_test_client_x"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], fields)
    state = hass.config_entries.flow._progress[result["flow_id"]]._pending.state
    query = urlencode({"code": "c", "state": state})
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"callback_url": f"https://localhost:8765/callback?{query}"}
    )


async def test_second_person_reuses_key(hass, aioclient_mock, entry):
    result = await _second_person(hass, aioclient_mock, "p2")
    assert result["step_id"] == "medications"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"medications": ["Ibuprofen"], "cooldown_seconds": 60}
    )
    assert result["result"].unique_id == "test:p2"


async def test_same_person_twice_is_refused(hass, aioclient_mock, entry):
    result = await _second_person(hass, aioclient_mock, "p1")
    assert result["reason"] == "already_configured"


async def test_refresh_rotates_and_is_saved_before_use(hass, aioclient_mock, entry, hass_storage):
    api(aioclient_mock)
    aioclient_mock.post(
        f"{BASE}/oauth/token",
        json={"access_token": "at2", "refresh_token": "rt2", "expires_in": 3600},
    )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "token": token(obtained_at=time.time() - 7200)}
    )
    await load(hass, entry)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.token"]["data"]
    assert stored["refresh_token"] == "rt2"
    bearer = [c for c in aioclient_mock.mock_calls if "MedicationRequest" in str(c[1])]
    assert bearer[0][3]["Authorization"] == "Bearer at2"


async def _reauth_to_paste(hass, aioclient_mock, entry):
    aioclient_mock.post(f"{BASE}/oauth/token", status=400, text='{"error":"invalid_grant"}')
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "token": token(obtained_at=0)}
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["step_id"] for f in flows] == ["reauth_confirm"]
    assert not any("/oauth/par" in str(c[1]) for c in aioclient_mock.mock_calls)

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/oauth/par", json={"request_uri": "urn:x", "expires_in": 600})
    aioclient_mock.post(f"{BASE}/oauth/token", json=token(access_token="at9", refresh_token="rt9"))
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
    assert result["step_id"] == "authorize"
    state = hass.config_entries.flow._progress[result["flow_id"]]._pending.state
    return result, urlencode({"code": "c", "state": state})


async def test_reauth_replaces_stale_stored_token(hass, aioclient_mock, entry, hass_storage):
    hass_storage[f"{DOMAIN}.{entry.entry_id}.token"] = {
        "version": 1, "key": f"{DOMAIN}.{entry.entry_id}.token", "data": token(obtained_at=0),
    }
    result, query = await _reauth_to_paste(hass, aioclient_mock, entry)
    api(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"callback_url": f"https://localhost:8765/callback?{query}"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["token"]["refresh_token"] == "rt9"
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_refuses_another_person(hass, aioclient_mock, entry):
    result, query = await _reauth_to_paste(hass, aioclient_mock, entry)
    aioclient_mock.get(f"{BASE}/v1/me", json={"profile": {"id": "other"}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"callback_url": f"https://localhost:8765/callback?{query}"}
    )
    assert result["reason"] == "wrong_profile"


async def test_options_remove_button(hass, aioclient_mock, entry):
    api(aioclient_mock)
    await load(hass, entry)
    assert er.async_get(hass).async_get(BUTTON)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "buttons"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"medications": ["Ibuprofen"], "cooldown_seconds": 30}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(BUTTON) is None
    assert hass.states.get("button.guava_sam_sandbox_log_ibuprofen") is not None


async def open_tag_links(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tag_links"}
    )


async def test_tag_links_list_each_medication_and_stay_the_same(
    hass, aioclient_mock, entry
):
    api(aioclient_mock)
    await load(hass, entry)
    before = aioclient_mock.call_count
    first = await open_tag_links(hass, entry)
    links = first["description_placeholders"]["links"]
    found = re.findall(r"^- (.+): `https://guavahealth\.com/nfc/([A-Za-z0-9]{12})`$", links, re.M)
    assert [name for name, _ in found] == ["Paracetamol", "Ibuprofen"]
    assert len({tag for _, tag in found}) == 2
    assert "Old Med" not in links

    done = await hass.config_entries.options.async_configure(first["flow_id"], {})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"medications": ["Paracetamol "], "cooldown_seconds": 60}

    again = await open_tag_links(hass, entry)
    assert again["description_placeholders"]["links"] == links
    # Reading the list is all this page does: nothing is written to Guava.
    assert all(call[0] == "GET" for call in aioclient_mock.mock_calls[before:])


async def test_tag_links_with_no_medications(hass, aioclient_mock, entry):
    api(aioclient_mock, meds={"resourceType": "Bundle", "entry": []})
    hass.config_entries.async_update_entry(entry, options={"medications": []})
    await load(hass, entry)
    result = await open_tag_links(hass, entry)
    assert result["description_placeholders"]["links"] == "No active medications."


async def test_card_is_served_with_a_versioned_url(hass, aioclient_mock, entry):
    api(aioclient_mock)
    await load(hass, entry)
    (paths,) = hass.http.async_register_static_paths.call_args.args
    assert paths[0].url_path == "/guava_health/guava-dose-card.js"
    assert Path(paths[0].path).is_file()
    assert hass.data["test_add_js"].call_args.args[1].endswith("?v=" + VERSION)
    manifest = json.loads((Path(paths[0].path).parents[1] / "manifest.json").read_text())
    assert manifest["version"] == VERSION


def test_resolve():
    assert resolve_medication(" PARACETAMOL", ["Paracetamol "]) == "Paracetamol "
    assert resolve_medication("Para", ["Paracetamol "]) is None
