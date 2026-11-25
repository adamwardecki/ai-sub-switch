# AI Subscription Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically continue quota-blocked Conductor tasks across ordered subscription accounts, retaining native transcript handoffs and deferring actions until the Mac unlocks.

**Architecture:** A local coordinator observes Conductor's database read-only, selects an account/model through pure rules, and persists recovery receipts in its own SQLite database. Explicit cswap commands activate Claude accounts; a serialized macOS accessibility adapter performs Conductor's native continuation. Feasibility is a blocking first milestone, not an assumption buried in later implementation.

**Tech Stack:** Proposed Python 3.12+, `uv`, standard-library `sqlite3`, `tomllib`, `argparse`, `subprocess`, and `fcntl`; PyObjC bindings for macOS accessibility/session APIs; pytest and Ruff for development; launchd for the user service. Task 1 validates this stack before product scaffolding. Lock exact dependency versions in `uv.lock`; pin a tested cswap release separately rather than importing its internals.

**Spec:** `docs/superpowers/specs/2026-11-18-ai-subscription-switcher-design.md`. Read it before this plan. Evidence: `.context/conductor-research/findings.md`; these research files are local and are not dependencies of the shipped product.

## Global Constraints

- macOS local Conductor only; initially Claude Code and Codex subscription sessions.
- Reactive switching after confirmed quota exhaustion; no proactive threshold switching.
- Defer both credential changes and chat continuation while locked. Monitoring may continue while the Mac is awake.
- One machine-wide active Claude account is acceptable. Separate simultaneous account profiles are unnecessary for this use case.
- Keep a task on its current account until another switch is needed. Do not move it back merely because a preferred account resets.
- Resume pending handoffs when the Mac unlocks. Locked-screen handoff is not required.
- Do not replay ambiguous sends automatically.
- No writes to Conductor's database, private-IPC dependency, plaintext credential export, or automatic installation of arbitrary provider code.
- Keep the original chat available. Preserve workspace files without resetting or automatically committing them.
- No execution is promised while asleep or powered off.
- Use the current Conductor worktree and branch; do not rename it. This plan does not install anything or enable live automation.

## Review Focus

1. A crash after Send but before receipt persistence must not submit the task twice — Tasks 5, 7, 8.
2. A user resumes/cancels a source or changes the foreground composer during recovery — Tasks 3, 7, 8.
3. Lock, sleep, fast user switching, or missing session-state data between two UI actions — Tasks 6, 7.
4. A Conductor update changes event schemas or accessibility labels; old history reappears — Tasks 1, 3, 7.
5. A global Claude login changes manually while chats retain old process credentials — Tasks 4, 8.

---

## Delivery boundaries and file map

This is one coupled recovery pipeline, not independently deployable services. Implement in task order. Task 1 is a go/no-go milestone; Tasks 2–10 depend on it passing. The first useful read-only deliverable is Tasks 2–5, and live automation is not enabled until Task 10.

```text
pyproject.toml, uv.lock                      package, aisub entry point, dev tools
README.md                                   setup and operation
examples/config.toml                        credential-free example
src/ai_sub_switch/
  domain.py                                 immutable records and adapter protocols
  config.py, rules.py                        TOML validation and account/model selection
  providers/base.py, registry.py             explicit adapter registry, no dynamic downloads
  providers/cswap.py, codex.py                authentication/availability adapters
  providers/process.py                       bounded subprocess calls and redaction
  storage.py                                tool-owned queue/cursor/receipt transactions
  conductor/database.py, events.py           read-only reader and event classification
  conductor/sessions.py                      latest-turn and source/destination checks
  conductor/ui.py                           native handoff state machine
  conductor/compatibility.json               tested app/schema/selector evidence
  macos/session.py, accessibility.py         desktop guard and bounded AX operations
  coordinator.py, daemon.py                  recovery decisions and polling loop
  cli.py, service.py                         commands and user LaunchAgent lifecycle
tests/
  conftest.py, fakes.py                      isolated temp stores, fake clock/adapters
  fixtures/                                 synthetic DB/events/AX/CLI snapshots
  test_config_rules.py, test_observer.py, test_providers.py
  test_storage.py, test_session_guard.py, test_handoff.py
  test_coordinator.py, test_cli_service.py, test_recovery_e2e.py
docs/feasibility/conductor-0.94.1.md          measured go/no-go findings, no private history
docs/operations.md, docs/providers.md        recovery runbook and extension contract
```

