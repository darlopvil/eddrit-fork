"""
Reddit's GraphQL API (gql-fed.reddit.com), the one the Android app uses.

Since 11 Nov 2026 Reddit refuses post search to the anonymous token on its
JSON API (every host and path variant), but the app's search query
(`DynamicSearch`) still answers the same anonymous token, with its own quota
(about 550 requests every 10 minutes per token, against 100 for the JSON API).
So search keeps working without spending the throwaway account's quota.

Results are converted to the JSON API's format (a "Listing" of "t3" items),
so the rest of eddrit parses them as before. Only the fields eddrit reads are
filled in. The JSON API sends text, HTML and URLs HTML-escaped and eddrit's
parser unescapes them, so they are escaped here the same way (_escaped):
GraphQL sends them raw, and unescaping a raw URL breaks it (`&lthumb=true`
turned into `<humb=true`, broken thumbnails), and unescaping raw HTML would
turn escaped text such as `&lt;script&gt;` into real tags. The query and the field names were taken from a real answer of
Reddit (11 Nov 2026) and from Nachtalb/redlib, which uses the same query.
"""

import datetime
import hashlib
import html
import json
import uuid
from typing import Any

import httpx
from loguru import logger

from eddrit import config
from eddrit.utils.httpx import (
    get_httpx_async_transport,
    raise_if_content_is_not_available,
)
from eddrit.utils.oauth import _get_login_headers_from_cache, oauth_login

GQL_URL = "https://gql-fed.reddit.com/"
DYNAMIC_SEARCH_HASH = "4acac653e5aac2ae2f08114d07ba7582e852464a57e324547c259ea3ef2ebd40"

# A page of the app's search has about 7 posts; the JSON API gave 25. Up to
# this many pages are asked for, while there are more.
SEARCH_PAGES = 3
# Below this many requests left, the anonymous token is renewed, like
# oauth_after_request does for the JSON API.
GQL_LOW_RATE_LIMIT = 60

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Own client: the app's one rewrites Host and Content-Type for
    oauth.reddit.com in its request hook."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=get_httpx_async_transport(proxy=config.PROXY),
            event_hooks={"response": [raise_if_content_is_not_available]},
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _query(operation: str, sha256: str, variables: dict[str, Any]) -> dict:
    """Run one of the app's persisted queries with the anonymous token."""
    response = await _get_client().post(
        GQL_URL,
        json={
            "operationName": operation,
            "variables": variables,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
        },
        headers={
            **_get_login_headers_from_cache(),
            "X-APOLLO-OPERATION-NAME": operation,
            "X-APOLLO-OPERATION-ID": sha256,
            "Accept": "application/json",
        },
    )
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None and float(remaining) < GQL_LOW_RATE_LIMIT:
        logger.info("GraphQL rate-limit remaining is too low, renewing token")
        oauth_login()
    if response.status_code != 200:
        raise RuntimeError(f"{operation} returned {response.status_code}")
    body = response.json()
    if body.get("data") is None:
        raise RuntimeError(f"{operation} returned no data: {body.get('errors')}")
    return body


def _escaped(value: str | None) -> str | None:
    """HTML-escaped like the JSON API sends it (see the module docstring)."""
    return html.escape(value, quote=False) if value else value


def _selftext_html(text: str | None) -> str | None:
    """Search sends the post's text in `content.html` without any markup (the
    bold of the markdown is gone), so it is shown as plain text, escaped
    before building the HTML and then escaped once more like the JSON API."""
    if not text:
        return None
    paragraphs = "".join(
        f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>"
        for part in text.split("\n\n")
        if part.strip()
    )
    return _escaped(f'<div class="md">{paragraphs}</div>')


def _richtext(value: str | None) -> list:
    """Flairs' richtext comes as a JSON string."""
    try:
        parsed = json.loads(value) if value else []
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _flair_fields(prefix: str, flair: dict | None) -> dict[str, Any]:
    """`link_flair_*` or `author_flair_*` fields of the JSON API."""
    flair = flair or {}
    return {
        f"{prefix}_type": flair.get("type", "text"),
        f"{prefix}_richtext": _richtext(flair.get("richtext")),
        f"{prefix}_text": flair.get("text"),
        f"{prefix}_background_color": (flair.get("template") or {}).get(
            "backgroundColor"
        ),
        f"{prefix}_text_color": (flair.get("textColor") or "").lower() or None,
    }


def _timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _thumbnail(node: dict) -> str:
    """The values the JSON API uses for its icons, or the thumbnail's URL."""
    if node.get("isSpoiler"):
        return "spoiler"
    if node.get("isNsfw"):
        return "nsfw"
    subreddit = node.get("subreddit") or {}
    thumbnail = node.get("thumbnail") or {}
    if subreddit.get("isThumbnailsEnabled") is not False and thumbnail.get("url"):
        return _escaped(thumbnail["url"]) or ""
    return "self" if node.get("isSelfPost") else "default"


