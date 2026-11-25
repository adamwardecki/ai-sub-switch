# Changelog

## 0.1.0

- Watch a selected local Conductor chat for Claude subscription quota failures.
- Queue recovery while macOS is locked and recheck the chat after unlock.
- Switch between enrolled Claude subscriptions and retry in the same chat when the model and effort stay the same.
- Continue in a new tab with the source transcript for a model or provider change.
- Configure subscription order and model mappings in TOML. Preview the choice before enabling live recovery.
- Keep a local record of recovery attempts and stop for inspection when a submission cannot be confirmed.

The live recovery paths were exercised on Conductor 0.100.0. This is an alpha release for macOS.
