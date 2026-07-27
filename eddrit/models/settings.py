from dataclasses import dataclass
from enum import Enum


class ThumbnailsMode(Enum):
    ALWAYS = "always"
    NEVER = "never"
    SUBREDDIT_PREFERENCE = "subreddit_preference"


class LayoutMode(Enum):
    WIDE = "wide"
    COMPACT = "compact"


class Theme(Enum):
    SYSTEM = None
    LIGHT = "light"
    DARK = "dark"


class LineHeightMode(Enum):
    COMPACT = "compact"
    NORMAL = "normal"
    LARGE = "large"


@dataclass
class Settings:
    layout: LayoutMode
    thumbnails: ThumbnailsMode
    nsfw_popular_all: bool
    nsfw_thumbnails: bool
    theme: Theme
    line_height: LineHeightMode