def post_to_api(node: dict | None) -> dict | None:
    """A GraphQL post (`SubredditPost` or `ProfilePost`) as a "t3" item of
    the JSON API, or None for anything else (ads, deleted posts...)."""
    if not node:
        return None
    if isinstance(node.get("postInfo"), dict):
        node = node["postInfo"]
    if node.get("__typename") not in ("SubredditPost", "ProfilePost"):
        return None
    fullname = node.get("id") or ""
    if not fullname.startswith("t3_"):
        return None

    subreddit = node.get("subreddit") or {}
    profile = node.get("profile") or {}
    content = node.get("content") or {}
    media = node.get("media") or {}
    author = node.get("authorInfo") or {}
    url = node.get("url") or ""
    is_self = node.get("isSelfPost") is True

    data: dict[str, Any] = {
        "id": fullname.removeprefix("t3_"),
        "name": fullname,
        # Search calls it postTitle, other queries title
        "title": _escaped(node.get("postTitle") or node.get("title") or ""),
        "author": author.get("name") or author.get("displayName") or "[deleted]",
        # Posts on a profile have a profile (u_name) instead of a subreddit
        "subreddit": subreddit.get("name") or profile.get("name") or "",
        "permalink": node.get("permalink") or "",
        "url": url,
        "domain": node.get("domain") or "",
        "is_self": is_self,
        "selftext_html": _selftext_html(content.get("html")),
        "score": node.get("score") or 0,
        "num_comments": node.get("commentCount") or 0,
        "created_utc": _timestamp(node.get("createdAt")),
        "over_18": node.get("isNsfw") is True,
        "spoiler": node.get("isSpoiler") is True,
        "stickied": node.get("isStickied") is True,
        "thumbnail": _thumbnail(node),
        **_flair_fields("link_flair", node.get("flair")),
        **_flair_fields("author_flair", node.get("authorFlair")),
    }

    still = (media.get("still") or {}).get("source")
    if still and still.get("url"):
        dimensions = still.get("dimensions") or {}
        data["preview"] = {
            "images": [
                {
                    "source": {
                        "url": _escaped(still["url"]),
                        "width": dimensions.get("width"),
                        "height": dimensions.get("height"),
                    }
                }
            ]
        }
    if media.get("typeHint") == "IMAGE":
        data["post_hint"] = "image"

    streaming = media.get("streaming") or {}
    if streaming.get("dashUrl"):
        dimensions = streaming.get("dimensions") or {}
        data["post_hint"] = "hosted:video"
        data["secure_media"] = {
            "reddit_video": {
                "dash_url": _escaped(streaming["dashUrl"]),
                "width": dimensions.get("width"),
                "height": dimensions.get("height"),
                "is_gif": streaming.get("isGif") is True,
            }
        }

    items = (node.get("gallery") or {}).get("items")
    if items:
        data["gallery_data"] = {
            "items": [
                {
                    "media_id": (item.get("media") or {}).get("id"),
                    "caption": item.get("caption") or "",
                    "outbound_url": item.get("outboundUrl"),
                }
                for item in items
            ]
        }
        data["media_metadata"] = {
            (item.get("media") or {}).get("id"): {
                "s": {
                    "u": (item.get("media") or {}).get("url"),
                    "x": (item.get("media") or {}).get("width"),
                    "y": (item.get("media") or {}).get("height"),
                }
            }
            for item in items
        }

    crosspost = post_to_api((node.get("crosspostRoot") or {}).get("post"))
    if crosspost:
        data["crosspost_parent_list"] = [crosspost["data"]]

    return {"kind": "t3", "data": data}


def _search_variables(text: str, after: str) -> dict[str, Any]:
    options = {
        "page_type": "search_results",
        "pane": "posts",
        "pref_allow_nsfw": "0",
        "is_eligible_for_kwf": "0",
        "after": after,
        "treatment": "default",
    }
    return {
        "query": text,
        # The app keeps one ID for all the pages of the same search
        "queryId": str(uuid.UUID(hashlib.md5(text.encode()).hexdigest())),  # noqa: S324
        "filters": [{"key": "safe_search", "value": "on"}],
        "options": [{"key": k, "value": v} for k, v in options.items()],
        "includeSubredditUnit": True,
    }


async def search_posts(text: str) -> dict:
    """Post search as a JSON API listing."""
    children: list[dict] = []
    seen: set[str] = set()
    after = ""
    for _ in range(SEARCH_PAGES):
        body = await _query(
            "DynamicSearch", DYNAMIC_SEARCH_HASH, _search_variables(text, after)
        )
        main = body["data"]["search"]["dynamic"]["components"]["main"]
        for edge in main.get("edges") or []:
            for result in (edge.get("node") or {}).get("children") or []:
                if result.get("__typename") not in ("SearchPost", "SearchMediaPost"):
                    continue
                item = post_to_api(result.get("post"))
                if item and item["data"]["id"] not in seen:
                    seen.add(item["data"]["id"])
                    children.append(item)
        page = main.get("pageInfo") or {}
        after = page.get("endCursor") or ""
        if not page.get("hasNextPage") or not after:
            break
    return {
        "kind": "Listing",
        "data": {"children": children, "before": None, "after": None},
    }