Runtime paths: `~/.config/ai-sub-switch/config.toml`, `~/Library/Application Support/ai-sub-switch/state.sqlite3`, and `~/Library/Logs/ai-sub-switch/`. Use private directories/files (0700/0600), logs containing IDs/reasons rather than prompts/tokens, and a single per-user OS lock file in the state directory. Default database path is Conductor's observed Application Support path; tests always supply temporary paths.

## Task 1: Prove the native handoff and runtime assumptions

**Files:** Create `docs/feasibility/conductor-0.94.1.md`; throwaway scripts and screenshots under `.context/feasibility/` only. No product scaffolding yet.

**Interfaces:** Consumes the design and research findings. Produces a written pass/fail matrix, tested versions, synthetic event/AX fixtures, and the exact native interaction sequence for same-agent and cross-agent handoffs.

- [ ] Record current Conductor version, cswap release/JSON contract, Python/PyObjC compatibility and installed agent paths without reading or printing credentials. Check dependency licenses before reuse. Treat untested Conductor versions as unsupported, not compatible by version range alone.
- [ ] Use an explicitly disposable task with a unique marker and one unfinished instruction. With computer-use tooling, inspect and exercise Conductor's native continuation: identify source by workspace/session ID; create destination; verify transcript attachment and chosen model; submit once; verify the target knows the marker and unfinished instruction. Test Claude→Claude and Claude→Codex. A model-menu label alone is not a pass. Keep screenshots under `.context/` and sanitize any fixture committed later.
- [ ] Verify two enrolled Claude accounts can be activated through cswap and that a new Conductor process uses the selected identity without browser authentication. Use cswap status plus a reliable local identity/account signal, not the model's self-report. If accounts need enrollment, have the user complete browser login. Do not interrupt unrelated sessions or run another switcher concurrently.
- [ ] Validate read-only signals distinguishing quota exhaustion, manual cancellation, ordinary completion, source supersession and successful destination submission for both Claude and Codex. Claude rejection events were observed during reconnaissance; Codex event shapes still need verification. Establish parent/subagent handling and source-account attribution. When a distinction has no reliable signal, specify `needs-attention`; do not infer blocked from `idle` alone. Verify Codex→Claude native continuation too so the configurable order works in both directions.
- [ ] Probe desktop states using PyObjC: unlocked, locked, unlock, sleep/wake, and inactive user session. `CGSessionCopyCurrentDictionary` and workspace notifications are candidates, not proof that every returned key is a public lock-state contract. Record the exact observed signal and its limitations; absent/inconsistent data must mean `unknown`. User performs lock/unlock during the live check; never disable locking.
- [ ] Verify a prompt-free Codex account/limit probe through the installed CLI's structured protocol, using primary CLI documentation or installed protocol schemas. Record exact request/response and exit behavior. Do not invent RPC names, generate a model request to estimate quota, or copy credentials.
- [ ] Fill the pass/fail matrix. Both native handoffs, source/destination identity, credential pickup, lock guard and prompt-free availability must pass to proceed. If a required capability fails, stop product implementation and present the specific alternative for user decision. Keep failed experiments labeled throwaway.
- [ ] Commit only the sanitized feasibility report: `git add docs/feasibility/conductor-0.94.1.md && git commit -m "docs: verify native Conductor recovery feasibility"`.

## Task 2: Define account configuration and pure selection rules

**Files:** Create `pyproject.toml`, `uv.lock`, `src/ai_sub_switch/{__init__,domain,config,rules}.py`, `examples/config.toml`, `tests/{conftest,fakes,test_config_rules}.py`.

**Interfaces:** Produce the following immutable dataclasses/enums in `domain.py` (all time fields are UTC datetimes):

