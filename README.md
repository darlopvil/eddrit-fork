<div align="center">

<image src="static/images/logo.svg" height="80">

<hr>

A lightweight alternative frontend for Reddit.

![License](https://img.shields.io/github/license/darlopvil/eddrit-fork)

<img src="doc/screenshots/subreddit.png" width="80%">

</div>

---

## 🍴 Fork notes (darlopvil/eddrit-fork)

This is a fork of [corenting/eddrit](https://github.com/corenting/eddrit) with fixes and
features of its own, maintained independently. Built from source and self-hosted. Changes
that need configuration are opt-in: with none of the variables below set, those features
stay off.

### Post pages and comment threads

- **Comment threads with a logged-in session (`REDDIT_SESSION`).** Since 20 Oct 2026 Reddit
  blocks every comments route for anonymous tokens (403 "blocked by network security") while
  listings keep working, so post pages fail. Set `REDDIT_SESSION` to the value of the
  `reddit_session` cookie of a logged-in **throwaway** account and thread requests go to
  `www.reddit.com` with it. Everything else stays anonymous, so the account only sees which
  threads are opened.
- **Firefox fingerprint for the session.** Since 1 Nov 2026 Reddit blocks Chrome TLS
  fingerprints from datacenter IPs on `www`, even with a valid session. Session requests use
  `REDDIT_SESSION_IMPERSONATE` (default `firefox147`); anonymous requests are unchanged.
- **The account is never used up.** Reddit gives a session 100 requests every 10 minutes.
  Threads are cached in Valkey for 5 minutes (`THREAD_CACHE_SECONDS`), so reloading or going
  back costs nothing, and the session is paused when Reddit reports fewer than 20 requests
  left in its window. While paused, the page says when to try again instead of failing.
- **Clear errors.** A rejected session (expired cookie or blocked fingerprint) gives a 503
  that says so, and a session at its limit gives a 429 with the waiting time, instead of a 500.
- **Comment permalinks render the thread** instead of a 404. ([#350](https://github.com/corenting/eddrit/issues/350))
- **A post page no longer fails when Reddit does not answer for its subreddit** (or user):
  the post is fetched first and the sidebar falls back to defaults.

### Search

- **Search keeps working.** Since 11 Nov 2026 Reddit refuses post search to the anonymous
  token on its JSON API (every host), and its website refuses every search to sessions.
  Post search now tries, in order:
  1. The JSON API with the anonymous token, as before. Once Reddit blocks it, it is skipped
     for 30 minutes, and used again by itself the day Reddit lifts the block.
  2. Reddit's GraphQL API (the Android app's search query) with the same anonymous token.
     It has its own quota (about 550 requests every 10 minutes) and doesn't touch the
     account. Results are converted to the JSON API's format.
  3. With `REDDIT_SESSION` set, the JSON API with a token minted for the session the way
     the Android app does after logging in. It shares the account's quota and pause with
     threads.
     
### Links

- **Share links are resolved** via the app API: `/r/<sub>/s/<id>` and, since Nov 2026,
  links shared from a profile (`/user/<name>/s/<id>`), which used to land on a missing
  subreddit. ([#351](https://github.com/corenting/eddrit/issues/351))
- **A subreddit that does not exist** shows "Subreddit not found" (404) instead of a server
  error.
- **No links to Reddit by accident.** Post titles and images that pointed at `i.redd.it`,
  `v.redd.it` or `reddit.com/gallery` open through the instance. The post page has an
  explicit "open on Reddit" link, tagged `?noredirect=1` so redirect rules can let it through.

### Media

- **Media proxy (`/media`).** Images, flair emojis, MP4 and DASH video are fetched
  server-side and streamed to the browser, so the client never talks to Reddit's CDNs. Host
  whitelist, `Range` support, connection pooling and an on-disk cache. Saved images are named
  after the post title instead of `media`. Behind `PROXY_MEDIA`.

### Third-party content

- **Privacy frontends for embedded content.** Optional `INVIDIOUS_URL`, `NITTER_URL`,
  `PROXITOK_URL`, `RIMGO_URL`, `PRIVIBLUR_URL` and `SOPRANO_URL`. Links to YouTube, X,
  TikTok, Imgur, Tumblr and Tenor in titles, posts and comments point to them, and videos,
  tweets, TikToks and Tumblr posts are embedded from them. Embeds never load the original
  site's player: with no frontend set they become a plain link, so opening a post with a
  YouTube video no longer pulls in Google's trackers.

### Embed view

- **`/r/<sub>/comments/<id>/<slug>/embed`** (and `/user/...`): a compact post view meant to
  be loaded in an `<iframe>` by other sites. No header or comments, media through `/media`,
  NSFW and spoiler content behind a click instead of the `/over18` redirect, and errors shown
  inline. `?theme=light|dark` forces the theme, since the host page doesn't send eddrit's
  cookies. Your reverse proxy must not add `X-Frame-Options` to these routes.

### Reliability

- **Fixed the proxy bypassing the curl_cffi impersonation** (JA4 was falling back to plain
  httpx).
- **Fresh connection when Reddit blocks the current one.** Reddit sometimes flags a single
  long-lived connection; on a 403 "blocked by network security" to a GET, eddrit retries once
  on a new connection.
- **HTTP timeout raised** so a reconnect of the outgoing proxy doesn't take the instance down.

### Configuration added by the fork

| Variable | Default | |
|---|---|---|
| `PROXY_MEDIA` | `false` | Serve media through `/media` |
| `MEDIA_CACHE_ENABLED` | `false` | On-disk cache for `/media` |
| `MEDIA_CACHE_DIR` | `/media-cache` | Cache directory |
| `MEDIA_CACHE_MAX_MB` | `5120` | Cache size limit |
| `MEDIA_CACHE_DAYS` | `7` | Cache age limit |
| `REDDIT_SESSION` | empty | `reddit_session` cookie of a throwaway account, for comment threads and search |
| `REDDIT_SESSION_IMPERSONATE` | `firefox147` | Browser fingerprint for session requests |
| `THREAD_CACHE_SECONDS` | `300` | Thread and search cache in Valkey (`0` disables it) |
| `INVIDIOUS_URL`, `NITTER_URL`, `PROXITOK_URL`, `RIMGO_URL`, `PRIVIBLUR_URL`, `SOPRANO_URL` | empty | Privacy frontends for third-party content |

### Notes

- Reddit blocks many datacenter IP ranges. `PROXY` lets eddrit go out through an HTTP proxy
  (for example a VPN container); that is a deployment choice, not a code requirement.
- The footer and the About page describe this fork.
- Maintained independently: upstream changes are not merged automatically, and this fork
  takes care of its own maintenance.

---

- Lightweight, with no ads
- Compact design inspired by [old.reddit.com](https://old.reddit.com) rather than the redesign
- Mobile-friendly
- No OAuth2 registration needed for self-hosting: mimics the official Android app by default to bypass rate-limiting
- Basic RSS support for subreddits and posts: fetches the original feed from Reddit and rewrites URLs to point to the current eddrit instance

URLs follow the same structure as Reddit, so you can simply replace `reddit.com` with your instance's domain to open any page in eddrit.

## Deployment (self-hosting)

To setup and configure your own instance, see the [deployment documentation](./doc/deployment/README.md).

## Development

For local development instructions, see the [development documentation](./doc/dev.md).

## Credits

eddrit is made by [corenting](https://github.com/corenting/eddrit) and released under the MIT
license; this fork keeps that license and its copyright notice (see [LICENSE](./LICENSE)).
eddrit is inspired by [Nitter](https://github.com/zedeus/nitter), an alternative frontend for Twitter.

- [Bootstrap Icons](https://icons.getbootstrap.com/) for the icons
- [dash.js](https://github.com/Dash-Industry-Forum/dash.js) for DASH video playback
- [Pico.css](https://picocss.com/) as the CSS framework
- [redlib](https://github.com/redlib-org/redlib) for the Android app spoofing code
- [Video.js](https://videojs.com/) for video playback
