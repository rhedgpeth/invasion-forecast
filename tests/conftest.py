import json

import httpx
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fake_http(routes: dict) -> httpx.AsyncClient:
    """An HTTP client whose responses are scripted per URL prefix: no network, no credits.

    Each value is (status, body) where body is a dict/list (sent as JSON) or a str (sent as text).
    """
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, (status, body) in routes.items():
            if url.startswith(prefix):
                if isinstance(body, str):
                    return httpx.Response(status, text=body)
                return httpx.Response(status, content=json.dumps(body), headers={"content-type": "application/json"})
        raise AssertionError(f"unexpected request: {url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))