- `Account(id: str, label: str, provider: str, backend_ref: str, enabled: bool)`.
- `ModelChoice(provider: str, model: str, effort: str | None)`.
- `Availability(account_id: str, state: Literal['ready','exhausted','reauth','unknown'], resets_at: datetime | None, observed_at: datetime, reason: str)`.
- `BlockedTurn(workspace_id: str, session_id: str, turn_id: str, event_id: str, account_id: str | None, source: ModelChoice, observed_at: datetime, resets_at: datetime | None)`.
- `Selection(kind: Literal['resume','handoff','wait','attention'], account_id: str | None, model: ModelChoice | None, retry_at: datetime | None, reason: str)`.
- `Config(accounts: tuple[Account,...], order: tuple[str,...], mappings: tuple[ModelMapping,...], conductor_db: Path)` and `ModelMapping(source_provider: str, source_model: str, destination_provider: str, destination_account: str | None, target: ModelChoice)`.
- `load_config(path: Path) -> Config`; `select_target(turn: BlockedTurn, config: Config, availability: Mapping[str, Availability], now: datetime) -> Selection`.

Configuration schema version is `1`; sections are `[[accounts]]`, top-level `order`, and `[[mappings]]`. Providers are initially `claude` and `codex`. Exact account-specific mappings win over provider mappings; same-provider preserves model/effort unless overridden. Unknown fields, duplicate IDs, duplicate/unknown order entries, ambiguous mappings and unsupported providers are errors. A disabled account never qualifies.

- [ ] Write parametrized rule tests using shared `accounts`, `turn`, and `quota` fixture builders from `tests/fakes.py`:
  ```python
  assert select_target(blocked_a, cfg, a_full_b_ready, now).account_id == 'claude-b'
  assert select_target(blocked_a, cfg, all_claude_full, now).model == mapped_codex
  assert select_target(blocked_a, cfg, a_recovered, now).kind == 'resume'
  assert select_target(blocked_a, cfg_without_mapping, all_claude_full, now).kind == 'attention'
  assert select_target(blocked_a, cfg, all_exhausted, now).kind == 'wait'
  ```
  Add config cases for duplicate IDs, disabled targets, unknown provider, credentials in config and missing model support. Define fixtures with explicit UTC time and synthetic model IDs; no real accounts.
- [ ] Run `uv run pytest tests/test_config_rules.py -q`; expect failures for missing module/functions after establishing package/dev dependencies.
- [ ] Implement records, validated TOML loading and selection. Check original account recovery first, then traverse configured order from highest to lowest excluding the blocked original; no change absent a blocked turn. Unknown availability does not mean ready. Default retry for unknown capacity is 60 seconds, capped at 300 seconds after repeated probe failures. Confirm actual model availability before executing a selection in later tasks.
- [ ] Provide suggested mapping defaults from Task 1's verified model catalog, version them, and write them into generated configuration for user editing. Do not freeze research-era model IDs as universal equivalents. Explicit user mappings override defaults; missing installed choices are errors.
- [ ] Run `uv run pytest tests/test_config_rules.py -q` and `uv run ruff check .`; expect all passing. Commit the package/config/rules deliverable.

## Task 3: Implement read-only quota observation and source reconciliation

**Files:** Create `conductor/{__init__,database,events,sessions}.py`, `conductor/compatibility.json`, `tests/test_observer.py`, synthetic fixtures under `tests/fixtures/conductor/`.

**Interfaces:** Produce `EventCursor(database_identity: str, rowid: int)`, `EventPage(events: tuple[dict,...], cursor: EventCursor)`, `SessionSnapshot(workspace_id: str, session_id: str, turn_id: str | None, disposition: Literal['blocked','working','completed','cancelled','superseded','unknown'], model: ModelChoice, archived: bool, hidden: bool)` in `domain.py`; `ConductorReader.baseline() -> EventCursor`, `.read_after(cursor: EventCursor, limit: int = 500) -> EventPage`, `.session(session_id: str) -> SessionSnapshot`; `classify_event(raw: dict, session: SessionSnapshot, account_id: str | None) -> BlockedTurn | None`.

