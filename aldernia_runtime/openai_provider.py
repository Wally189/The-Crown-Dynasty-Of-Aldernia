from __future__ import annotations

import json
import os
from typing import Any, Mapping
import urllib.parse
import urllib.request


OPENAI_WIF_ENV = (
    "OPENAI_WIF_AUDIENCE",
    "OPENAI_IDENTITY_PROVIDER_ID",
    "OPENAI_SERVICE_ACCOUNT_ID",
)
GITHUB_OIDC_ENV = (
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
)


class OpenAIProviderError(RuntimeError):
    pass


def wif_configured(env: Mapping[str, str] | None = None) -> bool:
    values = env or os.environ
    return all(str(values.get(name) or "").strip() for name in OPENAI_WIF_ENV + GITHUB_OIDC_ENV)


def provider_available(
    *,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    values = env or os.environ
    key = api_key if api_key is not None else values.get("OPENAI_API_KEY", "")
    return wif_configured(values) or bool(str(key or "").strip())


def _github_actions_oidc_token_provider(audience: str):
    request_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    request_token = os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]

    def get_token() -> str:
        parsed = urllib.parse.urlparse(request_url)
        query = dict(
            urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        )
        query["audience"] = audience
        url = urllib.parse.urlunparse(
            parsed._replace(query=urllib.parse.urlencode(query))
        )
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"bearer {request_token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        token = payload.get("value")
        if not token:
            raise OpenAIProviderError(
                "GitHub OIDC token response did not include a value"
            )
        return str(token)

    return {"token_type": "jwt", "get_token": get_token}


def _wif_response(body: Mapping[str, Any]) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise OpenAIProviderError(
            "OpenAI SDK is required for workload-identity authentication"
        ) from exc

    client = OpenAI(
        workload_identity={
            "identity_provider_id": os.environ["OPENAI_IDENTITY_PROVIDER_ID"],
            "service_account_id": os.environ["OPENAI_SERVICE_ACCOUNT_ID"],
            "provider": _github_actions_oidc_token_provider(
                os.environ["OPENAI_WIF_AUDIENCE"]
            ),
        },
    )
    response = client.responses.create(**dict(body))
    if hasattr(response, "model_dump"):
        value = response.model_dump()
    elif hasattr(response, "model_dump_json"):
        value = json.loads(response.model_dump_json())
    else:
        raise OpenAIProviderError(
            "OpenAI SDK response did not expose serialisable response data"
        )
    if not isinstance(value, dict):
        raise OpenAIProviderError("OpenAI SDK response was not an object")
    return value


def _api_key_response(
    body: Mapping[str, Any],
    *,
    api_key: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(dict(body)).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise OpenAIProviderError(
            f"OpenAI Responses API request failed: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise OpenAIProviderError("OpenAI Responses API returned a non-object")
    return value


def responses_create(
    body: Mapping[str, Any],
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Prefer short-lived GitHub OIDC -> OpenAI WIF; API key is fallback only."""
    if wif_configured():
        return _wif_response(body)
    key = str(api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if key:
        return _api_key_response(body, api_key=key)
    raise OpenAIProviderError(
        "No OpenAI provider configured: set GitHub/OpenAI workload identity "
        "variables or an explicit fallback API key"
    )
