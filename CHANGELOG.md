# Changelog

## [0.1.28] - 2026-09-19

- Skills: clearer end/verification rules (metrics / ``work_complete`` /
  ``stop_signal``) to avoid dual-evaluator ``CLIENT_CLAIM_IGNORED_NO_METRIC``.

## [0.1.27] - 2026-09-19

- Claude-managed Boucles: skills ``tokensaver-agentic-loops`` +
  ``tokensaver-close-loop`` (MCP start/end — no user ``tokensaver loop``).
- ``tokensaver_loop_start`` stamps ``~/.tokensaver-egress/current-loop.env``;
  ``serve`` hot-reloads that file per capture (no ``serve --loop`` required).
- ``install-skills --force`` prefers in-tree plugin over an older wheel.

## [0.1.26] - 2026-09-19

- Keep ``loop_id`` / ``loop_iteration`` inside ``attrs`` as well as top-level when
  shipping ingest (older API schemas that omit top-level ``loop_id`` no longer
  lose the Boucle link). Pair with platform ingest schema that declares these
  fields on ``EgressRecord``.


## [0.1.25] - 2026-09-19

- **Fix:** egress audits were ingested **without** ``loop_id`` → Boucles stayed at
  ``iters: 0`` while Flux showed Anthropic captures. ``_loop_from_headers`` skipped
  the ``TOKENSAVER_LOOP_ID`` env fallback when headers were empty/None; tunnel /
  HTTP-forward paths never stamped. Now always fall back to env, stamp in
  ``AuditSink.record``, and attach on tunnel/http_forward.


## [0.1.24] - 2026-09-19

- **Fix:** ``serve --loop`` created a Boucle + updated ``current-loop.env``
  *before* binding ``:8888``. If an old serve still held the port, ``loop status``
  showed the new loop (iters=0) while Claude hit the old process (often a
  cancelled loop → 403). Now refuse to start when the port is already in use.
- **Fix:** ``serve --loop`` / ``claude --loop`` without ``--goal`` created a
  ``goal_based`` loop that org policy rejects at precheck (``GOAL_REQUIRED``) →
  Claude ``403 Blocked by TokenSaver loop precheck: deny``. Now fail fast with
  a clear error; precheck 403 message includes the reason + restart hint.
- **Docs / UX — residual ``HTTPS_PROXY`` after Claude:** leftover proxy env in the
  parent shell (``source client-env.sh`` or ``--keep-proxy`` then a dead listener)
  makes ``pip``/``curl`` hit ``localhost:8888``. Documented in README + runbook
  §2.1.1; exit hints for ``stop`` / ``unproxy``.


## [0.1.19] - 2026-09-15

- Fix under-counted ``tokens_input`` with Anthropic prompt caching: sum
  ``input_tokens + cache_creation_input_tokens + cache_read_input_tokens``
  (Claude Code often reports ``input_tokens=2`` for the uncached tail only).


## [0.1.18] - 2026-09-14

- MITM: record ``attrs.ttft_ms`` (time to first response body byte after the
  provider request is sent) so the dashboard TTFT KPI works for egress LLM flows.


## [0.1.17] - 2026-09-14

- Fix ``CERT_SIGNATURE_FAILURE`` after CA rotation: purge/reissue host leaf certs
  not signed by the current CA key (``ensure_leaves_match_ca`` on ``serve`` / ``claude``).


## [0.1.16] - 2026-09-14

- ``serve`` refreshes ``client-env.sh`` and prints ``claude --no-start`` (fixes SSL
  when Claude is launched without ``NODE_EXTRA_CA_CERTS``).
- Deduplicate noisy MITM handshake-abort log spam; hint the one-command client launch.
- ``NO_PROXY`` includes ``mcp.tokensaver.fr`` / ``gateway.tokensaver.fr``.


## [0.1.15] - 2026-09-14

- Docs: two-terminal flow (`serve` + ``claude --no-start``) for live proxy logs.


## [0.1.14] - 2026-09-14

- Same as 0.1.13 (OSS auto-bump when ``v0.1.13`` already existed).


## [0.1.13] - 2026-09-14

- ``tokensaver-egress claude`` / ``run``: one-command launch with auto-started proxy
  (no second terminal / no source client-env).


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