- [ ] Write fixture-backed tests:
  ```python
  assert classify_event(allowed_with_overage_rejected, active, 'claude-a') is None
  assert classify_event(rejected_five_hour, blocked, 'claude-a').turn_id == 'turn-1'
  assert classify_event(subagent_error, active, 'claude-a') is None
  assert classify_event(temporary_throttle, active, 'claude-a') is None
  assert classify_event(old_rejection, manually_resumed, 'claude-a') is None
  ```
  Include reverted events, archived/hidden chats, unknown envelopes, cancellation, warning states, missing turn IDs, and changed schema. Add Codex exhausted-window and temporary-throttle fixtures from Task 1, and assert equivalent normalized results. Assert the reader connection rejects writes.
- [ ] Run `uv run pytest tests/test_observer.py -q`; expect failures for missing reader/classifier.
- [ ] Open Conductor using SQLite URI `mode=ro` plus `PRAGMA query_only=ON`, short read transactions and a 1000 ms busy timeout. Validate required schema and tested app version. Page by observed SQLite rowid while recording event/turn IDs for deduplication; detect database replacement or cursor regression, stop and reconcile rather than replaying old history.
- [ ] Implement classifier and source snapshot mapping from Task 1 evidence. Require subscription-window exhaustion or equivalent corroborated account quota evidence; a generic 429 is insufficient. Unknown/cancelled/superseded sources are not automatically eligible. Attribute accounts only from verified activation history/native identity; preserve unknown identity for attention.
- [ ] Run observer tests; expect pass, including a fixture where the table is replaced or reset. Commit the reader/classifier deliverable.

## Task 4: Add bounded subscription backends

**Files:** Create `providers/{__init__,base,registry,process,cswap,codex}.py`, `tests/test_providers.py`, `tests/fixtures/providers/`, `docs/providers.md`.

**Interfaces:** `ProviderAdapter.probe(account: Account, model: ModelChoice, now: datetime) -> Availability`, `.activate(account: Account) -> ActivationResult`, `.identity() -> str | None`; `ActivationResult(state: Literal['active','attention'], account_id: str | None, reason: str)`. `ProviderRegistry.get(provider: str) -> ProviderAdapter`. `ProcessRunner.run(argv: Sequence[str], timeout_seconds: float) -> ProcessResult(returncode: int, stdout: str, stderr: str)`.

- [ ] Write tests for valid cswap JSON, changed schema, expired authentication, Keychain denial, CLI timeout, account mismatch after activation, manual login drift, and a configured path containing spaces. Assert commands use argument arrays, never `shell=True`, and errors/logs redact token-like values. Assert `codex.activate` validates the existing account and never rotates credentials or selects API billing.
- [ ] Run `uv run pytest tests/test_providers.py -q`; expect missing-adapter failures.
- [ ] Implement cswap `list/status/switch --json` using the exact grammar/schema verified in Task 1, stable backend references rather than mutable numeric slots where possible, and 30-second command deadlines. Verify identity after switching; fail safely on unsupported versions. Do not invoke cswap's autonomous loop or `--force` to override live-process guards.
- [ ] Implement the Task 1-verified Codex structured usage/auth probe. Scope freshness to the account and model; stale (older than 60 seconds), unsupported or absent quota becomes unknown. Reject API-key modes for subscription-only configuration. Do not silently switch Codex accounts.
- [ ] Document the explicit registry contract for future providers. Unknown provider config reports unsupported; no arbitrary shell hooks or automatic provider installation. Run provider tests and commit.

## Task 5: Persist recoveries, cursors and action receipts

**Files:** Create `storage.py`, `tests/test_storage.py`; extend `domain.py` with queue records.

**Interfaces:** `Recovery(id: str, source: BlockedTurn, state: str, target_account_id: str | None, destination_session_id: str | None, submission_marker: str, retry_at: datetime | None, reason: str)`; `RecoveryStore.ingest(page: EventPage, turns: Sequence[BlockedTurn]) -> None`, `.pending(now: datetime) -> list[Recovery]`, `.transition(id: str, expected: str, next_state: str, details: dict) -> Recovery`, `.record_intent(id: str, action: str, details: dict) -> str`, `.complete_intent(intent_id: str, evidence: dict) -> None`.

