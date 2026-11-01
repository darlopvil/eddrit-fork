"""
Privacy frontends for third-party content (YouTube, X, TikTok, Imgur...).

Reddit posts link to and embed other sites. Left as they are, opening a post
with a YouTube video makes the browser load youtube.com, doubleclick.net and
friends. With these settings, links point to self-hosted frontends instead and
embeds are served by them (Invidious for YouTube, Nitter for X, ...).

Each frontend is optional (env var with its base URL). When one is not set,
that kind of embed becomes a plain link to the original site, so the browser
never loads anything from it on its own. Same mapping as darlopvil/MeMe.
"""

import re
from typing import TypedDict

from eddrit import config

TWITTER_HOSTS = {"twitter.com", "x.com", "mobile.twitter.com", "mobile.x.com"}
YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com"}
TIKTOK_HOSTS = {"tiktok.com", "m.tiktok.com"}
IMGUR_HOSTS = {"imgur.com", "i.imgur.com", "m.imgur.com"}

_URL_RE = re.compile(r"^https?://([^/?#]+)(.*)$", re.IGNORECASE)
_YOUTUBE_ID_RE = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?(?:youtube(?:-nocookie)?\.com/"
    r"(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
_TWEET_ID_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/[^/]+/status(?:es)?/(\d+)"
)
_TIKTOK_ID_RE = re.compile(r"^https?://(?:www\.|m\.)?tiktok\.com/@[^/]+/video/(\d+)")
_TUMBLR_POST_RES = (
    re.compile(r"^https?://(?:www\.)?([a-z0-9-]+)\.tumblr\.com/post/(\d+)"),
    re.compile(r"^https?://(?:www\.)?tumblr\.com/([a-z0-9-]+)/(\d+)"),
)
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')


def _tumblr_to_priviblur(host: str, rest: str) -> str | None:
    """Same mapping as Priviblur's own url handler (and MeMe)."""
    base = config.PRIVIBLUR_URL
    if not base or not (host == "tumblr.com" or host.endswith(".tumblr.com")):
        return None
    parts = host.split(".")
    if len(parts) == 4 and parts[1] == "media":
        return f"{base}/tblr/media/{parts[0]}{rest}"
    if host == "assets.tumblr.com":
        return f"{base}/tblr/assets{rest}"
    if host == "static.tumblr.com":
        return f"{base}/tblr/static{rest}"
    if host == "a.tumblr.com":
        return f"{base}/tblr/a{rest}"
    if parts[0] == "tumblr":
        return f"{base}{rest}"
    if rest.startswith("/post"):
        return f"{base}/{parts[0]}{rest[5:]}"
    return f"{base}/{parts[0]}{rest}"


def frontend_href(url: str) -> str:
    """Where a link to another site should point: the matching frontend when
    one is configured, otherwise the original URL. Only the host changes, the
    frontends mirror the original paths."""
    m = _URL_RE.match(url or "")
    if not m:
        return url
    host = m.group(1).lower().removeprefix("www.")
    rest = m.group(2) or "/"

    if config.NITTER_URL and host in TWITTER_HOSTS:
        return config.NITTER_URL + rest
    if config.INVIDIOUS_URL and host in YOUTUBE_HOSTS:
        return config.INVIDIOUS_URL + rest
    if config.INVIDIOUS_URL and host == "youtu.be":
        # youtu.be/<id>?t=1 -> /watch?v=<id>&t=1
        video, _, query = rest.lstrip("/").partition("?")
        return f"{config.INVIDIOUS_URL}/watch?v={video}" + (
            f"&{query}" if query else ""
        )
    if config.PROXITOK_URL and host in TIKTOK_HOSTS:
        return config.PROXITOK_URL + rest
    if config.RIMGO_URL and host in IMGUR_HOSTS:
        return config.RIMGO_URL + rest
    if config.SOPRANO_URL and host == "tenor.com" and rest.startswith("/view/"):
        return config.SOPRANO_URL + rest
    if (priviblur := _tumblr_to_priviblur(host, rest)) is not None:
        return priviblur
    return url


def rewrite_links(content: str) -> str:
    """Point the links inside a post or comment body at the frontends."""
    return _HREF_RE.sub(lambda m: f'href="{frontend_href(m.group(1))}"', content)


class EmbedTarget(TypedDict):
    iframe: str | None  # frontend player to embed, if any
    href: str  # link to the content (through a frontend when possible)


def embed_target(url: str) -> EmbedTarget:
    """How to show third-party embedded content (a post's YouTube video...).

    Never the site's own player: either the frontend's embed, or a link.
    """
    iframe = None
    if config.INVIDIOUS_URL and (m := _YOUTUBE_ID_RE.match(url)):
        iframe = f"{config.INVIDIOUS_URL}/embed/{m.group(1)}"
    elif config.NITTER_URL and (m := _TWEET_ID_RE.match(url)):
        iframe = f"{config.NITTER_URL}/i/status/{m.group(1)}/embed"
    elif config.PROXITOK_URL and (m := _TIKTOK_ID_RE.match(url)):
        iframe = f"{config.PROXITOK_URL}/embed/v2/{m.group(1)}"
    elif config.PRIVIBLUR_URL:
        for regex in _TUMBLR_POST_RES:
            if (m := regex.match(url)) and m.group(1) != "www":
                iframe = f"{config.PRIVIBLUR_URL}/{m.group(1)}/{m.group(2)}/embed"
                break
    return {"iframe": iframe, "href": frontend_href(url)}
