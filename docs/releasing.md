# Releasing AI Subscription Switcher

The first release is 0.1.0 alpha. The source repository is currently private.
Review the branches, tags, issues, and pull requests you intend to expose before
changing its visibility, or use a dedicated public repository. Update the
package URLs if the public repository differs from the current origin.

1. Review the MIT license and the compatibility notes in the README.
2. Run the Package checks workflow on the release commit. Download the
   `python-distributions` artifact from that run.
3. Verify the artifacts locally with `uvx --from twine twine check --strict dist/*`.
   Install the wheel in a fresh environment and run `aisub init` and
   `aisub validate`.
4. Create a draft GitHub release for `v0.1.0` on the reviewed commit. Use the
   changelog as the release notes. Attach the wheel and source archive, review
   the draft and publish it as a prerelease.
5. For PyPI, configure a [pending Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   once the public repository and publishing workflow are in place. Alternatively,
   upload the verified distributions with `uv publish` from an authorized
   environment. Package name availability is only established at publication.
6. Smoke test the published artifact with
   `uv tool install 'ai-sub-switch[macos]==0.1.0'`.

The CI workflow builds and uploads artifacts to its own run. It does not publish
to PyPI or GitHub Releases. Package checks use synthetic data and need no
Conductor, Claude, Codex or Accessibility permissions.
