# Guava-HA-Integration: notes for agents

Public, unofficial Home Assistant custom integration (`custom_components/guava_health`) for logging
doses to Guava Health. Not a Guava product: keep the notice at the top of README.md, and never word
anything as if Guava made, endorses or supports it. Installable through HACS as a custom repository (`hacs.json`); not in the default store yet: [hacs/default#13588](https://github.com/hacs/default/pull/13588) opened 2026-11-07, review takes months. Update README.md when it merges. The gateway GitHub token got a 403 opening that PR, so it went through `gh`. The icon in `custom_components/guava_health/brand/` is original: never swap in Guava's logo or a Home Assistant one.

Release tier: small (`v1.0`, `v1.1`, ...). Keep `manifest.json` `version` and `VERSION` in `const.py` in step; `VERSION` also busts the card's browser cache.

## Hard rules

1. **No personal data.** No client ids, secrets, tokens, profile or patient ids, real family names,
   or medication names from a real account. Use Paracetamol and Ibuprofen in examples and tests. Exception: `docs/images/choose-medications.png` shows the owner's name and medications, published at the owner's request (2026-11-07).
2. **Dated facts.** Statements about Guava's API carry the date observed, and say tested or confirmed.
   The reference is `docs/api-notes.md` in Guava-Health-Plugin; add new API findings there, not here.
3. **Writes are permanent from here.** The API can't delete a dose. Never add automatic retries on
   `POST /v1/data-entries` beyond the existing one after a rejected bearer token.

## Design decisions (2026-11-07)

- Direct API client inside Home Assistant, no MQTT bridge or `guava-mcp`: rotating refresh tokens need
  saved state, which rules out `rest_command`, and the API is public.
- Paste-back consent instead of Home Assistant's OAuth2 helper, because Guava needs PAR.
- Rotated tokens go to a `Store` saved immediately, not `entry.data`, whose saves are delayed. `entry.data`
  holds the first token only, and reauth deletes the store so that it applies.
- Reauth waits on a confirm step before calling PAR, because the consent link dies after 600 seconds
  and a reauth flow can sit unopened for hours.
- `api.py` has no Home Assistant imports.
- Quantity is chosen on the card, starting at 1. Guava's dose text is free text, mostly strength (`20mg`), so it must never be parsed into a quantity (checked on live 2026-11-07).
- NFC tag links (Configure → Links for NFC tags) are generated here, one per medication, and kept in a `Store` per entry so they never change. They can't be discovered: `guavahealth.com/nfc/<id>` identifies the tag, what it logs is set in Guava on first tap, and `docs/api-notes.md` lists no tag endpoint (2026-11-07). Don't add Home Assistant's `tag` integration to this route: the tag already goes straight to Guava.
- `guava-dose-card` ships inside the integration, so it works without HACS. It calls `guava_health.log_dose` and waits for the call, so its tick means Guava accepted the dose. Don't make it fire and forget.

## Test

`uv sync --python 3.13 && .venv/bin/pytest`. The status table in README.md says what has only been
tested against a mock.
