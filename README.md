<div align="center">

# mimic 🪞

**Intercept any app, then call it from Python like a library.**

*Capture traffic once — an AI writes the client for you.*

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

```python
from hinge_client import Hinge

acc = Hinge()                 # reuses your captured session
recs = acc.get_recommendations()
acc.like(subject_id, comment="hi lol")
```

You don't write `hinge_client.py`. **mimic** captures your own app traffic
through a proxy, and an AI (Claude or opencode) generates the client from the
endpoints it saw on the wire.

---

## Table of contents

- [How it works](#how-it-works)
- [Install](#install)
- [Quick start (iPhone)](#quick-start-iphone)
- [CLI reference](#cli-reference)
- [Capture backends](#capture-backends)
- [The library](#the-library)
- [Advanced: cert pinning](#advanced-cert-pinning)
- [Limitations](#limitations)
- [Ethics](#ethics)
- [License](#license)

## How it works

Most apps authenticate every request with the same bundle of values: a bearer
token, some device ids, a session id, cookies. They're stable across calls.
Capture them once from a real request you made, and you can replay them on new
requests to the same API.

```
┌──────────────┐     ┌───────────────┐     ┌───────────────────────┐
│ capture      │ --> │ extract auth  │ --> │ generate client       │
│ (mitmproxy / │     │ (mimic gets   │     │ (claude or opencode   │
│  HAR / cURL) │     │  headers +    │     │  reads the captured   │
│              │     │  cookies)     │     │  endpoints and writes │
│              │     │               │     │  a Python class)      │
└──────────────┘     └───────────────┘     └───────────────────────┘
```

The generated client is plain Python on top of `mimic.App`, and you edit it
like any other file. It gives you:

- **Named methods** — `get_recommendations()`, not `POST /rec/v2`
- **Body templates** — request payloads pre-filled from real captured bodies
- **Call chaining** — the multi-step flows mobile APIs tend to need
  (fetch a token in one call, spend it in the next)
- **Automatic auth** — headers are pulled from your captured session, never
  hardcoded

## Install

```bash
sh install.sh
```

Installs [`uv`](https://astral.sh/uv) if you don't have it, then mimic in an
isolated tool env. mitmproxy isn't a separate install; mimic launches it via
`uvx` on first `record`.

Prefer to do it manually?

```bash
uv tool install mimic-client
```

Then confirm everything is ready:

```bash
mimic doctor                    # check proxy, AI generator, optional tools
```

## Quick start (iPhone)

Start the proxy:

```bash
mimic record                    # prints your Mac's LAN IP + these steps
```

`record` fills in your Mac's LAN IP and walks you through it:

1. **iPhone → Wi-Fi → Configure Proxy → Manual** → `<your-mac-ip>:8080`
2. **Safari** → `http://mitm.it` → download and install the Apple profile
3. **Settings → General → About → Certificate Trust Settings** → turn on full
   trust for mitmproxy. ⚠️ *This step is easy to miss and nothing works
   without it.*
4. Open the target app and use it normally.

While capturing, you can watch requests live at
[http://127.0.0.1:8081](http://127.0.0.1:8081) (mitmweb dashboard).

Then generate the client:

```bash
mimic hosts                        # list captured hosts; pick your API host
mimic learn prod-api.hingeaws.net  # see the endpoints mimic saw
mimic gen   prod-api.hingeaws.net  # generate hinge_client.py
```

And use it:

```python
from hinge_client import Hinge
Hinge().get_recommendations()
```

## CLI reference

| Command | What it does |
|---------|--------------|
| `mimic record` | Start the mitmproxy proxy + print the iPhone setup steps |
| `mimic doctor` | Check your setup (proxy, AI generator, optional tools) |
| `mimic hosts [--har FILE]` | List captured hosts |
| `mimic learn <host> [--har FILE]` | Show the endpoints mimic saw for a host |
| `mimic gen <host> [-o OUT] [--model MODEL] [--generator claude\|opencode] [--prompt-only] [--har FILE]` | AI-generate a Python client for a host |
| `mimic unpin <ipa\|bundle-id>` | Defeat cert pinning via Frida so capture works |

Tips:

- `mimic gen --prompt-only` prints the AI prompt instead of calling the
  generator — useful if you'd rather paste it into your own assistant.
- `--har` on `hosts` / `learn` / `gen` reads from a HAR file instead of the
  live proxy (see [Capture backends](#capture-backends)).

## Capture backends

| Backend | Best for | Setup |
|---------|----------|-------|
| **mitmproxy** | iOS / Android apps (default) | Proxy + trusted CA cert; mimic runs mitmproxy via `uvx` for you |
| **HAR file** | Web apps, anything in a browser | Devtools → Network → "Save all as HAR". No proxy, no cert |
| **cURL paste** | One-off requests with a web version | Devtools → "Copy as cURL". No proxy, no cert |

**HAR example:**

```bash
mimic hosts api.example.com --har traffic.har
mimic gen   api.example.com --har traffic.har
```

**cURL example:**

```python
from mimic import Session
s = Session.from_curl(open("copied.txt").read())
```


## The library

If you don't want codegen, build a session by hand. There are four
constructors:

```python
from mimic import Session

Session.from_mitm("prod-api.hingeaws.net")          # pull auth from mitmweb
Session.from_curl(open("copied.txt").read())        # paste "Copy as cURL" from devtools
Session.from_har("traffic.har", "api.example.com")  # load a browser HAR export
Session(base_url="https://x.com", headers={...})    # fully explicit
```

`.get(path)`, `.post(path, json=...)`, and the other common HTTP verb helpers
return parsed JSON and raise `requests.HTTPError` for failed responses.

**Token refresh** — if your token rotates, a `401` on an idempotent request
(GET, PUT, DELETE, …) triggers one re-pull from mitmweb and a retry.
Non-idempotent requests (e.g. `POST`) are **not** retried unless you
explicitly pass `refresh=True`, so a like can't accidentally fire twice.

**Your own client** — subclass `mimic.App` and add named methods:

```python
from mimic import App

class Hinge(App):
    HOST = "prod-api.hingeaws.net"

    def get_recommendations(self):
        return self.post("/rec/v2", {"playerId": self.player_id})
```

`Hinge()` then auto-pulls your captured auth from mitmweb — no tokens
hardcoded anywhere.

## Advanced: cert pinning

Some apps (banking, Instagram) **pin** their certificate — the app rejects the
mitmproxy cert, so the proxy sees no traffic and nothing shows up in
`mimic hosts`. Pinning blocks *capture*, not replay: get past the pin and the
rest works normally.

`mimic unpin <ipa|bundle-id>` sets up a [Frida](https://frida.re)-based
bypass, with two paths:

- **Jailbroken device** — attach hooks over USB, no repackaging.
- **Stock device** — inject the Frida gadget into a decrypted IPA, re-sign,
  and sideload.

Full details, prerequisites, and the catches: [docs/pinning.md](docs/pinning.md).

## Limitations

- **Certificate pinning** — blocks capture (see above); workable with
  `mimic unpin`.
- **DPoP / sender-constrained tokens**
  ([RFC 9449](https://www.rfc-editor.org/rfc/rfc9449)) — each request carries
  a fresh proof signed by a private key that never leaves the device, so
  captured requests don't replay. This defeats the core model, not just
  capture; there's no clean workaround. See [docs/dpop.md](docs/dpop.md) for
  a deep dive.
- **Hardware attestation** (App Attest, DeviceCheck) — out of scope.
- **Flutter apps** — ship their own TLS stack and ignore the system proxy;
  they need [reFlutter](https://github.com/Impact-I/reFlutter), which
  `mimic unpin` doesn't handle.

> **Rule of thumb:** if `mimic hosts` shows the app's API host, you're good.

## Ethics

Use it on **your own accounts and data**. It replays *your* session; it is not
a tool for accessing anyone else's. Reverse-engineering and automating private
APIs may violate each app's terms of service — you are responsible for
complying with them.

## License

MIT, see [LICENSE](LICENSE). Provided as-is, no warranty. Use on your own
accounts and data; you are responsible for complying with each app's terms.
