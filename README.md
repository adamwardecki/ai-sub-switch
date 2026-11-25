# AI subscription switcher

The recovery runner watches a selected local Conductor chat for a Claude
subscription limit. It saves a pending turn while macOS is locked, then checks
that the turn is still blocked before selecting an account and model. For a Claude subscription change that
keeps the same model and effort, it switches the shared login and clicks
Conductor's Retry button in the existing chat. A provider or model change uses
a new chat with Conductor's transcript attachment. A send with an uncertain result stays in the queue for inspection
so a restart cannot send it again.

## Install

The alpha requires macOS, Python 3.12 or later, and a local Conductor workspace.
The live paths were verified on Conductor 0.100.0. The runner watches one
selected chat, and Conductor needs to be in the foreground for recovery.

Once the package is published to PyPI, install it with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install 'ai-sub-switch[macos]==0.1.0'
uv tool install 'claude-swap==1.2.0'
aisub init
aisub validate
```

To try a wheel built from this checkout, run `uv build --no-sources`, then
`uv tool install "ai-sub-switch[macos] @ $PWD/dist/ai_sub_switch-0.1.0-py3-none-any.whl"`.
Keep the `uv tool` bin directory on your PATH.

Enroll each Claude subscription in [claude-swap](https://github.com/realiti4/claude-swap).
With the first account signed in, run `cswap add --alias claude-a`. Use Claude
Code’s normal `/login` to sign in to the second account, then run
`cswap add --alias claude-b`. Check the aliases with `cswap list` and edit
`~/.config/ai-sub-switch/config.toml` if your aliases or preferred model order
differ. Codex fallback uses the current ChatGPT login.

## Observe a chat

Copy the Conductor chat’s link and use its session UUID. Launch from a
Terminal with [macOS Accessibility access](https://support.apple.com/guide/mac-help/allow-accessibility-apps-to-access-your-mac-mh43185/mac)
when testing live mode.

```sh
aisub doctor
aisub run --session CHAT_UUID --include-current
```

Omit `--include-current` to ignore quota failures that predate enrollment. The
first invocation for a chat establishes its observation baseline. Add `--once`
to perform a single check. State lives in
`~/Library/Application Support/ai-sub-switch/state.sqlite3` by default.

Run `aisub status` to inspect the queue. Use `pause` and
`resume` to control a live runner. A row stuck in `preparing` or `submitting`
requires checking the destination chat before trying to continue manually.

## Live verification

Conductor 0.100.0 passed a disposable Claude-to-Codex handoff and a
lock-to-unlock Claude-to-Claude test. In the latter, the Terminal runner
queued the quota-blocked turn, waited for unlock, switched from Claude A to
Claude B, and retried once in the original chat. Claude completed on the
new account. Native handoffs require Accessibility permission and Conductor
in the foreground.

Add `--live` to the scoped `run --session CHAT_UUID` command to enable
recovery for that chat. Without it, the runner reports its choice.

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

## License

[MIT](LICENSE). This is an independent project and is not affiliated with
Conductor, Anthropic, or OpenAI.
