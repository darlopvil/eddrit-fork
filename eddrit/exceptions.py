class RedditContentUnavailableError(Exception):
    """
    Raised when reddit content is not available.
    """

    detail = "Content is not available"

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class UserUnavailableError(Exception):
    """
    Raised when an user is not available.
    """

    detail = "User is not available"


class UserSuspendedError(RedditContentUnavailableError):
    detail = "User suspended"


class UserBlockedError(RedditContentUnavailableError):
    detail = "User blocked"


class UserNotFoundError(RedditContentUnavailableError):
    detail = "User not found"


class SubredditNotFoundError(RedditContentUnavailableError):
    detail = "Subreddit not found"


class ContentCannotBeViewedError(RedditContentUnavailableError):
    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(status_code)
        self.detail = f"Content cannot be viewed ({reason})"


class WikiPageNotFoundError(RedditContentUnavailableError):
    detail = "Wiki page not found"


class RedditSessionRejectedError(RedditContentUnavailableError):
    """
    Raised when Reddit blocks a thread request made with REDDIT_SESSION.
    """

    detail = "Reddit rejected the logged-in session used for comment threads and search (REDDIT_SESSION): the cookie has expired, or Reddit blocks the browser fingerprint (REDDIT_SESSION_IMPERSONATE)"

    def __init__(self) -> None:
        super().__init__(status_code=503)


class RedditSessionRateLimitedError(RedditContentUnavailableError):
    """
    Raised when the logged-in session used for threads reaches Reddit's limit,
    or is paused by eddrit because the limit is close.
    """

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__(status_code=429)
        minutes = max(1, round(retry_after / 60)) if retry_after else None
        self.detail = (
            "The logged-in session used for comment threads and search is at Reddit's limit "
            "(100 requests every 10 minutes) and is paused to protect the account. "
            + (
                f"Try again in {minutes} min"
                if minutes
                else "Try again in a few minutes"
            )
        )


class RateLimitedError(RedditContentUnavailableError):
    """
    Raised when rate-limit is reached
    """

    detail = "eddrit made too much requests to Reddit and was rate-limited. Try again later or on another instance"

    def __init__(self) -> None:
        super().__init__(status_code=429)
