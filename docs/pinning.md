# Certificate pinning

mimic captures traffic through a proxy (mitmproxy). A pinned app rejects the
proxy's cert, so the proxy sees nothing and there's nothing to capture. This
page covers getting past that.

## What pinning does

Normal TLS authenticates the server to the client: the app checks that the
server's cert is signed by a trusted CA. To MITM it, you add your own CA to the
device trust store and the proxy presents a cert signed by that CA. Pinning
hardcodes the exact cert (or public-key hash) the app will accept, so a proxy
cert signed by any other CA, including one you control, gets rejected.

So two things are true:

Pinning stops capture, not replay. It only makes traffic hard to observe. Once
you can see a request, the auth bundle in it replays the same as it would for an
unpinned app. Pinning adds no replay resistance; that's what DPoP is for (see
[dpop.md](dpop.md)).

You can't beat it by handing the app a cert. The pinned value is a public cert,
and presenting it in a handshake needs the matching private key, which sits on
the server. The fix is on the app side: stop the app from checking, by hooking
its TLS verification at runtime with [Frida](https://frida.re).

mimic doesn't reimplement those hooks; they change across iOS versions and TLS
stacks. `mimic unpin` drives the upstream that maintains them,
[httptoolkit/frida-interception-and-unpinning][ht], and handles the mimic-specific
part: baking your mitmproxy CA and proxy address into the scripts and printing
the command to run.

## Prerequisites

```bash
mimic doctor        # see the "optional — only for mimic unpin" section
```

- git, to fetch the unpinning scripts.
- frida, to run the hooks (`pipx install frida-tools`).
- objection, for the no-jailbreak path only (`pipx install objection`).
- A mitmproxy CA at `~/.mitmproxy/mitmproxy-ca-cert.pem`. Running `mimic record`
  once generates it.

## Path A: jailbroken device

The phone runs `frida-server` and you attach over USB. No repackaging, no
signing.

```bash
mimic record                       # one terminal — starts the proxy
mimic unpin com.example.app        # bundle id → prints a frida command
```

`unpin` fetches the scripts, bakes in your CA and this Mac's LAN IP:8080, and
prints:

```bash
frida -U \
    -l mimic-unpin/frida-scripts/config.js \
    -l mimic-unpin/frida-scripts/ios/ios-connect-hook.js \
    -l mimic-unpin/frida-scripts/ios/ios-disable-detection.js \
    -l mimic-unpin/frida-scripts/native-tls-hook.js \
    -l mimic-unpin/frida-scripts/native-connect-hook.js \
    -f com.example.app
```

Run it, use the app, then `mimic hosts` and `mimic gen <host>` as usual.

## Path B: stock device (Frida gadget)

No jailbreak. You inject the Frida gadget into a decrypted IPA, re-sign, and
sideload.

```bash
mimic unpin ./MyApp.ipa --codesign <TEAM_ID>
```

`unpin` bakes the config, runs `objection patchipa` to inject the gadget, and
writes `mimic-unpin/MyApp-patched.ipa`. It stops there and prints the signing
and install step, since that's what tools like Sideloadly handle:

- drag the patched IPA into [Sideloadly](https://sideloadly.io), or
- `pymobiledevice3 apps install mimic-unpin/MyApp-patched.ipa`

Then attach the scripts to the gadget (`unpin` prints the `frida -U` command) and
capture as usual.

### The catches

- You need a decrypted IPA. Your own app build is fine. An App Store app is
  FairPlay-encrypted, and decrypting it takes a jailbroken device once (or a
  pre-decrypted IPA). "No jailbreak" often means "no jailbreak, if someone
  already decrypted it."
- Free Apple certs expire after 7 days.
  [TrollStore](https://github.com/opa334/TrollStore) (iOS 17.0.x and below)
  installs with real entitlements permanently and skips the weekly re-sign.
- Apps with binary-integrity checks detect the re-signed bundle and refuse to
  run.

## Framework notes

- React Native uses `NSURLSession` and honors the system proxy. The standard
  hooks work.
- Flutter ships its own BoringSSL and ignores the system proxy, so
  `SecTrustEvaluate` hooks never fire and a device proxy captures nothing. It
  needs [reFlutter](https://github.com/Impact-I/reFlutter) to patch the engine;
  `mimic unpin` won't handle Flutter apps as they are.

## What still won't work

- DPoP / sender-constrained tokens. The per-request signing key never leaves the
  device, so captured requests don't replay. See [dpop.md](dpop.md).
- Hardware attestation (App Attest, DeviceCheck). Out of scope.

[ht]: https://github.com/httptoolkit/frida-interception-and-unpinning
