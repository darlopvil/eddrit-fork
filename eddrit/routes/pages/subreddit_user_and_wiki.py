from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from eddrit import models
from eddrit.models.link import Link
from eddrit.reddit.fetch import (
    get_post,
    get_post_rss,
    get_subreddit_information,
    get_subreddit_or_user_posts,
    get_subreddit_or_user_rss_feed,
    get_user_information,
    get_wiki_page,
)
from eddrit.routes.common.context import (
    get_canonical_url_context,
    get_posts_pages_common_context,
    get_templates_common_context,
)
from eddrit.routes.common.request import get_instance_scheme_and_netloc
from eddrit.templates import templates

import httpx

from eddrit import config
from eddrit.constants import REDDIT_BASE_API_URL
from eddrit.utils.httpx import get_httpx_async_transport
from eddrit.utils.oauth import _get_login_headers_from_cache


def _redirect_to_age_check(request: Request) -> RedirectResponse:
    return RedirectResponse(url=f"/over18?dest={request.url!s}")


def _should_redirect_to_age_check(request: Request, over18: bool) -> bool:
    return over18 and request.cookies.get("over18", "0") != "1"


def _get_rss_feed_url(request: Request) -> str:
    """
    Get the URL equivalent to the current request but for the RSS feed
    """
    return str(request.url).replace(request.url.path, f"{request.url.path}.rss")


async def subreddit_or_user_post(request: Request) -> Response:
    """
    Endpoint for a post page.
    """
    is_user = request.url.path.startswith("/user")

    # Get information
    if is_user:
        information = await get_user_information(
            request.state.http_client, request.path_params["name"]
        )
    else:
        information = await get_subreddit_information(
            request.state.http_client, request.path_params["name"]
        )

    post_id = request.path_params["post_id"]
    post = await get_post(
        request.state.http_client, request.path_params["name"], post_id, is_user
    )

    if _should_redirect_to_age_check(request, post.over18):
        return _redirect_to_age_check(request)

    return templates.TemplateResponse(
        "post.html",
        {
            "about_information": information,
            "post": post,
            **get_templates_common_context(request),
            **get_canonical_url_context(request),
            "rss_feed_url": _get_rss_feed_url(request),
        },
    )


async def subreddit_or_user_post_rss(request: Request) -> Response:
    """
    Endpoint for a post RSS feed.
    """
    is_user = request.url.path.startswith("/user")
    post_id = request.path_params["post_id"]

    rss_feed = await get_post_rss(
        request.state.http_client,
        request.path_params["name"],
        post_id,
        get_instance_scheme_and_netloc(request),
        is_user,
    )
    return Response(content=rss_feed, media_type="application/atom+xml")


async def wiki_page(request: Request) -> Response:
    """
    Endpoint for a wiki page.
    """
    subreddit_name = request.path_params["name"]
    wiki_page_name = request.path_params.get("page_name", "index")

    wiki_page_item = await get_wiki_page(
        request.state.http_client, subreddit_name, wiki_page_name
    )

    return templates.TemplateResponse(
        "wiki_page.html",
        {
            "wiki_page": wiki_page_item,
            **get_templates_common_context(request),
            **get_canonical_url_context(request),
        },
    )


def _get_request_context_for_subreddit_or_user(
    request: Request,
) -> tuple[
    bool,
    models.SubredditSortingMode | models.UserSortingMode,
    models.SubredditSortingPeriod,
    models.Pagination,
]:
    """Get tuple with request context about subreddit or user request, with the following items:
    1. is_user -> if it's for an user page and not a subreddit
    2. sorting_mode (hot, new etc.)
    3. sorting period (month, all time etc.)
    4. pagination details
    """
    is_user = request.url.path.startswith("/user")

    try:
        # Get sorting mode
        sorting_mode = (
            models.SubredditSortingMode(
                request.path_params.get("sorting_mode", models.SubredditSortingMode.HOT)
            )
            if not is_user
            else models.UserSortingMode(
                request.query_params.get("sort", models.UserSortingMode.NEW)
            )
        )

        # Get sorting period
        sorting_period = models.SubredditSortingPeriod(
            request.query_params.get(
                "t",
                models.SubredditSortingPeriod.DAY
                if not is_user
                else models.SubredditSortingPeriod.ALL,
            )
        )
    except ValueError:
        raise HTTPException(status_code=400)

    # Get pagination
    request_pagination = models.Pagination(
        before_post_id=request.query_params.get("before"),
        after_post_id=request.query_params.get("after"),
    )

    return (is_user, sorting_mode, sorting_period, request_pagination)