- [ ] Write tests using temporary SQLite databases: cursor update and queue insert commit atomically; duplicate session/turn creates one recovery; invalid transitions fail; data survives reopen; destination and send intent survive a crash. Assert uncertain send becomes attention/reconciliation work, not a fresh pending send.
- [ ] Run `uv run pytest tests/test_storage.py -q`; expect missing-store failures.
- [ ] Implement versioned schema with `recoveries`, `observer_cursor`, `action_receipts`, `account_activations`, and `settings` tables. Use a unique source session/turn key, compare-and-set transitions, UTC timestamps and separate source/account identity evidence. Store no credentials or transcript bodies.
- [ ] Persist intent before each external mutation, including destination creation and Send. States follow the design; add `submitting` as the durable pre-Send state. Unknown success after a crash requires evidence or attention. Record first-enable baseline and persistent pause state transactionally.
- [ ] Run storage tests including forced failure between insertion/cursor update and between intent/completion. Commit the durable queue deliverable.

## Task 6: Guard actions with desktop state and a single coordinator lock

**Files:** Create `macos/{__init__,session,accessibility}.py`, `tests/test_session_guard.py`.

**Interfaces:** `DesktopState(kind: Literal['unlocked','locked','inactive','unknown'], generation: int)`; `SessionGuard.snapshot() -> DesktopState`, `.require_unlocked(expected_generation: int | None = None) -> DesktopState`; `CoordinatorLease.acquire(path: Path) -> ContextManager[None]` in `storage.py`; `AXClient.snapshot() -> AXSnapshot`, `.perform(action: AXAction, guard_generation: int) -> None` (AX records defined in `macos/accessibility.py`).

- [ ] Write tests for locked startup, unknown state, inactive user, lock between actions, sleep/wake invalidating cached state, permission denial and a second process competing for the lease:
  ```python
  assert guard.snapshot().kind == 'unknown'
  with pytest.raises(DesktopUnavailable): guard.require_unlocked()
  assert second_daemon_can_acquire is False
  ```
- [ ] Run `uv run pytest tests/test_session_guard.py -q`; expect missing-guard failures.
- [ ] Implement the exact lock-state strategy proven in Task 1 through PyObjC. Re-query immediately before actions; notifications wake the coordinator but never prove unlocked by themselves. Advance generation on lock/user/wake transitions. Keep the observer running while awake; do not inhibit lock or sleep.
- [ ] Use `fcntl.flock` on one per-user lock file for the entire daemon/action cycle so two tool instances cannot race a credential swap. Fail immediately for a second daemon. Accessibility calls use bounded traversal and explicit unavailable results, not endless tree scans.
- [ ] Run session guard tests; commit the desktop/lease boundary.

## Task 7: Implement and verify native transcript handoffs

**Files:** Create `conductor/ui.py`, `tests/test_handoff.py`, `tests/fixtures/accessibility/`; update compatibility manifest from Task 1.

**Interfaces:** `HandoffRequest(recovery_id: str, source: BlockedTurn, target: ModelChoice, submission_marker: str)`; `PreparedHandoff(destination_session_id: str, workspace_id: str, target: ModelChoice, transcript_verified: bool)`; `SubmissionEvidence(state: Literal['confirmed','absent','ambiguous'], destination_session_id: str, turn_id: str | None)`; `NativeHandoff.available_models() -> tuple[ModelChoice,...]`, `.prepare(request: HandoffRequest) -> PreparedHandoff`, `.submit(prepared: PreparedHandoff, marker: str) -> None`, `.inspect(recovery: Recovery) -> SubmissionEvidence`.

- [ ] Write tests replaying sanitized AX fixtures through a fake AX client: exact workspace/session chosen despite duplicate titles; native transcript attached; mapped model/effort verified; unrelated draft untouched; wrong workspace rejected; lock before Send prevents it; renamed/missing controls fail closed. Assert no direct Conductor DB writes or private socket calls exist in this adapter.
- [ ] Run `uv run pytest tests/test_handoff.py -q`; expect missing-handoff failures.
- [ ] Implement only the native interaction sequences proven in Task 1. Re-observe identity after every navigation and action. Obtain supported model/effort choices through the verified UI catalog; a missing requested choice stops preparation. Guard credential/UI actions again at their boundaries. No coordinate-only fallback or blind keyboard Send into an unverified composer. Treat foreground/user changes as an interruption and revalidate or stop.
- [ ] Stage this exact continuation text with a non-secret marker: `Continue the unfinished task from the attached transcript. Preserve its requirements and constraints. Check the current workspace and completed actions before repeating any work. Recovery reference: <marker>.` Verify source transcript attachment before submission, including source identity; never substitute a guessed summary.
- [ ] Discover destination IDs through read-only state before/after native creation. A prepared destination must match workspace and model. `inspect` requires the marker in the destination user message and an associated turn/response; a typing attempt or an idle status is not delivery proof. An uncertain result returns ambiguous; never press Send twice to test it.
- [ ] Run handoff tests, including a crash after native creation with no persisted destination ID and a crash immediately after Send. Commit the native adapter.

