# DPoP / sender-constrained tokens

[Cert pinning](pinning.md) blocks capture; you can get past it and mimic works
as normal. DPoP is different: it breaks the replay itself, which is the thing
mimic does.

## Why replay fails

A plain app authenticates every request with the same reusable bundle: a bearer
token, cookies, device ids. Capture it once, replay it, done. That's mimic.

DPoP ([RFC 9449][rfc]) doesn't work that way. Every request carries a fresh
`DPoP:` header, a JWT signed by a private key the client holds, with claims that
tie it to that one request:

| Claim | Ties the proof to | Why a copy fails |
|-------|-------------------|------------------|
| `htm` | HTTP method | reuse on another verb is rejected |
| `htu` | request URL | reuse on another endpoint is rejected |
| `iat` | issue time | server accepts only a few-second window |
| `jti` | unique id | server caches it; single use |
| `nonce` | server-issued value | unpredictable, short-lived (§8) |
| `ath` | the access token | proof is tied to that token |

The access token is bound to the key's thumbprint (`cnf.jkt`), so a stolen token
does nothing without a matching signature on every request. Copying the header
replays nothing.

## The only option: a signing oracle

You usually can't extract the key, but you might borrow the app's ability to use
it. Keep the app running on a device you control, hook its DPoP-signing routine
with Frida, and have it mint a fresh proof for each request you want to send. The
oracle never touches the key; it uses the app to sign. Whether that's even
possible depends on where the key lives:

| Key storage | Extractable? | Replay path |
|-------------|--------------|-------------|
| Secure Enclave (P-256, `kSecAttrTokenIDSecureEnclave`) — recommended, common | No. A jailbreak gives userland root, not enclave access. | Live Frida oracle only, tethered to the running app |
| Keychain software key (lazier apps) | Yes, on a jailbroken device (keychain-dumper, or a Frida `SecItemCopyMatching` hook) | Dump the key, sign proofs offline in Python |

Fingerprint the target first: dump the keychain and check whether the DPoP key is
Secure-Enclave-backed or a plain software key. That answer decides the rest.

## The nonce problem

A working oracle still can't do offline replay if the server uses nonces
(RFC 9449 §8). They're server-issued, opaque, and short-lived, so you can't
pre-mint proofs. Each request turns into a round trip: send, get a
`use_dpop_nonce` error with a fresh nonce, sign with it, resend, with the device
online the whole time. Workable for poking an API by hand, not for detached or
bulk replay.

## Bottom line

- Software key plus a jailbreak: dump it, port the app's proof construction into
  Python, sign offline. Works, minus the nonce round trip.
- Secure Enclave: no extraction. The best you get is a live Frida oracle on a
  tethered jailbroken device, not worth wiring into a general tool.
- No device access: nothing works. There's no network-only path.

No DPoP-specific tooling exists (no Frida script, no mitmproxy addon); you'd build
the oracle from generic Frida hooks. Static header replay, which is what mimic is,
doesn't carry over to DPoP targets.

[rfc]: https://www.rfc-editor.org/rfc/rfc9449
