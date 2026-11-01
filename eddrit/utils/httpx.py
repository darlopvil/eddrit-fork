import asyncio
from json import JSONDecodeError

import httpx
from httpx_curl_cffi import AsyncCurlTransport, CurlTransport
from loguru import logger

from eddrit.exceptions import (
    ContentCannotBeViewedError,
    RateLimitedError,
    SubredditNotFoundError,
    UserBlockedError,
    UserNotFoundError,
    UserSuspendedError,
    WikiPageNotFoundError,
)

IMPERSONATE = "chrome131_android"


def get_httpx_transport(proxy: str | None = None) -> httpx.BaseTransport:
    """Get an HTTPX transport with fingerprint impersonation.

    The proxy is passed to the curl_cffi transport itself (not to the
    httpx.Client), otherwise httpx routes proxied requests through its default
    transport and bypasses the impersonation entirely.
    """
    return CurlTransport(impersonate=IMPERSONATE, default_headers=True, proxy=proxy)


def get_httpx_async_transport(
    proxy: str | None = None,
    impersonate: str = IMPERSONATE,
) -> httpx.AsyncBaseTransport:
    """Get an HTTPX async transport with fingerprint impersonation.

    The proxy is passed to the curl_cffi transport itself (not to the
    httpx.AsyncClient), otherwise httpx routes proxied requests through its
    default transport and bypasses the impersonation entirely.
    """
    return RecyclingAsyncTransport(proxy=proxy, impersonate=impersonate)


def _is_blocked_page(response: httpx.Response) -> bool:
    return response.status_code == 403 and b"blocked by network security" in (
        response.content
    )


class RecyclingAsyncTransport(httpx.AsyncBaseTransport):
    """curl_cffi transport that starts over when Reddit blocks it.

    Reddit's edge can flag a long-lived connection (or the curl session behind
    it) and keep answering it with the "blocked by network security" 403, while
    a brand-new one from the same IP, with the same token, works. Seen on
    1 Nov 2026: three 429 pages in a row, then 200 right after restarting
    eddrit, nothing else changed. Instead of needing that restart, a blocked
    GET is retried once on a fresh curl session (new connection and cookies).
    If that one is blocked too, the IP itself is blocked and the 403 is
    returned as usual.
    """

    def __init__(
        self, proxy: str | None = None, impersonate: str = IMPERSONATE
    ) -> None:
        self._proxy = proxy
        self._impersonate = impersonate
        self._inner = self._new_inner()

    def _new_inner(self) -> AsyncCurlTransport:
        return AsyncCurlTransport(
            impersonate=self._impersonate, default_headers=True, proxy=self._proxy
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        inner = self._inner
        response = await inner.handle_async_request(request)
        if response.status_code != 403 or request.method not in ("GET", "HEAD"):
            return response

        await response.aread()
        if not _is_blocked_page(response):
            return response

        # Swap in a fresh session once, even if several requests hit the block
        # at the same time. The old one is closed later, not now, so requests
        # still running on it are not cut off.
        if self._inner is inner:
            logger.warning(
                "Reddit blocked the current connection: retrying on a fresh one"
            )
            self._inner = self._new_inner()
            asyncio.get_running_loop().call_later(
                60, lambda: asyncio.ensure_future(inner.aclose())
            )
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


async def raise_if_content_is_not_available(api_res: httpx.Response) -> None:
    """Response hook: raise if Reddit content is unavailable (banned, private etc.)."""
    await api_res.aread()

    request_path = str(api_res.request.url.path)
    is_user_path = "/user/" in request_path
    is_subreddit_path = "/r/" in request_path
    has_json = not request_path.endswith(".rss") and api_res.request.method != "HEAD"

    # Rate-limit: HTML 403 blocked page (applies to all requests)
    if api_res.status_code == 403 and "blocked by network security" in api_res.text:
        raise RateLimitedError()

    # Parse JSON body when applicable (not a list)
    json_dict_body: dict = {}
    if has_json:
        try:
            parsed = api_res.json()
        except JSONDecodeError:
            logger.exception(
                "Cannot parse JSON from response with status code {api_status_code}"
                " and content {api_content}",
                api_status_code=api_res.status_code,
                api_content=api_res.text,
            )
            raise
        if isinstance(parsed, dict):
            json_dict_body = parsed

    # User-specific checks
    if is_user_path and api_res.status_code == 404:
        raise UserNotFoundError(api_res.status_code)
    if is_user_path and json_dict_body.get("data", {}).get("is_suspended", False):
        raise UserSuspendedError(api_res.status_code)
    if is_user_path and json_dict_body.get("data", {}).get("is_blocked", False):
        raise UserBlockedError(api_res.status_code)

    # Wiki page not found
    if api_res.status_code == 404 and json_dict_body.get("reason") == "PAGE_NOT_FOUND":
        raise WikiPageNotFoundError(status_code=api_res.status_code)

    # Banned subreddit
    if api_res.status_code == 404 and json_dict_body.get("reason") == "banned":
        raise ContentCannotBeViewedError(api_res.status_code, "banned")

    # Quarantined/private/gated
    if api_res.status_code == 403 and (reason := json_dict_body.get("reason")):
        raise ContentCannotBeViewedError(api_res.status_code, reason)

    # Subreddit-specific not-found checks
    if (
        is_subreddit_path
        and api_res.status_code == 302
        and "search" in api_res.headers.get("location", "")
    ):
        raise SubredditNotFoundError(status_code=404)

    if (
        is_subreddit_path
        and len(api_res.history) > 0
        and api_res.history[0].status_code != 200
    ):
        raise SubredditNotFoundError(status_code=api_res.status_code)

    if is_subreddit_path and json_dict_body.get("error") == 404:
        raise SubredditNotFoundError(status_code=api_res.status_code)
