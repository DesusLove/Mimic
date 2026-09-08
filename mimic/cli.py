"""mimic CLI — capture any iOS app, generate a client.

    mimic record            start the proxy + print iPhone setup steps
    mimic hosts             list captured hosts (pick your API host here)
    mimic learn <host>      show the endpoints mimic saw for a host
    mimic gen <host>        AI-write a Python client for a host
    mimic unpin <ipa|id>    defeat cert pinning (Frida) so capture works
    mimic doctor            check your setup
"""
import argparse
import re
import shutil
import socket
import subprocess
import sys

from . import codegen
from . import unpin
from .sources import har
from .sources import mitm


def _mitm_and_flows():
    m = mitm.Mitm()
    return m, m.flows()


def _flows_from_args(args):
    """Flows from a HAR file when --har is given, else from live mitmweb."""
    if getattr(args, "har", None):
        return None, har.load(args.har)
    return _mitm_and_flows()


def _endpoints_from_args(args, m, flows):
    """Endpoints for a host, from HAR (inline bodies) or mitmweb (fetched)."""
    if getattr(args, "har", None):
        return har.endpoints(args.har, args.host)
    return mitm.endpoints(m, flows, args.host)


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "<this-machine-ip>"
    finally:
        s.close()


def _mitmweb_cmd():
    """Prefer mitmweb on PATH; otherwise run it ephemerally through uv."""
    if shutil.which("mitmweb"):
        return ["mitmweb"]
    if shutil.which("uvx"):
        return ["uvx", "--from", "mitmproxy", "mitmweb"]
    return None


def cmd_record(args):
    ip = _lan_ip()
    print(
        f"""
iPhone capture — do this once, then just reopen the app to add traffic:

  1. iPhone → Settings → Wi-Fi → (your network) ⓘ → Configure Proxy → Manual
        Server: {ip}      Port: 8080
  2. Safari → http://mitm.it → download the Apple (.pem) profile
  3. Settings → General → VPN & Device Management → install the profile
  4. Settings → General → About → Certificate Trust Settings
        → turn ON full trust for "mitmproxy"          ← everyone forgets this
  5. open the target app and use it normally
  6. back here:   mimic hosts      then   mimic gen <api-host>

  mitmweb dashboard: http://127.0.0.1:8081   (mimic reads flows from here)

  Some apps (banks, Instagram) pin their certificate, so a proxy sees no
  usable traffic — those aren't supported. Many apps aren't pinned and just
  work; if `mimic hosts` shows the app's API host, you're good.
"""
    )
    cmd = _mitmweb_cmd()
    if not cmd:
        sys.exit(
            "no proxy available — install uv (https://astral.sh/uv) so mimic can\n"
            "run mitmproxy for you, or `pipx install mitmproxy` yourself."
        )
    sys.stdout.flush()  # show the steps before the proxy takes over the terminal
    try:
        subprocess.run(cmd)
    except FileNotFoundError:
        sys.exit("failed to launch mitmweb")


def cmd_doctor(args):
    ok = True

    def check(name, present, fix):
        nonlocal ok
        mark = "ok " if present else "MISSING"
        print(f"  [{mark}] {name}")
        if not present:
            ok = False
            print(f"          → {fix}")

    print("mimic setup check:\n")
    check("proxy (mitmweb or uvx)", _mitmweb_cmd() is not None,
          "install uv: curl -LsSf https://astral.sh/uv/install.sh | sh")
    check("AI generator (claude or opencode)",
          shutil.which("claude") is not None or shutil.which("opencode") is not None,
          "install Claude Code or OpenCode (https://opencode.ai), or use `mimic gen --prompt-only`")
    reachable = False
    try:
        mitm.Mitm().flows()
        reachable = True
    except mitm.MitmError:
        pass
    check("mitmweb running + reachable", reachable,
          "run `mimic record` in another terminal")

    def opt(name, present, fix):
        # Optional — only needed for `mimic unpin`; never fails the check.
        print(f"  [{'ok ' if present else '  -'}] {name}")
        if not present:
            print(f"          → {fix}")

    print("\noptional — only for `mimic unpin` (pinned apps):")
    opt("git (fetch unpinning scripts)", shutil.which("git") is not None,
        "install git (Xcode CLT: xcode-select --install)")
    opt("frida (run the hooks)", shutil.which("frida") is not None,
        "pipx install frida-tools   (or: uv tool install frida-tools)")
    opt("objection (gadget inject, no-JB path)", shutil.which("objection") is not None,
        "pipx install objection   (or: uv tool install objection)")

    print(f"\nLAN IP for the iPhone proxy: {_lan_ip()}:8080")
    sys.exit(0 if ok else 1)