async def subreddit_or_user(request: Request) -> Response:
    """
    Endpoint for a subreddit or an user page.
    """
    is_user, sorting_mode, sorting_period, pagination = (
        _get_request_context_for_subreddit_or_user(request)
    )

    # Get information
    if is_user:
        information = await get_user_information(
            request.state.http_client, request.path_params["name"]
        )
    else:
        information = await get_subreddit_information(
            request.state.http_client, request.path_params["name"]
        )

    if _should_redirect_to_age_check(request, information.over18):
        return _redirect_to_age_check(request)

    posts, response_pagination = await get_subreddit_or_user_posts(
        request.state.http_client,
        request.path_params["name"],
        pagination,
        sorting_mode,
        sorting_period,
        is_user,
    )

    links: list[Link] = []

    # Add "Add to favorites" link for subreddits
    if not is_user:
        links.append(
            Link(
                name="Add to favorites",
                target=f"onFavoriteButtonClick('{request.path_params['name']}');",
                is_javascript_target=True,
                html_id="favorite-link",
                html_data=request.path_params["name"],
            )
        )

    # Add wiki link if it's a subreddit with wiki enabled
    if not is_user and information.wiki_enabled:  # type: ignore
        links.append(
            Link(
                name="Wiki",
                target=f"/r/{request.path_params['name']}/wiki",
                is_javascript_target=False,
            )
        )

    return templates.TemplateResponse(
        "posts_list.html",
        {
            "links": links,
            **get_posts_pages_common_context(
                response_pagination,
                posts,
                information,
                sorting_mode,
                sorting_period,
            ),
            "rss_feed_url": _get_rss_feed_url(request),
            **get_templates_common_context(request),
            **get_canonical_url_context(request),
        },
    )


async def subreddit_or_user_rss(request: Request) -> Response:
    is_user, sorting_mode, sorting_period, pagination = (
        _get_request_context_for_subreddit_or_user(request)
    )

    rss_feed = await get_subreddit_or_user_rss_feed(
        request.state.http_client,
        request.path_params["name"],
        pagination,
        sorting_mode,
        sorting_period,
        is_user,
        get_instance_scheme_and_netloc(request),
    )

    return Response(content=rss_feed, media_type="application/atom+xml")


async def share_link(request: Request) -> Response:
    """
    Resolve a Reddit share link (/r/<sub>/s/<id>) to its canonical permalink.

    The web endpoint is WAF-blocked (403), but the app API resolves the share id
    via a 301 whose Location is the canonical URL when authenticated with the app
    bearer token. We use an ephemeral client (own transport + proxy, no shared
    event hooks) so the shared client's JSON-parsing response hook does not choke
    on the 301 HTML body. Cached login headers are reused: no extra token mint.
    """
    name = request.path_params["name"]
    share_id = request.path_params["share_id"]
    url = f"{REDDIT_BASE_API_URL}/r/{name}/s/{share_id}"

    async with httpx.AsyncClient(
        http2=True,
        transport=get_httpx_async_transport(),
        proxy=config.PROXY,
        follow_redirects=False,
        timeout=20,
    ) as client:
        res = await client.get(url, headers=_get_login_headers_from_cache())

    location = res.headers.get("location")
    if res.status_code not in (301, 302, 307, 308) or not location:
        raise HTTPException(status_code=404)

    # Keep only the path (drops the reddit host and share/utm tracking query).
    return RedirectResponse(url=httpx.URL(location).path)


routes = [
    # Wiki (register with trailing slash too: the permalinks given by reddit for these URLs have a trailing slash
    # so we want to avoid an extra redirect)
    Route("/{name:str}/wiki", endpoint=wiki_page),
    Route("/{name:str}/wiki/", endpoint=wiki_page),
    Route("/{name:str}/wiki/{page_name:str}", endpoint=wiki_page),
    # Subreddit or user
    Route("/{name:str}.rss", endpoint=subreddit_or_user_rss),
    Route("/{name:str}/.rss", endpoint=subreddit_or_user_rss),
    Route("/{name:str}", endpoint=subreddit_or_user),
    # Subreddit or user (with sorting mode)
    Route("/{name:str}/{sorting_mode:str}.rss", endpoint=subreddit_or_user_rss),
    Route("/{name:str}/{sorting_mode:str}/.rss", endpoint=subreddit_or_user_rss),
    Route("/{name:str}/{sorting_mode:str}", endpoint=subreddit_or_user),
    # Post
    Route(
        "/{name:str}/comments/{post_id:str}/{post_title:str}.rss",
        endpoint=subreddit_or_user_post_rss,
    ),
    Route(
        "/{name:str}/comments/{post_id:str}/{post_title:str}/.rss",
        endpoint=subreddit_or_user_post_rss,
    ),
    Route(
        "/{name:str}/comments/{post_id:str}/{post_title:str}",
        endpoint=subreddit_or_user_post,
    ),
    Route(
        "/{name:str}/comments/{post_id:str}/{post_title:str}/",
        endpoint=subreddit_or_user_post,
    ),
    # Post - comment permalink: Reddit anade el id del comentario como 5o
    # segmento. eddrit no tiene vista de comentario individual, asi que
    # renderizamos el hilo completo en vez de dar 404. El handler de post
    # ignora post_title/comment_id.
    Route(
        "/{name:str}/comments/{post_id:str}/{post_title:str}/{comment_id:str}",
        endpoint=subreddit_or_user_post,
    ),
    Route(
        "/{name:str}/comments/{post_id:str}/{post_title:str}/{comment_id:str}/",
        endpoint=subreddit_or_user_post,
    ),
    # Share link resolver: /r/<sub>/s/<id> -> canonical permalink via the app API.
    Route("/{name:str}/s/{share_id:str}", endpoint=share_link),
    Route("/{name:str}/s/{share_id:str}/", endpoint=share_link),
]
