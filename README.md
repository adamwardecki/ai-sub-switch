# AI subscription switcher

The recovery runner watches a selected local Conductor chat for a Claude
subscription limit. It saves a pending turn while macOS is locked, then checks
that the turn is still blocked before selecting an account and model. For a Claude subscription change that
keeps the same model and effort, it switches the shared login and clicks
Conductor's Retry button in the existing chat. A provider or model change uses
a new chat with Conductor's transcript attachment. A send with an uncertain result stays in the queue for inspection
so a restart cannot send it again.

## Observe a chat

Use Python 3.12+, uv, and two Claude accounts enrolled in `cswap`. Edit
[the example configuration](examples/config.toml) with your account aliases
and fallback order. Use a Conductor chat UUID from its copied link.

```sh
uv run --locked --extra macos aisub --cswap /absolute/path/to/cswap doctor
uv run --locked --extra macos aisub --config examples/config.toml \
  --cswap /absolute/path/to/cswap run --session CHAT_UUID --include-current
```

Omit `--include-current` to ignore quota failures that predate enrollment. The
first invocation for a chat establishes its observation baseline. Add `--once`
to perform a single check. State lives in
`~/Library/Application Support/ai-sub-switch/state.sqlite3` by default.

Run `uv run --locked aisub status` to inspect the queue. Use `pause` and
`resume` to control a live runner. A row stuck in `preparing` or `submitting`
requires checking the destination chat before trying to continue manually.

## Live verification

Read-only recovery and a disposable Claude-to-Codex handoff were verified on
Conductor 0.100.0. The Terminal runner created a GPT-6.2 Sol tab, attached the
source transcript, and sent one continuation. Codex recovered the marker
from that transcript. The Terminal probe also retried a quota-blocked Claude
turn in the same chat after a manual account switch. Conductor recorded one
replay and Claude completed it. The integrated account-switch and unlock cycle
still needs live validation. The
production watcher continues to reject live operation on 0.100.0.
Accessibility permission and Conductor in the foreground are required for
the native handoff.

To verify the automated click against a disposable, currently quota-blocked
Claude chat, activate a Claude account with capacity, then run:

```sh
uv run --locked --extra macos aisub --config examples/config.toml \
  test-handoff --same-thread --session CHAT_UUID
```

This probe uses the current Claude login and reports whether Conductor recorded
a replay in the original chat.

For the full lock/unlock test, start with a currently quota-blocked disposable
Claude chat on the exhausted account. Wait for other Claude work to settle:

```sh
uv run --locked --extra macos aisub --config examples/config.toml \
  --state .context/unlock-test.sqlite --cswap /absolute/path/to/cswap \
  run --session CHAT_UUID --include-current --live --unlock-test
```

Once the runner reports that the turn is queued, bring Conductor forward,
lock macOS for at least five seconds, then unlock. It waits for a confirmed lock
and unlock, switches to another Claude account, and retries at most once.
Use a fresh state path for a new disposable test. This mode has a three-minute
time limit and only accepts the designated disposable chat.

`launch-agent --session CHAT_UUID` prints a per-user launchd plist. It
defaults to observation mode. The plist reflects the current executable and
paths, so generate it from a stable installation before loading it.

The runner currently recognizes Claude quota events. It can check Codex
subscription capacity for a fallback, but does not classify Codex quota
failures as recovery triggers. An unsupported model label or changed UI
control stops recovery for inspection.

## Development

```sh
uv run --locked pytest -q
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv build
```