## Task 8: Connect rules, queue, providers and handoff recovery

**Files:** Create `coordinator.py`, `daemon.py`, `tests/test_coordinator.py`; extend `tests/fakes.py`.

**Interfaces:** `Coordinator.tick(now: datetime) -> TickResult(observed: int, waiting: int, verified: int, attention: int)` and `run_daemon(config: Config, stop: Event) -> int`. Inject reader, store, registry, session guard, handoff adapter and clock. Constructors receive these interfaces explicitly; tests never discover real user paths.

- [ ] Write deterministic integration tests:
  ```python
  coordinator.tick(now)  # two quota-blocked Claude chats
  assert fake_cswap.switches == ['claude-b']
  assert set(fake_handoff.submitted_sources) == {'session-1', 'session-2'}
  assert completed_chat not in fake_handoff.submitted_sources
  ```
  Add locked→unlock, lock after credential switch, original quota reset, all exhausted, user cancellation/manual resume, unknown source account, manual account drift, no mapping, and restart after Send cases. Verify source failures from a completed handoff never create another recovery.
- [ ] Run `uv run pytest tests/test_coordinator.py -q`; expect missing-coordinator failures.
- [ ] Implement ingestion independently of action eligibility. On each actionable tick, reconcile every pending source/destination first, then check unlocked state, current account identity and fresh capacity. Do not call provider probes while locked: a seemingly read-only cswap probe may refresh tokens or prompt for Keychain access. Unknown source-account identity becomes attention. Unknown target capacity retries with bounded backoff; never defaults to ready.
- [ ] Coalesce recoveries targeting the same Claude account; recheck quota between chats. Do not activate another Claude account while an unrelated or just-resumed Claude session is working. Hold incompatible later recoveries until the current account's work settles or it exhausts; never kill sessions. A queue entry may resume on the already active account if it is an eligible fallback, avoiding flip-flop.
- [ ] Persist action intent, activate once, verify account, prepare native destination, persist destination, recheck lock/source, persist submitting, submit once, inspect and record verified. On crashes reconcile receipts and destination evidence; ambiguous create/send becomes attention. Quota rejection from the destination becomes a new bounded recovery with lineage; do not retry an exhausted account until availability changes.
- [ ] Poll events every 2 seconds, UI reconciliation every 2 seconds only while a recovery is active, and quota probes no faster than 60 seconds per account/model except immediately after a verified activation or reported reset. Retry transient failures at 5/15/60/300 seconds; actionable auth/schema/UI failures need attention. Wake on unlock and recheck immediately. Global pause blocks mutations, not observation.
- [ ] Run coordinator tests plus Tasks 2–7 tests once; expect pass. Commit the end-to-end coordinator.

## Task 9: Deliver the CLI and per-user service

**Files:** Create `cli.py`, `service.py`, `tests/test_cli_service.py`, `README.md`, `docs/operations.md`; update entry point and example config.

**Interfaces:** `main(argv: Sequence[str] | None = None) -> int`, `render_launch_agent(executable: Path, config: Path, state_dir: Path) -> bytes`. Commands:

`aisub init`; `config validate`; `accounts list`; `accounts add --id ID --provider PROVIDER --backend-ref REF`; `order set ID...`; `mapping set --from-provider P --from-model M --to-provider P --to-model M [--to-account ID] [--effort E]`; `status [--json]`; `pause`; `resume`; `doctor`; `run [--observe-only]`; `service install|uninstall|status`; `recovery inspect ID`; `recovery cancel ID`.

