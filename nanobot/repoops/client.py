"""Narrow, SSRF-guarded GitHub REST client used by RepoOps tools."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from typing import TypeAlias, cast
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx

from nanobot.security.network import validate_url_target

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
TargetValidator: TypeAlias = Callable[[str], tuple[bool, str]]

_API_VERSION = "2026-03-10"
_DOWNLOAD_HOST_SUFFIXES = (
    ".actions.githubusercontent.com",
    ".blob.core.windows.net",
    ".githubusercontent.com",
)


class GitHubAPIError(RuntimeError):
    """A bounded GitHub API or payload error safe to return to the model."""


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in cast(list[object], value))
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in cast(dict[object, object], value).items()
        )
    return False


class GitHubClient:
    """Async GitHub client with an explicit API origin boundary.

    The configured API base is a user-authorized trust boundary (and supports
    GitHub Enterprise Server). Every request still passes the shared SSRF
    validator. Download redirects are accepted only for GitHub Actions'
    documented signed-log hosts and are validated again before use.
    """

    def __init__(
        self,
        *,
        token: str = "",
        api_base: str = "https://api.github.com",
        timeout: float = 30.0,
        max_download_bytes: int = 5_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
        target_validator: TargetValidator = validate_url_target,
    ) -> None:
        parsed = urlparse(api_base)
        if parsed.scheme != "https" or not parsed.netloc:
            raise GitHubAPIError("RepoOps apiBase must be an absolute HTTPS URL")
        if parsed.query or parsed.fragment:
            raise GitHubAPIError("RepoOps apiBase cannot contain a query or fragment")
        self.api_base = api_base.rstrip("/") + "/"
        self._api_origin = (parsed.scheme.lower(), parsed.netloc.lower())
        self.token = token.strip()
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes
        self.transport = transport
        self.target_validator = target_validator

    def _headers(self, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "RepoOps-Agent/0.1",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _api_url(self, path: str, params: dict[str, str | int] | None = None) -> str:
        if path.startswith(("http://", "https://", "//")):
            raise GitHubAPIError("GitHub API paths must be relative")
        url = urljoin(self.api_base, path.lstrip("/"))
        parsed = urlparse(url)
        if (parsed.scheme.lower(), parsed.netloc.lower()) != self._api_origin:
            raise GitHubAPIError("GitHub API path escaped the configured API origin")
        if params:
            url = f"{url}?{urlencode(params)}"
        self._require_safe_target(url)
        return url

    def _require_safe_target(self, url: str) -> None:
        valid, error = self.target_validator(url)
        if not valid:
            raise GitHubAPIError(f"GitHub request blocked by network policy: {error}")

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, JsonValue] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> JsonValue:
        url = self._api_url(path, params)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(accept=accept),
                    json=body,
                )
            except httpx.RequestError as exc:
                raise GitHubAPIError(f"GitHub request failed: {exc}") from exc
        self._raise_for_status(response)
        try:
            payload: object = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise GitHubAPIError("GitHub returned a non-JSON response") from exc
        if not _is_json_value(payload):
            raise GitHubAPIError("GitHub returned an unsupported JSON payload")
        return cast(JsonValue, payload)

    async def request_text(
        self,
        path: str,
        *,
        accept: str,
    ) -> str:
        url = self._api_url(path)
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            try:
                response = await client.get(url, headers=self._headers(accept=accept))
            except httpx.RequestError as exc:
                raise GitHubAPIError(f"GitHub request failed: {exc}") from exc
        self._raise_for_status(response)
        return response.text

    async def download_run_logs(self, repository: str, run_id: int) -> dict[str, str]:
        url = self._api_url(f"repos/{repository}/actions/runs/{run_id}/logs")
        response = await self._download_with_safe_redirect(url)
        raw = response.content
        if len(raw) > self.max_download_bytes:
            raise GitHubAPIError(
                f"GitHub Actions log archive exceeds {self.max_download_bytes} bytes"
            )
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                result: dict[str, str] = {}
                total = 0
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    total += info.file_size
                    if total > self.max_download_bytes:
                        raise GitHubAPIError("Expanded GitHub Actions logs exceed the size limit")
                    content = archive.read(info).decode("utf-8", errors="replace")
                    result[info.filename] = content
                return result
        except zipfile.BadZipFile as exc:
            raise GitHubAPIError("GitHub Actions returned an invalid log archive") from exc

    async def _download_with_safe_redirect(self, initial_url: str) -> httpx.Response:
        current_url = initial_url
        async with httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            for redirect_count in range(4):
                self._require_safe_target(current_url)
                try:
                    response = await client.get(current_url, headers=self._headers())
                except httpx.RequestError as exc:
                    raise GitHubAPIError(f"GitHub log download failed: {exc}") from exc
                if response.status_code not in {301, 302, 303, 307, 308}:
                    self._raise_for_status(response)
                    return response
                location = response.headers.get("location", "")
                if not location:
                    raise GitHubAPIError("GitHub log redirect omitted the Location header")
                next_url = urljoin(current_url, location)
                parsed = urlparse(next_url)
                hostname = (parsed.hostname or "").lower()
                if parsed.scheme != "https" or not any(
                    hostname.endswith(suffix) for suffix in _DOWNLOAD_HOST_SUFFIXES
                ):
                    raise GitHubAPIError("GitHub log redirect target is not authorized")
                current_url = next_url
                if redirect_count == 3:
                    break
        raise GitHubAPIError("GitHub log download exceeded the redirect limit")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        message = ""
        try:
            payload: object = response.json()
            if isinstance(payload, dict):
                raw_message = cast(dict[object, object], payload).get("message")
                if isinstance(raw_message, str):
                    message = raw_message
        except (json.JSONDecodeError, ValueError):
            message = response.text[:500]
        suffix = f": {message}" if message else ""
        raise GitHubAPIError(f"GitHub API returned HTTP {response.status_code}{suffix}")

    @staticmethod
    def encode_path(path: str) -> str:
        cleaned = path.strip().lstrip("/")
        if not cleaned or cleaned.endswith("/"):
            raise GitHubAPIError("Repository file path must name a file")
        if any(part in {"", ".", ".."} for part in cleaned.split("/")):
            raise GitHubAPIError("Repository file path contains an unsafe segment")
        return quote(cleaned, safe="/")
