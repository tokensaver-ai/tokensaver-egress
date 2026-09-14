"""CLI entrypoint: ``tokensaver-egress serve`` / ``claude`` / ``setup`` (ACP-4)."""

from __future__ import annotations

import argparse
import sys

from tokensaver_egress import __version__
from tokensaver_egress.banner import print_banner
from tokensaver_egress.ca import MitmCA
from tokensaver_egress.serve_cmd import run_serve
from tokensaver_egress.wizard import is_interactive, run_guided_setup, run_menu

DOCS_URL = "https://github.com/tokensaver-ai/tokensaver-egress"
PLATFORM_URL = "https://platform.tokensaver.fr"


def print_help(*, file=None) -> None:
    out = file if file is not None else sys.stdout
    print(
        f"""TokenSaver egress — client-side HTTPS capture proxy (ACP-4)

Usage:
  tokensaver-egress                 Interactive menu (TTY) or this help
  tokensaver-egress setup           Guided setup (API key, CA, skills) — once
  tokensaver-egress claude [ARGS]   Easiest: start proxy if needed + run Claude Code
  tokensaver-egress run [--] CMD…   Same for any command (cursor, curl, …)
  tokensaver-egress serve           Start proxy only (leave running)
  tokensaver-egress stop            Stop a proxy auto-started by claude/run
  tokensaver-egress init-ca
  tokensaver-egress install-skills
  tokensaver-egress unproxy
  tokensaver-egress help

Commands:
  setup            Beginner wizard (recommended first run)
  claude           Launch Claude Code through the proxy (one command)
  run              Launch any command through the proxy
  serve            Start the forward proxy only
  stop             Stop auto-started background proxy
  init-ca          Generate the local MITM CA
  install-skills   Install Claude Code skills (CCR, …)
  unproxy          Clear HTTPS_PROXY in a shell that sourced client-env.sh
  help             Show this message

Everyday (after setup once):
  tokensaver-egress claude

  # Or keep the proxy up and open tools yourself:
  tokensaver-egress serve
  # other terminal: source ~/.tokensaver-egress/client-env.sh && claude

Options (serve):
  --host HOST          Bind address (default: 0.0.0.0)
  --port PORT          Listen port (default: 8888)
  --log-level LEVEL    DEBUG | INFO | WARNING | ERROR (default: INFO)
  --transparent        Linux TPROXY / REDIRECT mode (metadata-only)

Options (claude / run):
  --keep-proxy         Leave auto-started proxy running after the command exits
  --no-start           Do not auto-start; fail if nothing listens on the port

Env:
  TOKENSAVER_API_KEY / TOKENSAVER_INGEST_URL   Ship audits to the control plane
  EGRESS_MITM_ENABLED=true                     Decrypt known LLM hosts
  EGRESS_CAPTURE_BODIES=1                      Persist request/response bodies
  TOKENSAVER_NO_BANNER=1                       Hide the ASCII logo
  Config file: ~/.tokensaver-egress/env         Written by ``setup``

Docs:  {DOCS_URL}
Console: {PLATFORM_URL}
Version: {__version__}
""",
        file=out,
    )


def _cmd_init_ca(_args: argparse.Namespace) -> int:
    ca = MitmCA()
    path = ca.init_ca()
    print(f"CA certificate written to: {path}")
    print("Trust this CA on client machines, then set EGRESS_MITM_ENABLED=true")
    print(
        "  macOS: sudo security add-trusted-cert -d -r trustRoot "
        "-k /Library/Keychains/System.keychain",
        path,
    )
    return 0


def _cmd_install_skills(args: argparse.Namespace) -> int:
    from tokensaver_egress.plugin_install import install_claude_plugin, plugin_is_installed

    force = bool(getattr(args, "force", False))
    if plugin_is_installed() and not force:
        from tokensaver_egress.plugin_install import plugin_install_dir

        print(f"Claude skills already installed: {plugin_install_dir()}")
        print("  Reinstall with:  tokensaver-egress install-skills --force")
        return 0
    dest = install_claude_plugin(force=force or not plugin_is_installed())
    if dest is None:
        print("✖ Plugin bundle missing — cannot install skills.", file=sys.stderr)
        return 1
    print(f"✓ Claude skills installed: {dest}")
    print("  Includes: tokensaver-ccr, tokensaver-onboarding, tokensaver-mcp-tools")
    print("  Restart Claude Code (or open a new session) to load them.")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    return run_serve(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        transparent=bool(getattr(args, "transparent", False)),
    )


