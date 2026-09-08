"""Defeat certificate pinning so mimic can capture a pinned iOS app.

Pinning doesn't stop replay — it stops *capture*. A pinned app rejects
mitmproxy's cert, so the proxy never sees the plaintext and there's nothing to
grab. The fix is app-side: make the app stop checking the pin, using Frida to
hook its TLS verification at runtime.

mimic doesn't reimplement the hooks (that's a moving target across iOS versions
and TLS stacks). It orchestrates the maintained upstream —
httptoolkit/frida-interception-and-unpinning — and owns the one part that is
genuinely mimic's job: baking *your* mitmproxy CA and proxy address into the
scripts, and emitting the exact command to run.

Two device paths:

  - jailbroken (frida-server on the phone): attach over USB, no repackaging.
    `mimic unpin <bundle-id>` prints the ready `frida -U …` command.
  - stock device (Frida gadget): inject the gadget into a decrypted IPA, re-sign,
    sideload. `mimic unpin <app.ipa>` runs `objection patchipa` for the inject,
    then hands the signing+install step to Sideloadly / pymobiledevice3.
"""
import os
import re
import shutil
import subprocess
import sys

SCRIPTS_REPO = "https://github.com/httptoolkit/frida-interception-and-unpinning"

# Load order matters: config.js first (it defines CERT_PEM/PROXY_* the others
# read), then the iOS hooks, then the shared native hooks. Mirrors the upstream
# README's iOS invocation.
IOS_SCRIPTS = [
    "config.js",
    "ios/ios-connect-hook.js",
    "ios/ios-disable-detection.js",
    "native-tls-hook.js",
    "native-connect-hook.js",
]

DEFAULT_CA = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
PROXY_PORT = 8080  # mitmweb's proxy listener — same port `mimic record` prints


def _clone_or_update(dst):
    """Fetch (or refresh) the upstream Frida scripts into `dst`."""
    if not shutil.which("git"):
        return False
    if os.path.isdir(os.path.join(dst, ".git")):
        subprocess.run(["git", "-C", dst, "pull", "--quiet", "--ff-only"],
                       check=False)
        return True
    r = subprocess.run(["git", "clone", "--depth", "1", "--quiet",
                        SCRIPTS_REPO, dst], check=False)
    return r.returncode == 0


def _patch_config(scripts_dir, ca_pem, proxy_host):
    """Bake the CA + proxy into a copy of upstream config.js, in place.

    We patch upstream's file rather than hand-write one so all its helper code
    (the base64/DER machinery the hooks depend on) stays intact and current.
    """
    path = os.path.join(scripts_dir, "config.js")
    with open(path) as f:
        src = f.read()

    ca = ca_pem.strip()
    src, n_cert = re.subn(r"const CERT_PEM = `.*?`;",
                          f"const CERT_PEM = `{ca}`;", src, count=1, flags=re.S)
    src, n_host = re.subn(r"const PROXY_HOST = '[^']*';",
                          f"const PROXY_HOST = '{proxy_host}';", src, count=1)
    src, n_port = re.subn(r"const PROXY_PORT = \d+;",
                          f"const PROXY_PORT = {PROXY_PORT};", src, count=1)
    if not (n_cert and n_host and n_port):
        sys.exit("config.js from upstream changed shape — mimic couldn't patch "
                 f"it (cert={n_cert} host={n_host} port={n_port}). Open an issue.")

    with open(path, "w") as f:
        f.write(src)


def _lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "<this-machine-ip>"
    finally:
        s.close()


def _frida_command(scripts_dir, target, gadget):
    flag = "-n" if gadget else "-f"  # attach to running gadget vs spawn by id
    lines = ["frida -U \\"] if not gadget else ["frida -U \\"]
    for s in IOS_SCRIPTS:
        lines.append(f"    -l {os.path.join(scripts_dir, s)} \\")
    lines.append(f"    {flag} {target}")
    return "\n".join(lines)


def cmd_unpin(args):
    target = args.target
    is_ipa = target.lower().endswith(".ipa")

    ca_path = args.ca or DEFAULT_CA
    if not os.path.exists(ca_path):
        sys.exit(f"no mitmproxy CA at {ca_path} — run `mimic record` once so "
                 "mitmproxy generates it, or pass --ca <path>.")
    with open(ca_path) as f:
        ca_pem = f.read()

    workdir = args.workdir or "mimic-unpin"
    scripts_dir = os.path.join(workdir, "frida-scripts")
    os.makedirs(workdir, exist_ok=True)

    print(f"fetching unpinning scripts → {scripts_dir}", file=sys.stderr)
    if not _clone_or_update(scripts_dir):
        sys.exit(
            "couldn't fetch the Frida scripts (need git + network). Clone them "
            f"yourself:\n    git clone {SCRIPTS_REPO} {scripts_dir}\n"
            "then re-run `mimic unpin`.")

    host = args.proxy_host or _lan_ip()
    _patch_config(scripts_dir, ca_pem, host)
    print(f"baked your CA + proxy {host}:{PROXY_PORT} into config.js\n")

    if not is_ipa:
        # Jailbroken path: bundle id → attach over USB to frida-server.
        print("Jailbroken device (frida-server) path:\n")
        print("  1. phone + Mac on the same network; `mimic record` running here")
        print("  2. run this to launch the app with pinning disabled:\n")
        print(_frida_command(scripts_dir, target, gadget=False))
        print("\n  3. use the app, then:  mimic hosts   →   mimic gen <api-host>")
        return

    # Stock device path: inject the Frida gadget into the IPA via objection.
    print("Stock device (Frida gadget) path:\n")
    if not shutil.which("objection"):
        sys.exit(
            "objection not found — it does the gadget injection.\n"
            "    pipx install objection      (or: uv tool install objection)\n"
            "then re-run `mimic unpin`.")

    out_ipa = os.path.join(workdir, os.path.basename(target)[:-4] + "-patched.ipa")
    cmd = ["objection", "patchipa", "--source", target, "--output", out_ipa]
    if args.codesign:
        cmd += ["--codesign-signature", args.codesign]
    print("injecting Frida gadget:\n    " + " ".join(cmd) + "\n")
    r = subprocess.run(cmd, check=False)
    if r.returncode != 0:
        sys.exit("objection patchipa failed (needs a *decrypted* IPA; App Store "
                 "binaries are encrypted). See the note below.")

    print(f"\npatched IPA → {out_ipa}\n")
    print("Next — sign + install (mimic hands this off; Apple auth lives here):")
    print("  • easiest:   drag the patched IPA into Sideloadly and install")
    print("  • scripted:  pymobiledevice3 apps install " + out_ipa)
    print("\nThen attach the unpinning scripts to the gadget:\n")
    print(_frida_command(scripts_dir, "Gadget", gadget=True))
    print("\nThen use the app and:  mimic hosts   →   mimic gen <api-host>")
    print("\nNotes:")
    print("  - Needs a DECRYPTED IPA. Your own app build works; an App Store")
    print("    app must be decrypted first (a jailbroken device, once).")
    print("  - Free Apple certs expire in 7 days. TrollStore (iOS ≤17.0.x)")
    print("    avoids the re-sign churn.")
    print("  - Flutter apps ignore the proxy + use private BoringSSL — this")
    print("    path won't capture them without reFlutter.")
