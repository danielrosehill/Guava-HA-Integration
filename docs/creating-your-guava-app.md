# Creating your own Guava app

Each user creates their own app in Guava's API portal. Observed 2026-11-03 and 2026-11-07; the
portal is young and may change. A fuller field-by-field guide is in the plugin repo:
[creating-the-api-app.md](https://github.com/danielrosehill/Guava-Health-Plugin/blob/master/docs/creating-the-api-app.md).

## 1. Create the app

1. Sign up at https://guavahealth.com/api. API access to your own data is free; business use goes
   through Guava. The portal is at https://guavahealth.com/api-portal.
2. **API Apps → New App.**
3. Fill in:

| Field | Value |
|---|---|
| App Name | Anything. It appears on the consent screen. |
| Redirect URIs | `https://localhost:8765/callback`. Nothing has to listen there. |
| Purpose | What it does, and that it's personal, non-commercial and single-household. |
| Long-lived background access (`offline_access`) | Tick. Without it, sign-in lasts an hour. |
| Patient ID for this connection (`launch/patient`) | Tick. |
| Medication usage (Logging) | Read + Write |
| Medications (Clinical records) | Read |

More scopes are fine. The integration requests only `offline_access`, `launch/patient`,
`patient/med_usage.cruds` and `patient/MedicationRequest.rs`.

4. **Create App**, and store the test client id and secret straight away.

One app covers a whole family: Guava grants per person, and you choose the person on the consent
screen.

## 2. Complete the sandbox login (required before approval)

Guava refuses **Submit for production approval** until the test key has done all of these
(observed 2026-11-07):

1. `POST /oauth/par`
2. An `authorization_code` exchange at `/oauth/token`
3. A `/v1` request

Adding this integration with the **test** key does all three, because it calls `/v1/me` after the
exchange. The consent screen is marked as a sandbox authorisation and the grant is for Guava's
fictional patient, so no real data is involved. The entry appears with `(sandbox)` in its title, and
you can delete it afterwards. The same steps can be run with `guava auth` from the
[plugin](https://github.com/danielrosehill/Guava-Health-Plugin).

## 3. Get a live key

1. Open the app, then **Submit for production approval**. Personal apps were approved instantly
   (2026-11-03).
2. **Create live key**, and store the id and secret.
3. Add the integration again with the live key, once per person.

The host follows the key: ids starting `guava_test_` use `api-test.guavahealth.com`, others use
`api.guavahealth.com`. A test key on live gets `invalid_client`.

## Use a separate app from your other Guava tools

Refresh tokens rotate, so two clients can't share a grant. A separate app for Home Assistant keeps
it independent of anything else you run.