def _cmd_stop(_args: argparse.Namespace) -> int:
    from tokensaver_egress.run_cmd import stop_auto_proxy

    if stop_auto_proxy():
        print("✓ Stopped auto-started proxy.")
        return 0
    print("No auto-started proxy pid found (nothing to stop).")
    print("  If you started with `serve`, use Ctrl+C in that terminal.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokensaver-egress",
        description="Egress capture proxy (ACP-4)",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="Run forward proxy", add_help=True)
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8888)
    serve_p.add_argument("--log-level", default="INFO")
    serve_p.add_argument(
        "--transparent",
        action="store_true",
        help="Transparent mode (TPROXY/iptables-REDIRECT): recover original dst + SNI, tunnel",
    )

    sub.add_parser("init-ca", help="Generate local MITM CA")
    skills_p = sub.add_parser("install-skills", help="Install Claude Code skills")
    skills_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ~/.claude/skills/tokensaver-router",
    )
    unproxy_p = sub.add_parser("unproxy", help="Clear client HTTPS_PROXY / HTTP_PROXY")
    unproxy_p.add_argument(
        "--sh",
        action="store_true",
        help='Print unset snippet for: eval "$(tokensaver-egress unproxy --sh)"',
    )

    for name, help_txt in (
        ("claude", "Start proxy if needed and run Claude Code"),
        ("run", "Start proxy if needed and run a command"),
    ):
        p = sub.add_parser(name, help=help_txt)
        p.add_argument(
            "--keep-proxy",
            action="store_true",
            help="Leave auto-started proxy running after exit",
        )
        p.add_argument(
            "--no-start",
            action="store_true",
            help="Fail if proxy is not already listening",
        )
        p.add_argument(
            "argv",
            nargs=argparse.REMAINDER,
            help="Arguments forwarded to the command",
        )

    sub.add_parser("stop", help="Stop auto-started background proxy")
    sub.add_parser("setup", help="Interactive guided setup")
    sub.add_parser("wizard", help="Alias for setup")
    sub.add_parser("help", help="Show help")

    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    return parser


def main(argv: list[str] | None = None) -> None:
    print_banner()
    raw = list(sys.argv[1:] if argv is None else argv)

    if not raw:
        if is_interactive():
            raise SystemExit(run_menu())
        print_help()
        raise SystemExit(0)

    if raw[0] in ("-h", "--help", "help"):
        print_help()
        raise SystemExit(0)

    if raw[0].startswith("-"):
        raw = ["serve", *raw]

    parser = _build_parser()
    args = parser.parse_args(raw)

    if args.command in (None, "help") or getattr(args, "help", False):
        print_help()
        raise SystemExit(0)
    if args.command in ("setup", "wizard"):
        raise SystemExit(run_guided_setup(start_after=True))
    if args.command == "init-ca":
        raise SystemExit(_cmd_init_ca(args))
    if args.command == "install-skills":
        raise SystemExit(_cmd_install_skills(args))
    if args.command == "unproxy":
        from tokensaver_egress.proxy_env import run_unproxy_cmd

        raise SystemExit(run_unproxy_cmd(sh_only=bool(getattr(args, "sh", False))))
    if args.command == "stop":
        raise SystemExit(_cmd_stop(args))
    if args.command in ("claude", "run"):
        from tokensaver_egress.run_cmd import run_with_proxy

        forwarded = list(getattr(args, "argv", []) or [])
        if args.command == "claude":
            forwarded = ["claude", *forwarded]
        raise SystemExit(
            run_with_proxy(
                forwarded,
                start_if_needed=not bool(getattr(args, "no_start", False)),
                keep_proxy=bool(getattr(args, "keep_proxy", False)),
            )
        )
    if args.command == "serve":
        raise SystemExit(_cmd_serve(args))

    print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
