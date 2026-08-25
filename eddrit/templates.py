import json
from dataclasses import asdict, is_dataclass
from typing import Any

from urllib.parse import quote, urlparse

from starlette.templating import Jinja2Templates

from eddrit import __version__, config, models
from eddrit.routes.pages.media import ALLOWED_HOSTS as ALLOWED_MEDIA_HOSTS
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
def to_json_dataclass(value: Any) -> str:
    if type(value) is list:
        converted = [asdict(item) if is_dataclass(item) else item for item in value]  # type: ignore
    else:
        converted = asdict(value) if is_dataclass(value) else value  # type: ignore
    return json.dumps(converted, default=str)


templates.env.filters["tojson_dataclass"] = to_json_dataclass


# Rewrite Reddit media URLs to go through our /media proxy, so the user's
# browser never talks to Reddit's CDNs. No-op when PROXY_MEDIA is disabled or
# when the URL is not a proxyable Reddit host (e.g. our own static files).
def media_url(value: Any) -> Any:
    if not config.PROXY_MEDIA or not value or not isinstance(value, str):
        return value
    if not value.startswith("https://"):
        return value
    if urlparse(value).hostname not in ALLOWED_MEDIA_HOSTS:
        return value
    return f"/media?url={quote(value, safe='')}"


templates.env.filters["media"] = media_url
