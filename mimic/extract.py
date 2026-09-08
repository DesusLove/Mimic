"""Pull the reusable part of your captured session out of mitm flows.

The whole trick behind mimic: an app authenticates each request with a bundle
of headers (or cookies) — a bearer token, device ids, a session id. Those are
stable across calls. Capture them once from a real request you made, and you
can craft new calls that the server can't tell apart from your real client.

This module finds the newest authenticated request to a host and keeps only the
headers worth reusing.
"""

# Header names that carry identity/auth/device context worth replaying.
# Anything matching these prefixes or exact names is kept.
KEEP_EXACT = {
    "authorization",
    "cookie",
    "user-agent",
    "accept-language",
    "content-type",
    "accept",
}
KEEP_PREFIX = ("x-",)  # x-device-id, x-session-id, x-app-version, x-api-key, …

# Per-request noise that must NOT be replayed — the HTTP layer sets these.
DROP = {
    "content-length",
    "host",
    "connection",
    "accept-encoding",
    "content-encoding",
    "priority",
    "x-forwarded-for",
}


def _keep(name):
    n = name.lower()
    if n in DROP:
        return False
    return n in KEEP_EXACT or n.startswith(KEEP_PREFIX)


def session_headers(flows, host):
    """Reusable auth/device headers from your newest authed request to `host`.

    "Authed" = has an Authorization or Cookie header. Returns {} if none found.
    """
    for f in reversed(flows):
        req = f.get("request")
        if not req or req["host"] != host:
            continue
        names = {k.lower() for k, _ in req.get("headers", [])}
        if not ({"authorization", "cookie"} & names):
            continue
        return {k: v for k, v in req["headers"] if _keep(k)}
    return {}


def base_url(flows, host):
    """Reconstruct the scheme://host base from a captured request."""
    for f in flows:
        req = f.get("request")
        if req and req["host"] == host:
            scheme = req.get("scheme", "https")
            port = req.get("port")
            netloc = host
            if port and port not in (80, 443):
                netloc = f"{host}:{port}"
            return f"{scheme}://{netloc}"
    return f"https://{host}"
