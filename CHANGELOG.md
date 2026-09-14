# Changelog

## [0.1.3] - 2026-09-14

- Bare ``tokensaver-egress`` shows professional help (no auto-serve crash).
- Friendly error when the listen port is already in use.


## [0.1.2] - 2026-09-14

- Show ASCII banner before argparse so ``tokensaver-egress --help`` displays it too.


## [0.1.1] - 2026-09-14

- Show TokenSaver ASCII banner on startup (same logo as CLI; disable with `TOKENSAVER_NO_BANNER=1`).


## [0.1.0] - 2026-09-14

- Initial PyPI package layout (`tokensaver-egress`) from monorepo `packages/egress`.
- Console entrypoint `tokensaver-egress` (`serve`, `init-ca`).
- Explicit / MITM / transparent capture modes (ACP-4).
