# steps/api/conftest.py
import json
import os
import httpx
import pytest
from dotenv import load_dotenv
from pytest_bdd import given, then, parsers

load_dotenv()

FRONTEND_URL = os.environ["FRONTEND_URL"]
BACKEND_URL  = os.environ["BACKEND_URL"]

# Auth state files saved by `npm run setup:auth` (Playwright browser login)
AUTH_DIR = os.path.join(os.path.dirname(__file__), '../../.auth')

ROLE_AUTH_FILE = {
    "officer":  "officer.json",
    "officer2": "officer2.json",
    "sergeant": "sergeant.json",
    "admin":    "admin.json",
    "iauser":   "iauser.json",
    "sysops":   "sysops.json",
}

# Local-stack mode: when DEMS_ROPC=1, mint role tokens directly from local
# Keycloak via the dems-dev-ropc password-grant client instead of Playwright
# sessions. Maps QA role keys -> seeded local (username, password).
ROPC_CREDS = {
    "officer":  ("dems_officer1", "demsofficer1pass"),
    "officer2": ("dems_officer2", "demsofficer2pass"),
    "sergeant": ("dems_sergeant1", "demssergeant1pass"),
    "admin":    ("dems_admin1", "demsadmin1pass"),
    "iauser":   ("dems_iauser", "demsiauserpass"),
    "sysops":   ("sysops1", "sysops1pass"),
}
ROPC_TOKEN_URL = os.environ.get(
    "KEYCLOAK_TOKEN_URL",
    "http://localhost:8080/oidc/realms/dems/protocol/openid-connect/token",
)
ROPC_CLIENT_ID = os.environ.get("ROPC_CLIENT_ID", "dems-dev-ropc")


def _fetch_token_ropc(role: str) -> str:
    """Local-stack path: Keycloak resource-owner password grant (no browser)."""
    username, password = ROPC_CREDS[role]
    resp = httpx.post(
        ROPC_TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": ROPC_CLIENT_ID,
            "username": username,
            "password": password,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_token_from_session(role: str) -> str:
    """
    Extract Keycloak Bearer token from a Playwright-saved auth state.
    Calls /api/auth/session with the NextAuth session cookie — no direct
    Keycloak access grants needed.
    Run `npm run setup:auth` first to create .auth/*.json files.
    When DEMS_ROPC=1 (local stack), use the ROPC password grant instead.
    """
    if os.environ.get("DEMS_ROPC") == "1":
        return _fetch_token_ropc(role)
    auth_file = os.path.join(AUTH_DIR, ROLE_AUTH_FILE[role])
    if not os.path.exists(auth_file):
        raise FileNotFoundError(
            f".auth/{ROLE_AUTH_FILE[role]} not found. "
            f"Run `npm run setup:auth` first to save Playwright sessions."
        )

    with open(auth_file) as f:
        auth_state = json.load(f)

    # Extract NextAuth session cookie from Playwright storage state
    cookies = {
        c["name"]: c["value"]
        for c in auth_state.get("cookies", [])
        if "session-token" in c["name"]
    }
    if not cookies:
        raise ValueError(f"No session-token cookie found in .auth/{ROLE_AUTH_FILE[role]}")

    resp = httpx.get(f"{FRONTEND_URL}/api/auth/session", cookies=cookies, timeout=10)
    resp.raise_for_status()

    token = resp.json().get("user", {}).get("accessToken")
    if not token:
        raise ValueError(
            f"No accessToken in /api/auth/session response for role={role}. "
            f"Session may be expired — re-run `npm run setup:auth`."
        )
    return token


def api_client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=BACKEND_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )


@pytest.fixture(scope="session")
def officer_token() -> str:
    return fetch_token_from_session("officer")

@pytest.fixture(scope="session")
def officer2_token() -> str:
    return fetch_token_from_session("officer2")

@pytest.fixture(scope="session")
def sergeant_token() -> str:
    return fetch_token_from_session("sergeant")

@pytest.fixture(scope="session")
def admin_token() -> str:
    return fetch_token_from_session("admin")

@pytest.fixture(scope="session")
def iauser_token() -> str:
    return fetch_token_from_session("iauser")

@pytest.fixture(scope="session")
def sysops_token() -> str:
    return fetch_token_from_session("sysops")


@pytest.fixture
def context() -> dict:
    return {}


@given(parsers.parse('I am logged in as "{role}"'))
def i_am_logged_in_as(context, officer_token, officer2_token, sergeant_token,
                       admin_token, iauser_token, sysops_token, role):
    token_map = {
        "officer1":  officer_token,
        "officer2":  officer2_token,
        "sergeant1": sergeant_token,
        "admin":     admin_token,
        "iauser":    iauser_token,
        "sysops1":   sysops_token,
    }
    token = token_map.get(role)
    if token is None:
        raise ValueError(f"Unknown role: {role!r}. Valid roles: {list(token_map)}")
    context["token"] = token
    context["client"] = api_client(token)


@then('I should receive a 403 error')
def receive_403(context):
    assert context["last_response"].status_code == 403, \
        f"Expected 403, got {context['last_response'].status_code}: {context['last_response'].text}"
