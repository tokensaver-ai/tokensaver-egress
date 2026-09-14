# Changelog

## [0.1.12] - 2026-09-14

- Fix Python 3.10 CI: use ``timezone.utc`` instead of ``datetime.UTC`` (3.11+).


## [0.1.10] - 2026-09-14

- README: public “how it works” principles (client placement, metadata-first, modes, SaaS policies).


## [0.1.9] - 2026-09-14

- Public docs: remove private-repo links; clarify client-only architecture (SaaS holds business logic).


## [0.1.8] - 2026-09-14

- ``tokensaver-egress unproxy`` (+ ``client-unproxy.sh``) to clear ``HTTPS_PROXY``
  after Ctrl+C; stop hint reminds clients so pip/curl are not stuck on a dead proxy.


## [0.1.7] - 2026-09-14

- Detect/fix accidental double-pasted ``TOKENSAVER_API_KEY``; clearer warning on
  policies-bundle ``401 Unauthorized``.


## [0.1.6] - 2026-09-14

- Bundle Claude Code skills (CCR, onboarding, MCP tools) and install via
  ``tokensaver-egress install-skills`` / guided setup — no ``tokensaver-cli`` required.


## [0.1.5] - 2026-09-14

- Interactive guided setup: ``tokensaver-egress setup`` (and menu on bare TTY invoke).
- Walks through API key, ingest URL, MITM CA create/trust, body capture, port; saves ``~/.tokensaver-egress/env`` + ``client-env.sh``.


## [0.1.4] - 2026-09-14

- Expand README: simple how-to, Claude Code / curl examples, troubleshooting.


## [0.1.3] - 2026-09-14

- Bare ``tokensaver-egress`` shows professional help (no auto-serve crash).
- Friendly error when the listen port is already in use.


## [0.1.2] - 2026-09-14

- Show ASCII banner before argparse so ``tokensaver-egress --help`` displays it too.


## [0.1.1] - 2026-09-14

- Show TokenSaver ASCII banner on startup (same logo as CLI; disable with `TOKENSAVER_NO_BANNER=1`).


## [0.1.0] - 2026-09-14

- Initial public release of ``tokensaver-egress`` on PyPI.
- Console entrypoint ``tokensaver-egress`` (``serve``, ``init-ca``).
- Explicit / MITM / transparent capture modes (ACP-4).
