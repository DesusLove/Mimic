"""Read captured traffic from a running mitmweb instance.

mitmweb exposes a JSON API on http://127.0.0.1:8081. Auth is a token passed
once as a query param, which sets a session cookie; subsequent requests reuse
the cookie. This module pulls the raw flows and normalizes them into a shape
that both the runtime Session and the AI codegen step can consume.
"""
import json
import os

import requests

DEFAULT_URL = os.environ.get("MITM_URL", "http://127.0.0.1:8081")
DEFAULT_TOKEN = os.environ.get("MITM_TOKEN", "test")


class MitmError(RuntimeError):
    pass


class Mitm:
    """A thin client over a running mitmweb's flow API."""

    def __init__(self, url=DEFAULT_URL, token=DEFAULT_TOKEN):
        self.url = url.rstrip("/")
        self.token = token
        self._http = requests.Session()

    def _auth(self):
        try:
            self._http.get(f"{self.url}/", params={"token": self.token}, timeout=5)
        except requests.RequestException as e:
            raise MitmError(
                f"can't reach mitmweb at {self.url} — is it running? "
                f"start it with `mitmweb` (original error: {e})"
            )

    def flows(self):
        """All captured flows as a list of dicts (mitmweb's own schema)."""
        self._auth()
        r = self._http.get(f"{self.url}/flows", timeout=15)
        if r.status_code != 200:
            raise MitmError(f"mitmweb /flows returned {r.status_code}")
        return r.json()

    def body(self, flow_id, side):
        """Raw request/response body bytes for a flow. side is 'request' or 'response'."""
        r = self._http.get(
            f"{self.url}/flows/{flow_id}/{side}/content.data", timeout=15
        )
        return r.content


def _headers_dict(req):
    return {k: v for k, v in req.get("headers", [])}


def hosts(flows):
    """Count requests per host, most frequent first."""
    counts = {}
    for f in flows:
        req = f.get("request")
        if req:
            counts[req["host"]] = counts.get(req["host"], 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def endpoints(mitm, flows, host):
    """Distinct (method, path) endpoints for a host, with a sample req/resp body.

    Returns a list of dicts ready to feed to the AI. Latest capture of each
    endpoint wins, so bodies reflect the most recent real call.
    """
    by_key = {}
    for f in flows:
        req = f.get("request")
        if not req or req["host"] != host:
            continue
        by_key[(req["method"], req["path"].split("?")[0])] = f

    out = []
    for (method, path), f in by_key.items():
        req = f.get("request") or {}
        resp = f.get("response") or {}
        out.append(
            {
                "method": method,
                "path": path,
                "status": resp.get("status_code"),
                "query": req["path"].split("?", 1)[1] if "?" in req["path"] else "",
                "request_body": _decode(mitm.body(f["id"], "request")),
                "response_body": _decode(mitm.body(f["id"], "response")),
            }
        )
    return out


def _decode(raw, limit=4000):
    """Best-effort decode of a body to a truncated JSON/text string for the AI."""
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"<{len(raw)} bytes binary>"
    try:  # pretty-print + truncate JSON so the AI sees structure, not noise
        text = json.dumps(json.loads(text), indent=2)
    except ValueError:
        pass
    return text[:limit] + ("\n…(truncated)" if len(text) > limit else "")
