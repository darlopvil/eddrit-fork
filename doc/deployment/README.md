# Deployment

## Components

eddrit requires two components:
- the app itself
- a [valkey](https://github.com/valkey-io/valkey) instance (used to store OAuth tokens)

**A proxy is also recommended for public instances, or if your hosting provider’s IP addresses are blocked by Reddit.** A common solution is to use [Cloudflare WARP](https://developers.cloudflare.com/warp-client/) as a proxy, as it has [a proxy mode](https://blog.cloudflare.com/announcing-warp-for-linux-and-proxy-mode/). You then need to configure the app to use it (see the configuration section below).

## Installation

You can run the app through Docker, or directly if you know how to deploy a Python ASGI application.

### Option 1: Docker (recommended)

This fork does not publish images: the images of upstream eddrit do not include its changes.
Build it from a clone of the repository:

```sh
docker build -t eddrit:fork .
```

ℹ️ *Note*: an example `docker-compose.yml` file that uses that image is available [here](./docker-compose.yml).

### Option 2: without Docker

If you know how to deploy a Python ASGI application, you can deploy it directly without Docker.

For example, with [uvicorn](https://uvicorn.org/):
1. Make sure Python >= 3.14 is installed, as it is the minimum version supported by eddrit.
2. Install [Poetry](https://python-poetry.org/), which is used to manage the project’s dependencies.
3. Download or clone the repository and run `poetry install --only main` to install the application.
4. Run the app through uvicorn, for example: `poetry run uvicorn eddrit.app:app`

## Configuration

The application is configured through environment variables. If a `.env` file is present, it will also be read.

Mandatory configuration:
- `VALKEY_URL`: the URL of your [valkey](https://github.com/valkey-io/valkey) instance. Example: `valkeys://my_user:my_password@example.com:15345/1`
- `FORWARDED_ALLOW_IPS` (required when using a reverse proxy such as nginx or traefik in front of the app): a comma-separated list of IP addresses of the proxy, trusted for determining the correct public URL of the instance.
    - Set to `*` to disable IP checking. This is useful when you don’t know the proxy’s IP address in advance, but **this is dangerous** unless you have ensured through other means (e.g. a private network) that only your proxy can reach eddrit and not end clients directly. Otherwise, this makes you vulnerable to IP spoofing attacks.
    - Make sure that your proxy sets the correct `X-Forwarded-For`, `X-Forwarded-Proto` etc. headers.

Optional configuration:
- `DEBUG` (default: `false`): enable [starlette](https://www.starlette.io/) debug mode. Should not be needed outside of development.
- `LOG_LEVEL` (default: `WARNING`): only log messages at or above this level.
- `PROXY` (default: none): if set, requests to Reddit will be sent through the specified proxy.

The fork adds more optional variables (media proxy, logged-in session for comment threads,
privacy frontends...): see the table in the [main README](../../README.md#configuration-added-by-the-fork).
