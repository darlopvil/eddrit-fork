"""
Atom feeds built by eddrit from Reddit's JSON listings.

eddrit used to pass Reddit's own feeds through (`.rss` on the API, links
rewritten). Since Nov 2026 the API no longer serves them: a subreddit's feed
is an empty one titled "Not Found" (from rss.reddit.com) and a user's feed is
a 404, while the JSON listings keep working. So the feed is made here from the
same listing the page shows: posts, and comments on user pages and threads.

Links point to the instance, thumbnails go through /media when PROXY_MEDIA is
on, and links to other sites through the privacy frontends, as on the pages.
"""

import datetime
import html
from typing import Any
from xml.sax.saxutils import escape, quoteattr

REDDIT_WEB_URL = "https://www.reddit.com"


def _iso(timestamp: float | None) -> str:
    return (
        datetime.datetime.fromtimestamp(timestamp or 0, tz=datetime.UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _to_instance(content: str, instance: str) -> str:
    return content.replace(REDDIT_WEB_URL, instance)


def _media(url: str, instance: str) -> str:
    # Imported here: templates imports the routes, which import this module
    from eddrit.templates import media_url

    proxied = media_url(url)
    return f"{instance}{proxied}" if proxied.startswith("/") else proxied


def _post_content(data: dict[str, Any], link: str, instance: str) -> str:
    from eddrit.utils.frontends import frontend_href

    parts = []
    thumbnail = html.unescape(data.get("thumbnail") or "")
    if thumbnail.startswith("https://"):
        parts.append(
            f'<a href="{escape(link)}"><img src="{escape(_media(thumbnail, instance))}" alt=""></a>'
        )
    if data.get("selftext_html"):
        parts.append(_to_instance(html.unescape(data["selftext_html"]), instance))
    url = html.unescape(data.get("url") or "")
    if url and not data.get("is_self"):
        target = frontend_href(_to_instance(url, instance))
        parts.append(
            f'<p><a href="{escape(target)}">[link]</a> · <a href="{escape(link)}">'
            f"[{data.get('num_comments', 0)} comments]</a></p>"
        )
    return "".join(parts)


def _entry(child: dict[str, Any], instance: str) -> str | None:
    kind, data = child.get("kind"), child.get("data") or {}
    if kind not in ("t3", "t1") or not data.get("permalink"):
        return None
    link = f"{instance}{data['permalink']}"
    if kind == "t3":
        title = html.unescape(data.get("title") or "")
        content = _post_content(data, link, instance)
    else:
        title = f"Comment on {html.unescape(data.get('link_title') or 'a post')}"
        content = _to_instance(html.unescape(data.get("body_html") or ""), instance)
    author = data.get("author") or "[deleted]"
    subreddit = data.get("subreddit") or ""
    return (
        "<entry>"
        f"<id>{escape(link)}</id>"
        f"<title>{escape(title)}</title>"
        f'<link rel="alternate" href={quoteattr(link)} />'
        f"<author><name>/u/{escape(author)}</name>"
        f"<uri>{escape(instance)}/user/{escape(author)}</uri></author>"
        f"<published>{_iso(data.get('created_utc'))}</published>"
        f"<updated>{_iso(data.get('edited') or data.get('created_utc'))}</updated>"
        + (
            f"<category term={quoteattr(subreddit)} label={quoteattr('r/' + subreddit)} />"
            if subreddit
            else ""
        )
        + f'<content type="html">{escape(content)}</content>'
        "</entry>"
    )


def build_feed(
    children: list[dict[str, Any]],
    title: str,
    page_url: str,
    feed_url: str,
    instance: str,
) -> str:
    """An Atom feed with the posts and comments of a JSON listing."""
    entries = [e for e in (_entry(child, instance) for child in children) if e]
    created = [(child.get("data") or {}).get("created_utc") or 0 for child in children]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<id>{escape(feed_url)}</id>"
        f"<title>{escape(title)}</title>"
        f'<link rel="alternate" href={quoteattr(page_url)} />'
        f'<link rel="self" href={quoteattr(feed_url)} type="application/atom+xml" />'
        f"<updated>{_iso(max(created, default=0))}</updated>"
        "<generator>eddrit</generator>" + "".join(entries) + "</feed>"
    )
