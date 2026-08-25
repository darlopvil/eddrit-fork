from urllib.parse import urlparse

import httpx
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from eddrit import config
from eddrit.utils.httpx import get_httpx_async_transport

# Only these hosts can be proxied. Without this whitelist the instance would be
# an open proxy usable by anyone to fetch arbitrary URLs through our VPN egress.
ALLOWED_HOSTS = frozenset(
    {
        "i.redd.it",
        "preview.redd.it",
        "external-preview.redd.it",
        "a.thumbs.redditmedia.com",
        "b.thumbs.redditmedia.com",
        "styles.redditmedia.com",
        "www.redditstatic.com",
        "emoji.redditmedia.com",
    }
)

# Images only for now: video (v.redd.it, DASH) needs Range support and is a
# different beast, especially on low-end hardware.
ALLOWED_CONTENT_TYPE_PREFIXES = ("image/",)

BROWSER_CACHE_SECONDS = 86400

# Reddit's media CDN rejects the Android app User-Agent used for the API
# (403 + HTML block page). A regular browser UA is required here.
MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "image/avif,image/webp,*/*",
}


async def media_proxy(request: Request) -> Response:
    """
    Proxy Reddit media so the user's browser never talks to Reddit's CDNs.

    The full URL (including its query string and Reddit's HMAC `s=` signature)
    must be passed intact in the `url` parameter, otherwise Reddit rejects it.
    """
    if not config.PROXY_MEDIA:
        raise HTTPException(status_code=404)

    url = request.query_params.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Missing url parameter")

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail="Host not allowed")

    client = httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
        transport=get_httpx_async_transport(proxy=config.PROXY),
        follow_redirects=True,
    )

    try:
        req = client.build_request("GET", url, headers=MEDIA_HEADERS)
        upstream = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Cannot fetch media")

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code != 200 or not content_type.startswith(
        ALLOWED_CONTENT_TYPE_PREFIXES
    ):
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="Invalid media response")

    async def stream_and_close():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    headers = {"Cache-Control": f"public, max-age={BROWSER_CACHE_SECONDS}"}
    if content_length := upstream.headers.get("content-length"):
        headers["Content-Length"] = content_length

    return StreamingResponse(
        stream_and_close(),
        media_type=content_type,
        headers=headers,
    )


routes = [
    Route("/media", endpoint=media_proxy, methods=["GET"]),
]
