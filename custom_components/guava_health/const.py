"""Constants for the Guava Health integration."""

DOMAIN = "guava_health"

API_BASE = {
    "live": "https://api.guavahealth.com",
    "test": "https://api-test.guavahealth.com",
}
AUTHORIZE_URL = "https://guavahealth.com/oauth/authorize"
DEFAULT_REDIRECT_URI = "https://localhost:8765/callback"

# Cloudflare answers 403 (error 1010) to default Python agents (tested 2026-11-03).
USER_AGENT = (
    "guava-ha-integration/1.0 (+https://github.com/danielrosehill/Guava-HA-Integration)"
)

# Only what the integration uses. The app may have more enabled; `profile` is added by
# Guava automatically (tested 2026-11-03).
SCOPES = (
    "offline_access",
    "launch/patient",
    "patient/med_usage.cruds",
    "patient/MedicationRequest.rs",
)

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_REDIRECT_URI = "redirect_uri"
CONF_CALLBACK_URL = "callback_url"
CONF_TOKEN = "token"
CONF_MEDICATIONS = "medications"
CONF_COOLDOWN = "cooldown_seconds"

DEFAULT_COOLDOWN = 60

EVENT_DOSE_LOGGED = "guava_health_dose_logged"
SERVICE_LOG_MEDICATION = "log_medication"
