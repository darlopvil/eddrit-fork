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


def _absolute(content: str, instance: str) -> str:
    """Feed readers have no page to resolve relative links against."""
    content = _to_instance(content, instance)
    return content.replace('href="/', f'href="{instance}/').replace(
        'src="/', f'src="{instance}/'
    )


def _img(url: str, link: str, instance: str) -> str:
    return (
        f'<p><a href="{escape(link)}"><img src="{escape(_media(url, instance))}" '
        'alt="" style="max-width:100%"></a></p>'
    )


def _video(video: Any, link: str, instance: str) -> str:
    """Readers can play an MP4 or a GIF, not Reddit's DASH: those get their
    poster and a link to the post."""
    from eddrit.models import PostVideoFormat

    if video.video_format == PostVideoFormat.MP4 or video.is_gif:
        poster = (
            f' poster="{escape(_media(video.poster_url, instance))}"'
            if video.poster_url
            else ""
        )
        loop = " autoplay loop muted" if video.is_gif else " controls"
        return (
            f'<p><video src="{escape(_media(video.url, instance))}"{poster}{loop} '
            'style="max-width:100%"></video></p>'
        )
    poster = _img(video.poster_url, link, instance) if video.poster_url else ""
    return f'{poster}<p><a href="{escape(link)}">▶ Watch the video on eddrit</a></p>'


def _post_content(data: dict[str, Any], link: str, instance: str) -> str:
    """The post as the page shows it: picture, gallery, video, embed or link
    card, then its text. Parsed with eddrit's own parser."""
    from eddrit.models import PostContentType
    from eddrit.reddit import parser
    from eddrit.utils.frontends import embed_target, frontend_href

    post = parser.parse_post(data)
    content = post.content
    parts = [
        (
            f"<p>r/{escape(post.subreddit)} · {escape(post.human_score)} points · "
            f'<a href="{escape(link)}">{post.comments_count} comments</a></p>'
        )
    ]
    target = frontend_href(_to_instance(post.url, instance))

    if content.type == PostContentType.PICTURE:
        parts.append(_img(content.picture.url, link, instance))
    elif content.type == PostContentType.GALLERY:
        for item, caption in zip(content.contents, content.captions, strict=False):
            if hasattr(item, "is_gif"):
                parts.append(_video(item, link, instance))
            else:
                parts.append(_img(item.url, link, instance))
            if caption:
                parts.append(f"<p><em>{escape(caption)}</em></p>")
    elif content.type == PostContentType.VIDEO:
        parts.extend(_video(video, link, instance) for video in content.videos[:1])
    elif content.type == PostContentType.EMBED:
        if content.poster_url:
            parts.append(_img(content.poster_url, link, instance))
        embed_href = embed_target(post.url)["href"]
        parts.append(
            f'<p><a href="{escape(embed_href)}">▶ {escape(post.domain)}</a></p>'
        )
    elif content.type == PostContentType.LINK:
        if not post.thumbnail_is_icon and post.thumbnail_url.startswith("https://"):
            parts.append(_img(post.thumbnail_url, target, instance))
        parts.append(f'<p><a href="{escape(target)}">🔗 {escape(post.domain)}</a></p>')

    if content.text:
        parts.append(_absolute(content.text, instance))
    return "".join(parts)


def _comment_content(data: dict[str, Any], instance: str) -> str:
    """The comment, after a line saying where it was written."""
    subreddit = data.get("subreddit") or ""
    post_link = data.get("link_permalink") or ""
    title = html.unescape(data.get("link_title") or "")
    where = f"in r/{escape(subreddit)}"
    if post_link and title:
        where += f', on <a href="{escape(_absolute(post_link, instance))}">{escape(title)}</a>'
    body = _absolute(html.unescape(data.get("body_html") or ""), instance)
    return f"<p>{where}</p>{body}"


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
        content = _comment_content(data, instance)
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
