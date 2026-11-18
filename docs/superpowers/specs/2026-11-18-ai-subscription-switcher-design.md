# AI subscription switcher — proposed design

Status: awaiting user review. This document does not authorize implementation.

## Intent

Keep coding tasks moving when a paid AI subscription runs out of quota. The initial user has two Claude subscriptions and one OpenAI subscription, used through Conductor on macOS. Today switching Claude accounts requires `/login` and browser authentication. The product should retain enrolled logins locally, choose the next subscription in a user-defined order, and continue unfinished work automatically.

## Confirmed requirements

- Use existing subscriptions, rather than requiring API-billed accounts.
- Let the user order subscription accounts and configure provider/model mappings.
- Provide defaults plus custom configuration and an extensible provider design.
- Prefer tool independence, with Conductor as the first integration.
- Use Conductor's existing continuation functionality: new tab, previous transcript attached, chosen destination model.
- One machine-wide active Claude account is acceptable. Separate simultaneous account profiles are unnecessary for this use case.
- A task should continue automatically after a limit, without repeated browser authentication in normal operation.
- Resume pending handoffs when the Mac unlocks. Locked-screen handoff is not required.

## Proposed v1 defaults — assistant decisions for review

- macOS local Conductor only; initially Claude Code and Codex subscription sessions.
- A local command-line tool plus a per-user background service. Human-editable configuration and commands for accounts, order, mappings, status, pause, resume, and diagnostics. A graphical settings interface is deferred.
- Reactive switching after confirmed quota exhaustion; no proactive threshold switching.
- Keep a task on its current account until another switch is needed. Do not move it back merely because a preferred account resets.
- Defer both credential changes and chat continuation while locked. Monitoring may continue while the Mac is awake.
- At unlock, recheck quota. If the original account has recovered, prefer resuming there; otherwise follow the configured fallback order, skipping accounts known to be unavailable.
- If all accounts are unavailable, keep the task pending until the earliest known reset, with bounded retry when reset information is absent. Do not fall back to paid API usage implicitly.
- Persist pending work across service restarts, logout, and reboot; perform actions only after the user logs in, the desktop is unlocked, and Conductor is available. No execution is promised while asleep or powered off.

## Approaches considered

1. Reuse an account switcher and add our rules/Conductor adapter. Less credential code to maintain; depends on upstream compatibility. This is the recommended basis following the user's request to investigate existing switchers.
2. Implement account storage and rotation ourselves. More control, but requires independently handling Keychain, rotating tokens, identity updates, and concurrency. Deferred.
3. Use a local proxy or a separate agent launcher. Potentially broader automation, but changes the execution/handoff mechanism the user specifically wants to retain. Not selected for v1.

Recommended dependency: `realiti4/claude-swap` (`cswap`), invoked through its CLI/JSON interface. The dependency recommendation is an assistant proposal, not a separate user-approved commitment. Use explicit account selection, not its autonomous switching loop, so there is one owner of ordering and recovery.

## Evidence and feasibility boundary

Research on installed Conductor 0.94.1 established:

- Local SQLite state contains sessions, workspace associations, turn IDs, live state, and agent events.
- Claude events include `rate_limit_event` with `rate_limit_info.status` equal to `allowed`, `allowed_warning`, or `rejected`, quota window and reset time. Assistant API error events also carry `error=rate_limit`.
- `overageStatus=rejected` occurs even on allowed subscription events; it is not a trigger.
- Accessibility exposes the model picker and marks cross-agent choices as opening in a new chat. Chat menus expose concise/full transcript copying.
- Available Conductor MCP tools do not provide chat creation or continuation. The documented public API targets cloud workspaces.

Not yet verified: the complete native transcript handoff, destination submission, credential pickup after swapping, and reliable lock/unlock detection in the eventual service. No live account swap or continuation has been performed.

The first implementation milestone must be a bounded feasibility prototype using a disposable task. Verify the native continuation action, attachment, destination model, and credential pickup before building automatic fleet-wide recovery. If that cannot be made reliable, report the blocker and revisit the design; do not silently replace it with database writes, private IPC, or a separate CLI agent. The approval of this design covers planning that prototype, not arbitrary changes to unrelated chats.

## Components

### Configuration and model mappings

Represent accounts independently from providers: stable local ID, display label, provider type, backend account reference, and enabled flag. Credentials never appear in the configuration.

The ordered account list is the user's primary rule. Support model mappings keyed by source provider/model and destination provider, with an optional destination-account override. Keep the same model for Claude-to-Claude unless explicitly overridden. Cross-provider defaults are suggested capability matches, not claims of identical models or reasoning behavior. Validate supported destination selections against the installed integration. An absent or unsupported mapping blocks that handoff with a clear status rather than silently downgrading.

Custom configuration can add accounts, ordering, mappings, and provider settings. An entirely new authentication protocol or tool integration requires an adapter; a provider name or URL alone does not confer support. Provider extensions declare account availability, activation, and model capabilities. Actual supported providers in v1 are Claude and OpenAI/Codex.

### Account backend

Enroll Claude accounts through normal authentication once, then register them with cswap. Keep credentials in its macOS Keychain backend. Our tool stores only account references. Handle expired/revoked logins as needing user attention; never promise browser authentication will never recur.

The single existing Codex account can retain its normal Conductor authentication. Confirm its availability when selected; no multi-account Codex credential rotation is required in v1. Future adapters may add it.

