<div align="center">

<image src="static/images/logo.svg" height="80">

<hr>

A lightweight alternative frontend for Reddit.

![GitHub Tag](https://img.shields.io/github/v/tag/corenting/eddrit?label=latest)
![Build](https://img.shields.io/github/actions/workflow/status/corenting/eddrit/ci.yml?branch=master)
![License](https://img.shields.io/github/license/corenting/eddrit)

<a href="https://eddrit.com"><img src="https://raw.githubusercontent.com/corenting/eddrit/master/doc/screenshots/subreddit.png" width="80%"></a>

</div>

---

## 🍴 Fork notes (darlopvil/eddrit-fork)

This is a fork of [corenting/eddrit](https://github.com/corenting/eddrit) with a few
fixes and features not (yet) in upstream. Built from source and self-hosted.

### Added / fixed here

- **Comment permalinks now render instead of 404.** Reddit appends the comment id as a
  5th path segment (`/r/<sub>/comments/<id>/<slug>/<comment_id>`); upstream had no route
  for it. The full thread is rendered. ([#350](https://github.com/corenting/eddrit/issues/350))
- **Share links (`/r/<sub>/s/<id>`) are resolved.** The web endpoint is WAF-blocked, so
  they're resolved via the app API (`oauth.reddit.com`, 301 → canonical permalink) and
  the user is redirected. ([#351](https://github.com/corenting/eddrit/issues/351))
- **Fixed the proxy bypassing the curl_cffi impersonation.** When `PROXY` was set,
  httpx routed requests through its default transport, dropping the Chrome-Android TLS
  fingerprint (JA4 `t13d1516h2_...` → `t13d1713h1_...`, HTTP/2 → HTTP/1.1). The proxy is
  now passed to the curl_cffi transport itself, keeping the impersonation over the proxy.

### Notes

- Runs behind a VPN egress (Gluetun + Mullvad via `PROXY`) to avoid Reddit's IP-reputation
  WAF block. This is a deployment choice, not a code requirement.
- Kept in sync with upstream; the changes above are additive.

---

> **🌍 Official demo instance**: [eddrit.com](https://eddrit.com)

- Lightweight, with no ads
- Compact design inspired by [old.reddit.com](https://old.reddit.com) rather than the redesign
- Mobile-friendly
- No OAuth2 registration needed for self-hosting: mimics the official Android app by default to bypass rate-limiting
- Basic RSS support for subreddits and posts: fetches the original feed from Reddit and rewrites URLs to point to the current eddrit instance

URLs follow the same structure as Reddit, so you can simply replace `reddit.com` with `eddrit.com` to open any page in eddrit.

## Donations

If you wish to support the app, donations are possible on [GitHub Sponsors](https://github.com/sponsors/corenting/) or [here](https://corenting.fr/donate).

## Deployment (self-hosting)

To setup and configure your own instance, see the [deployment documentation](./doc/deployment/README.md).

## Development

For local development instructions, see the [development documentation](./doc/dev.md).

## Credits

eddrit is inspired by [Nitter](https://github.com/zedeus/nitter), an alternative frontend for Twitter.

- [Bootstrap Icons](https://icons.getbootstrap.com/) for the icons
- [dash.js](https://github.com/Dash-Industry-Forum/dash.js) for DASH video playback
- [Pico.css](https://picocss.com/) as the CSS framework
- [redlib](https://github.com/redlib-org/redlib) for the Android app spoofing code
- [Video.js](https://videojs.com/) for video playback
