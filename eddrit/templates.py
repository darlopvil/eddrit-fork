import json
from dataclasses import asdict, is_dataclass
from typing import Any

from urllib.parse import quote, urlparse

from starlette.templating import Jinja2Templates

from eddrit import __version__, config, models
from eddrit.routes.pages.media import ALLOWED_HOSTS as ALLOWED_MEDIA_HOSTS
from eddrit.utils.frontends import embed_target, frontend_href
from eddrit.utils.subreddit import is_homepage

templates = Jinja2Templates(directory="templates")

# Add global information to env
templates.env.globals["global"] = {
    "app_version": __version__,
    "subreddit_is_homepage": is_homepage,
    "subreddit_sorting_modes": [e.value for e in models.SubredditSortingMode],
    "user_sorting_modes": [e.value for e in models.UserSortingMode],
    "sorting_periods": [e.value for e in models.SubredditSortingPeriod],
}


# Add a json filter compatible with dataclasses
def _proxy_video_urls(item: Any) -> Any:
    """Rewrite MP4 video URLs to go through our media proxy.

    Videos are rendered by JS from a JSON blob, not by Jinja, so the `media`
    filter cannot reach them. Only MP4 sources are rewritten: DASH manifests
    (v.redd.it) are left untouched and keep going straight to Reddit, so their
    playback is unaffected.
    """
    if not isinstance(item, dict):
        return item
    video_format = str(item.get("video_format", ""))

    if video_format == str(models.PostVideoFormat.DASH):
        # DASH manifests are served under a path mirroring Reddit's layout, so
        # the relative BaseURLs inside the manifest resolve to our proxy too.
        item["url"] = dash_media_url(item.get("url") or "")
        if poster := item.get("poster_url"):
            item["poster_url"] = media_url(poster)
        return item

    if video_format != str(models.PostVideoFormat.MP4):
        return item
    for key in ("url", "poster_url"):
        if value := item.get(key):
            proxied = media_url(value)
            # Not a Reddit host (e.g. an Imgur .gifv as MP4): its frontend
            item[key] = frontend_href(proxied) if proxied == value else proxied
    return item


def to_json_dataclass(value: Any) -> str:
    if type(value) is list:
        converted = [asdict(item) if is_dataclass(item) else item for item in value]  # type: ignore
        converted = [_proxy_video_urls(item) for item in converted]
    else:
        converted = asdict(value) if is_dataclass(value) else value  # type: ignore
        converted = _proxy_video_urls(converted)
    return json.dumps(converted, default=str)


templates.env.filters["tojson_dataclass"] = to_json_dataclass


# Rewrite Reddit media URLs to go through our /media proxy, so the user's
# browser never talks to Reddit's CDNs. No-op when PROXY_MEDIA is disabled or
# when the URL is not a proxyable Reddit host (e.g. our own static files).
#
# `name` (optional) becomes the file name the browser offers when the media is
# saved ("Save image as"), instead of "media". Usually the post title.
def media_url(value: Any, name: str | None = None) -> Any:
    if not config.PROXY_MEDIA or not value or not isinstance(value, str):
        return value
    if not value.startswith("https://"):
        return value
    if urlparse(value).hostname not in ALLOWED_MEDIA_HOSTS:
        return value
    proxied = f"/media?url={quote(value, safe='')}"
    if name:
        proxied += f"&name={quote(name, safe='')}"
    return proxied


templates.env.filters["media"] = media_url


# Where the title of a post points. Upstream uses post.url as is, which for
# Reddit-hosted content sends the browser straight to Reddit: i.redd.it images,
# v.redd.it videos, reddit.com/gallery/<id> and crossposts to reddit.com.
def post_link(post: models.Post) -> str:
    if post.content.type == models.PostContentType.PICTURE:
        return media_url(post.content.picture.url, post.title)  # type: ignore[attr-defined]

    parsed = urlparse(post.url)
    host = parsed.hostname or ""
    if not host:
        return post.url  # self posts: already a path on this instance
    if host == "v.redd.it" or parsed.path.startswith("/gallery/"):
        return post.comments_url_path
    if host == "reddit.com" or host.endswith(".reddit.com"):
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")
    if host in ALLOWED_MEDIA_HOSTS:
        return media_url(post.url, post.title)
    return frontend_href(post.url)


templates.env.filters["post_link"] = post_link
templates.env.filters["frontend"] = frontend_href
templates.env.filters["embed_target"] = embed_target


def dash_media_url(value: str) -> str:
    """Rewrite a v.redd.it DASH manifest URL to our /media/dash proxy.

    The proxied path mirrors Reddit's layout, so the relative BaseURLs inside
    the manifest resolve to our proxy too. No-op when PROXY_MEDIA is disabled
    or for any other host.
    """
    if config.PROXY_MEDIA and (parsed := urlparse(value)).hostname == "v.redd.it":
        query = f"?{parsed.query}" if parsed.query else ""
        return f"/media/dash{parsed.path}{query}"
    return value


# Pick the video to show in the embed view, which uses a bare <video> element
# instead of Video.js. MP4 is preferred because it plays without JavaScript;
# DASH (most v.redd.it videos) needs the small dash.js loader of the embed.
def embed_video(videos: Any) -> dict[str, Any] | None:
    videos = list(videos or [])
    if not videos:
        return None
    video = next(
        (v for v in videos if v.video_format == models.PostVideoFormat.MP4),
        videos[0],
    )
    is_dash = video.video_format == models.PostVideoFormat.DASH
    return {
        "src": dash_media_url(video.url) if is_dash else media_url(video.url),
        "is_dash": is_dash,
        "is_gif": video.is_gif,
        "width": video.width,
        "height": video.height,
        "poster_url": media_url(video.poster_url),
    }


templates.env.filters["embed_video"] = embed_video
