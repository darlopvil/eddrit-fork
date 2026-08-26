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

# Images and plain MP4 video. DASH (v.redd.it manifests + CMAF segments) is not
# proxied yet: those URLs are left untouched and still go straight to Reddit.
ALLOWED_CONTENT_TYPE_PREFIXES = ("image/", "video/")

BROWSER_CACHE_SECONDS = 86400

# Reddit's media CDN rejects the Android app User-Agent used for the API
# (403 + HTML block page). A regular browser UA is required here.
MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "image/avif,image/webp,video/webm,video/mp4,*/*;q=0.8",
}


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Shared client so TLS connections to Reddit's CDN are pooled and reused.

    Creating a client per request means a full TLS handshake per image, which
    adds up when a listing page fetches dozens of thumbnails through the VPN.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=get_httpx_async_transport(proxy=config.PROXY),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


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

    client = _get_client()

    try:
        headers = dict(MEDIA_HEADERS)
        # Forward the browser's Range header so seeking works on videos
        if range_header := request.headers.get("range"):
            headers["Range"] = range_header
        req = client.build_request("GET", url, headers=headers)
        upstream = await client.send(req, stream=True)
    except Exception:
        raise HTTPException(status_code=502, detail="Cannot fetch media")

    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code not in (200, 206) or not content_type.startswith(
        ALLOWED_CONTENT_TYPE_PREFIXES
    ):
        await upstream.aclose()
        raise HTTPException(status_code=502, detail="Invalid media response")

    async def stream_and_close():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    response_headers = {
        "Cache-Control": f"public, max-age={BROWSER_CACHE_SECONDS}",
        "Accept-Ranges": "bytes",
    }
    if content_length := upstream.headers.get("content-length"):
        response_headers["Content-Length"] = content_length
    if content_range := upstream.headers.get("content-range"):
        response_headers["Content-Range"] = content_range

    return StreamingResponse(
        stream_and_close(),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
    )


routes = [
    Route("/media", endpoint=media_proxy, methods=["GET"]),
]
