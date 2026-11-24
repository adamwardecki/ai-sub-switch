# Conductor 0.96.0 feasibility

Checked November 24, 2026 in a local Conductor worktree. Task 1 is still open. The live Claude account change, runner permissions, lock transitions, and successful Claude destination turn remain to be verified.

| Check | Evidence | Result |
| --- | --- | --- |
| Native Claude → Codex transcript handoff | A disposable Sonnet 5.5 turn hit a real five-hour limit. A GPT-6.2 Sol tab in the same workspace received Conductor's transcript attachment. Codex read the Markdown file and returned a synthetic marker that appeared only in the source chat. | Passed with an explicit instruction to read the attachment. |
| Native Codex → Claude draft | The source model picker opened a new Sonnet 5.5 tab. The blank composer accepted the Codex transcript via **Add chat transcripts**. | Draft verified. Claude quota blocked a live destination test. |
| Native Claude → Claude draft | The new Sonnet 5.5 composer accepted the Claude source transcript while preserving the source tab. | Draft verified. Credential pickup and completed destination turn remain unverified. |
| Claude quota detection | The disposable source produced a rejected `five_hour` event, a root assistant `rate_limit` error, and a terminal 429. All correlated to the same turn. The session status settled to `idle`. | Live signal verified. |
| Codex quota detection | The bundled protocol schema includes `usageLimitExceeded`. Local events store Codex payloads under `rawPayload.event`. Observed completed and failed turn events, but no Codex subscription exhaustion example was found in the sampled events. | Protocol evidence only. Keep unrecognized local representations out of automatic switching. |
| Codex subscription probe | Installed App Server initialized, `account/read` returned `chatgpt`, and `account/rateLimits/read` returned quota windows. It exited cleanly when stdin closed. | Passed without a model request. |
| `cswap` activation | Installed version 1.2.0 in a workspace-only virtual environment. Verified command help, response contract, and source. No enrolled account pair was available at the start of testing. | Requires the user's two enrolled logins. |
| macOS access and lock state | Python 3.12.13 imported PyObjC 13.0 ApplicationServices and Quartz. The observed desktop was on-console and logged in, with no lock key in the session dictionary. `AXIsProcessTrusted()` returned false for the actual Python probe process. | Bindings work. Runner trust and lock, unlock, wake, and inactive-user transitions remain unverified. |

## Native interaction on this version

1. Resolve the workspace and source session by ID. The active local chat URL in the accessibility tree includes both. Conductor's `GetSessions` tool also returns a native deep link with both IDs.
2. Open the source **Change agent** menu, search for the target model, and select it. A different harness opens a new tab. For the same harness and model, use the new-chat button and verify the target model in the composer.
3. In the empty destination, select the source from **Add chat transcripts**. Verify the source transcript pill, model and selected destination before typing. A display title alone is insufficient when the source choice is ambiguous.
4. Focus the composer explicitly before adding the continuation text. The clipboard paste timed out when the composer lacked focus. Reinspect on timeout before proceeding.
5. Ask the destination to **read the attached transcript** before continuing. The attachment appears as a file reference in the sent turn. The first test forbade tools and Codex could not recover the marker. A follow-up explicitly permitting it to read the attached file returned the expected marker.
6. Submit once. Confirm the destination turn from its persisted message and event records. In the trial, the sent attachment was named `session-transcript-<source-session-id>.md`, which provides a stronger cross-check than the draft label alone.

The first-turn quota error in the disposable chat exposed a **Retry** button. **Retry in new chat** was present for quota failures in longer chats elsewhere in the app. The latter control may not be available on every blocked turn, so the tested path uses Conductor's native new-tab and transcript controls. This path was verified through assisted UI interaction. The application service still needs to demonstrate the same sequence with its own Accessibility permission.

## Read-only observation and availability

- SQLite opened successfully with URI `mode=ro` and `PRAGMA query_only=ON`. `session_events` retained `session_id`, `session_index`, `event_id`, `event_type`, `content`, `received_at`, and `reverted_at`. User events and quota errors carried the turn ID. The test source's assistant status and result must both be considered because the terminal Claude payload had `subtype=success` alongside `is_error=true` and `api_error_status=429`.
- Recheck the latest sent user turn and cancellation before any mutation. A later turn supersedes an earlier failure even if the historical Retry button still appears. Ordinary Codex completions have a nested `turn.completed` event. A generic failure, `idle`, or a rate event for a child cannot classify the parent as blocked.
- Codex CLI 0.169.0 answered newline-delimited `initialize`, `initialized`, `account/read` with `refreshToken: false`, then `account/rateLimits/read`. Rate-limit results contained `ordinaryUsageAllowed`, `rateLimits`, `rateLimitsByLimitId`, and primary window fields `usedPercent`, `windowDurationMins`, and `resetsAt`. Account identifiers and usage values were omitted from the report. The test used the installed Conductor-provided CLI path. [Codex App Server protocol](https://developers.openai.com/codex/app-server)
- Claude Code was 2.1.331. Both cswap 1.2.0 and PyObjC 13.0 declare MIT in the installed package metadata. `cswap list/status/switch --json` use schema version 1 and the documentation exposes usage freshness and reasons for stale or unavailable values. The installed cswap implementation can fall back to files after Keychain errors. A successful exit alone cannot verify Keychain-backed operation. Validate the credential storage path and resulting identity during live acceptance. [cswap JSON reference](https://github.com/realiti4/claude-swap/blob/main/docs/json-output.md)

## Open verification

- Enroll two existing Claude logins through normal sign-in. Verify each can be restored with `cswap` and a fresh Conductor Claude process uses the chosen account. Check that no unrelated Claude session is running before changing the shared login.
- Complete Claude destination turns for both source providers using the already-available native attachment controls. Validate what works when the initial continuation prompt explicitly asks the destination to read the file.
- Grant Accessibility to the chosen runner, then collect an unlocked → locked → unlocked trace and check recovery on wake. Missing or contradictory state should keep UI operations pending.
- Verify manual cancellation and pause signals on disposable turns. Confirm exact local Codex quota event classification from a genuine event or a verified transformation of the installed protocol. Treat unproven cases as needing attention.

Conductor's bundled controls now list local sessions and models. `CreateSession` and `SendSessionMessage` accept message text but do not expose the native source transcript attachment. The bundled standalone CLI describes a cloud API. Neither establishes a headless local handoff for this version.