Serialize Claude account changes with a machine-wide coordinator lock. Let cswap handle its credential locks and token freshness. An idle or quota-blocked session may still have a live process: do not assume all processes exited. Verify target-account activation before resuming work. Do not terminate unrelated running sessions to force a swap; wait or surface a blocked condition if the backend requires it.

### Conductor observer

Read the local database in read-only mode through a version/schema-checked adapter. Tail new events with a durable cursor; baseline existing history on first enable so historical failures are not replayed. On restart, reconcile the persisted cursor and queue against current session/turn state.

Use structured quota rejection plus current turn state. Distinguish subscription exhaustion from temporary throttling, network errors, authentication errors, and normal quota warnings. Child-agent failures alone do not justify restarting a parent chat. Preserve source session, workspace, turn and event identifiers for every pending recovery.

Observe all local Conductor workspaces while automation is enabled, but act only on confirmed quota-blocked turns. Exclude archived/hidden sessions by default. Never automatically continue completed, manually cancelled, user-paused, or superseded work. Where those states cannot be distinguished reliably, mark the item for attention instead of submitting a guess.

### Persistent queue and coordinator

Keep tool-owned durable state separate from Conductor's database. Queue states include pending, waiting-for-unlock, waiting-for-capacity, preparing, destination-prepared, submitted, verified, cancelled and needs-attention.

Deduplicate using source session/turn identity. Coalesce failures from the same active Claude account into one account-switch operation; individual chats retain their own recovery records.

Before each action, confirm unlocked desktop, available application, source turn still blocked, account availability and valid model mapping. A newly detected lock pauses further UI actions. Reconcile completed steps after unlock instead of restarting the whole handoff.

Do not replay ambiguous sends automatically. Record the prepared destination and inspect its transcript/state to determine whether the continuation started. If that cannot be established, require attention. This provides at-most-once automatic submission where evidence is ambiguous; it does not claim transactional exactly-once delivery through a UI.

### Native continuation adapter

Use macOS accessibility controls for Conductor navigation and its native continuation workflow. Resolve workspace/session identity, not only display titles. Serialize actions that share the application window and avoid typing into a composer with an unrelated user draft.

For provider changes, open the native destination tab with the source transcript attached and the mapped model selected. The continuation prompt asks the target to pick up the unfinished task from the attached history, honoring existing constraints and checking current files before repeating actions.

For a same-provider account switch, investigate whether Conductor supports safely resuming the original task after credential pickup; prefer the user's requested native new-tab handoff unless a different same-provider path is explicitly accepted after the prototype. Do not silently substitute a bare process restart.

Verify destination workspace, model/provider, attachment, and submitted turn. Keep the original chat available. Record source-to-destination linkage so stale source failures cannot trigger another handoff. Preserve workspace files without resetting or automatically committing them.

## Unlock recovery

1. Reconcile pending entries with current source and destination state.
2. Drop recoveries made obsolete by manual work, cancellation, or an existing successful handoff.
3. Refresh account availability and apply the original-account-recovered rule.
4. Perform any required global credential change once.
5. Continue eligible chats sequentially through native UI actions and record verification.

Lock-state detection must not weaken macOS security or disable screen locking. If Keychain needs interactive approval, pause with an actionable status.

## Verification and acceptance

- Two Claude accounts can be enrolled and switched without routine browser login; a new Conductor agent demonstrably uses the selected account.
- A confirmed Claude A limit selects Claude B, keeping the configured model.
- When both Claude accounts are unavailable, a disposable task continues through Conductor's native transcript attachment to the configured Codex model.
- Two blocked chats cause one global account switch and no duplicate submissions.
- While locked, recoveries remain durable and no UI submission occurs. On unlock, still-valid work resumes.
- Service restart retains recoveries; historical completed/cancelled/manual-resumed chats do not restart.
- Allowed/warning/overage-only events and subagent-only failures do not cause inappropriate handoffs.
- Missing mappings, expired authentication, unavailable accounts, changed database schemas, and changed UI controls produce explicit waiting/attention states.
- A crash after destination preparation or submission does not create duplicate work on retry.

Use synthetic events and fake credential backends for automated tests. Live acceptance uses a disposable task and enrolled accounts; avoid consuming real quota to manufacture a limit where a controlled test can establish the same behavior.

## Non-goals and failure conditions

No billing/subscription purchases, API-payment fallback, locked-screen UI automation, cloud migration, Windows/Linux client, or guaranteed support for arbitrary tools in v1. No writes to Conductor's database, private-IPC dependency, plaintext credential export, or automatic installation of arbitrary provider code.

Resuming the wrong chat, silently losing transcript context, repeating side effects after an uncertain send, or changing unrelated active sessions are unacceptable outcomes.

## Left to the builder

Choose implementation language, packaging, queue database, lock-state API, retry/backoff values and accessibility library after the feasibility test. Keep the rules/provider layer independent from Conductor-specific observation and UI code. Pin and document supported cswap/Conductor versions, and preserve a path to additional tool adapters.

## References

- https://github.com/realiti4/claude-swap
- https://github.com/realiti4/claude-swap/blob/main/docs/json-output.md
- https://github.com/realiti4/claude-swap/blob/main/docs/auto-switch.md
- https://www.conductor.build/docs/api
- https://www.conductor.build/docs/reference/deep-links
- https://github.com/hyldmo/conductor-remote (prior art; its documented private send path is currently broken)
- Local research notes: `.context/conductor-research/findings.md`