def cmd_hosts(args):
    _, flows = _flows_from_args(args)
    rows = mitm.hosts(flows)
    if not rows:
        if getattr(args, "har", None):
            sys.exit("no entries in the HAR file")
        sys.exit("no traffic captured yet — run `mimic record` and use the app")
    print(f"{'requests':>9}  host")
    for host, n in rows:
        print(f"{n:>9}  {host}")
    print("\nPick your API host (usually the one with JSON, not media/cdn).")


def cmd_learn(args):
    m, flows = _flows_from_args(args)
    eps = _endpoints_from_args(args, m, flows)
    if not eps:
        sys.exit(f"no requests to {args.host} found")
    print(f"{args.host}: {len(eps)} endpoints\n")
    for e in eps:
        print(f"  {e['method']:5s} {e['path']}   -> {e['status']}")


def cmd_gen(args):
    m, flows = _flows_from_args(args)
    eps = _endpoints_from_args(args, m, flows)
    if not eps:
        sys.exit(f"no requests to {args.host} found")

    if args.prompt_only:
        print(codegen.build_prompt(args.host, eps))
        return

    out = args.out or _default_out(args.host)
    print(f"asking {args.generator} to write a client from {len(eps)} endpoints…", file=sys.stderr)
    source = codegen.generate(args.host, eps, model=args.model, generator=args.generator)
    with open(out, "w") as f:
        f.write(source)
    cls = _class_name(source)
    print(f"\nwrote {out}")
    print(f"\n    from {out[:-3]} import {cls or 'Client'}")
    print(f"    acc = {cls or 'Client'}()")
    print("    # then call the generated methods\n")


def _default_out(host):
    stem = re.sub(r"[^a-z0-9]+", "_", host.split(".")[0].lower()).strip("_")
    return f"{stem or 'app'}_client.py"


def _class_name(source):
    m = re.search(r"class\s+(\w+)\s*\(", source)
    return m.group(1) if m else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="mimic", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("record", help="start the proxy + iPhone setup steps").set_defaults(func=cmd_record)
    sub.add_parser("doctor", help="check your setup").set_defaults(func=cmd_doctor)
    hp = sub.add_parser("hosts", help="list captured hosts")
    hp.add_argument("--har", metavar="FILE", help="read from a HAR file instead of mitmweb")
    hp.set_defaults(func=cmd_hosts)

    lp = sub.add_parser("learn", help="show endpoints for a host")
    lp.add_argument("host")
    lp.add_argument("--har", metavar="FILE", help="read from a HAR file instead of mitmweb")
    lp.set_defaults(func=cmd_learn)

    gp = sub.add_parser("gen", help="AI-generate a client for a host")
    gp.add_argument("host")
    gp.add_argument("-o", "--out", help="output .py path")
    gp.add_argument("--model", default="sonnet", help="model name (claude default: sonnet)")
    gp.add_argument("--generator", default="claude", choices=["claude", "opencode"],
                    help="AI generator to use (default: claude)")
    gp.add_argument("--prompt-only", action="store_true", help="print the prompt instead of calling the AI generator")
    gp.add_argument("--har", metavar="FILE", help="read from a HAR file instead of mitmweb")
    gp.set_defaults(func=cmd_gen)

    up = sub.add_parser("unpin", help="defeat cert pinning via Frida so capture works")
    up.add_argument("target", help="a decrypted .ipa (gadget path) or app bundle-id (jailbroken path)")
    up.add_argument("--ca", help="mitmproxy CA cert (default: ~/.mitmproxy/mitmproxy-ca-cert.pem)")
    up.add_argument("--proxy-host", help="proxy host to bake in (default: this Mac's LAN IP)")
    up.add_argument("--workdir", help="where to put scripts + patched IPA (default: mimic-unpin/)")
    up.add_argument("--codesign", help="signing identity for `objection patchipa`")
    up.set_defaults(func=unpin.cmd_unpin)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