- [ ] Write CLI tests asserting: init never overwrites existing configuration; order/mapping writes are atomic and validated; status excludes tokens; pause persists across restart; observe-only never mutates credentials/UI; service paths with spaces are preserved; uninstall retains pending work/config and current agent logins; doctor never triggers a login or model request. Exit codes: 0 success, 2 invalid config/arguments, 3 unavailable/attention, 4 already running.
- [ ] Run `uv run pytest tests/test_cli_service.py -q`; expect missing-command failures.
- [ ] Implement argparse commands and readable/JSON status containing source/destination IDs, selected account/model, reason and next check time. `accounts add` registers an existing backend identity; it does not ask for raw tokens or automate browser auth. `resume` retries waiting work after reconciliation; ambiguous submissions remain attention and require inspection, not forced resending.
- [ ] Generate `~/Library/LaunchAgents/com.ai-sub-switch.agent.plist` with an absolute executable path, explicit config/state arguments, user Aqua session restriction, bounded restart throttle and private logs. Use `launchctl` argument arrays, no shell interpolation. Installing a service is an explicit user command; neither dependency install nor init auto-enables automation.
- [ ] Document cswap enrollment and version pin, Accessibility permission for the actual installed runtime, supported Conductor version, lock/sleep/reboot behavior, pause/uninstall, and resolution of mapping/auth/ambiguous-send failures. Provide a dry observation-first setup path. Do not include secrets or actual chat transcripts in diagnostics.
- [ ] Run CLI tests and `uv run aisub --help`; expect command list and all tests passing. Commit the operable CLI/service.

## Task 10: Validate failure recovery and prepare release

**Files:** Create `tests/test_recovery_e2e.py`, `.github/workflows/ci.yml`; update `docs/operations.md` and feasibility report with a versioned acceptance matrix.

**Interfaces:** Consumes the complete application; produces a tested package and documented compatibility/limitations. CI is synthetic and must never require live logins or desktop permissions.

- [ ] Add multi-process temporary-store tests for daemon restart, duplicate instances, database cursor reset, partial UI operations, locked startup, unlock with recovered original quota, missing Keychain access, and manual source continuation. Assert original files remain intact and unrelated sessions receive no messages.
- [ ] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv build`; expect all tests/checks pass and wheel/sdist build. Add CI that runs the same synthetic checks with locked dependencies on macOS.
- [ ] Install the built artifact into an isolated test environment; verify the console entry point, configuration validation and observe-only mode. Tests use synthetic fixtures and a temporary state directory. Do not enable real automation during package verification.
- [ ] Run the live disposable-task acceptance matrix: Claude A→B; both unavailable→Codex with native transcript; two blocked chats/one switch; lock before action; unlock after manual handling; restart after destination preparation. Simulate quota at the adapter boundary where practical rather than consuming subscriptions to exhaustion. Label simulated and real provider evidence separately.
- [ ] Complete one real locked→unlock service test with the user controlling screen lock, and verify successful continuation through native UI. Record tested Conductor/cswap/PyObjC versions and any pending capabilities. Do not call simulated delivery proof of live delivery.
- [ ] Commit release documentation and verification results. Only enable live service for the user's real workspace fleet when explicitly requested; summarize any failed acceptance criterion before expanding scope.

## Self-review and execution handoff

Coverage: ordering/defaults/custom mappings (2), quota/source classification (3), saved subscriptions and custom-provider boundary (4), restart durability (5), lock/unlock and serialization (6), native transcript/model selection (1,7), coordinated recovery/all-exhausted/manual intervention (8), user controls/service (9), acceptance/packaging (10). Five review-focus failures have owning tests above.

Execution recommendation: native implementation in this session, because Tasks 2–8 share a tightly coupled recovery contract and Task 1 may change integration details. Request an independent whole-branch review after validation. A subagent-per-task execution is also possible if the user prefers additional review gates. No implementation is part of writing this plan.

Sources for implementation research: [PyObjC](https://pyobjc.readthedocs.io/en/latest/), [Apple AXUIElement](https://developer.apple.com/documentation/applicationservices/axuielement), [Apple session dictionary](https://developer.apple.com/documentation/coregraphics/cgsessioncopycurrentdictionary()), and the primary links in the design. These establish available integration surfaces; Task 1 must establish actual locked-state and native handoff behavior.
