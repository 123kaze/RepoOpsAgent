from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from nanobot.repoops.client import GitHubAPIError, GitHubClient


def _allow(_url: str) -> tuple[bool, str]:
    return True, ""


@pytest.mark.asyncio
async def test_request_json_uses_versioned_api_and_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widget/issues"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["x-github-api-version"] == "2026-03-10"
        return httpx.Response(200, json=[{"number": 7}])

    client = GitHubClient(
        token="secret",
        transport=httpx.MockTransport(handler),
        target_validator=_allow,
    )

    result = await client.request_json("GET", "repos/acme/widget/issues")

    assert result == [{"number": 7}]


@pytest.mark.asyncio
async def test_network_policy_blocks_before_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        target_validator=lambda _url: (False, "private address"),
    )

    with pytest.raises(GitHubAPIError, match="network policy"):
        await client.request_json("GET", "repos/acme/widget/issues")

    assert called is False


@pytest.mark.asyncio
async def test_log_download_validates_redirect_and_expands_zip() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("test/job.txt", "setup\nERROR failed assertion\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={
                    "location": "https://logs.actions.githubusercontent.com/run.zip"
                },
            )
        assert request.url.host == "logs.actions.githubusercontent.com"
        return httpx.Response(200, content=buffer.getvalue())

    client = GitHubClient(
        transport=httpx.MockTransport(handler),
        target_validator=_allow,
    )

    logs = await client.download_run_logs("acme/widget", 42)

    assert logs == {"test/job.txt": "setup\nERROR failed assertion\n"}


def test_encode_path_rejects_parent_segments() -> None:
    with pytest.raises(GitHubAPIError, match="unsafe segment"):
        GitHubClient.encode_path("../config.json")
