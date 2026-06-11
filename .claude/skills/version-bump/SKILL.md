---
name: version-bump
description: Bump the version and cut a GitHub release for the claude-tmux-hop plugin — apply the per-file version-bump rules, update CHANGELOG.md, commit, push, and publish the GitHub release with notes. Use whenever the maintainer wants to release, publish, or version this repo — "버전 올려", "버전 범프", "릴리스 만들어/해줘", "배포해", "새 버전 내자", "cut a release", "publish a new version", "bump the version", "do a release". Trigger even when no version number is named or "release" isn't said explicitly but the intent is clearly to ship the current changes. Only applies inside the claude-tmux-hop repository.
---

# version-bump

Release the `claude-tmux-hop` plugin: decide the new version, bump only the files
the change actually affects, record the changes in `CHANGELOG.md`, commit, push,
and publish a GitHub release whose notes are the changelog section. **This skill is
the single source of truth for the project's version-bump rules** (they used to
live in `CLAUDE.md`).

## When to use

Trigger when the maintainer wants to ship the current `main` state — bump the
version and create a release. Phrases: "버전 올려/범프", "릴리스/배포", "publish",
"cut a release", and the like.

Do **not** trigger outside the claude-tmux-hop repo, or for unrelated git tagging.

## Versioning model (what to bump, and why)

Three version fields, kept in lockstep where they overlap:

- `pyproject.toml` — the Python package.
- `.claude-plugin/plugin.json` — the Claude Code plugin.
- `.claude-plugin/marketplace.json` (`plugins[*].version`) — **always mirrors plugin.json**.

Bump only what the change touches:

| Change | pyproject | plugin.json | marketplace.json |
|---|---|---|---|
| `src/**` Python | ✓ | ✓ | ✓ |
| plugin files not in `src/` (`hop.tmux`, `hooks/**`, `plugin.json`, shipped `skills/**`) | – | ✓ | ✓ |
| pure `marketplace.json` metadata (name/description/author) | – | – | – |

Why: the plugin ships and executes the Python via `bin/claude-tmux-hop`, so any
`src/` change is also a plugin-behavior change → all three. Plugin-side files that
aren't Python don't move the Python package version. marketplace.json's version
exists only to mirror plugin.json. All three share one number — when buckets change
together, take the highest (Python → all three).

Bump nothing for changes to non-shipped files — docs and dev tooling (`README.md`,
`CLAUDE.md`, `.github/`, `.claude/`, this skill) don't ship in the plugin and carry
no version.

## Choosing the new version (semver)

- Breaking change to CLI / `@hop-*` options / behavior → **major**.
- New user-facing feature → **minor**.
- Fix, refactor, docs, internal-only → **patch**.

Propose a version, then confirm with the maintainer before applying — this repo has
historically used patch bumps even for small features, so don't assume. The current
version is in `pyproject.toml`.

## How to run

0. **Preconditions.** Confirm you're in the claude-tmux-hop repo root and `gh` is
   authenticated (`gh auth status`). There is no release CI — the
   `.github/workflows/` files are Claude bots, not release automation — so this
   skill performs the release directly. (If a release workflow is ever added,
   check it first and defer to it.)

1. **Scope the changes.** Find the last release tag
   (`git describe --tags --abbrev=0`) and read `git log <last-tag>..HEAD --oneline`
   plus `git status`. From the changed paths pick the version files to bump (table
   above); from the commit types pick the semver level.

2. **Confirm with the maintainer.** State the proposed new version, which files
   you'll bump, and a one-line summary of the release. Proceed once confirmed —
   steps 5-7 push and publish, so don't skip this.

3. **Bump the version files** to the new number (only the ones the table selects).
   Keep `marketplace.json` equal to `plugin.json`.

4. **Update `CHANGELOG.md`** (create it if missing — "Keep a Changelog" style, a
   `## [X.Y.Z] - YYYY-MM-DD` section grouped by Added / Changed / Fixed / Removed).
   Write it for someone reading release notes: summarize the *intent* of the
   changes, not raw commit subjects. This section is reused verbatim as the GitHub
   release body, so make it stand alone.

5. **Commit.** If the feature work is already committed, make one
   `chore: bump version to X.Y.Z` commit for the version files + changelog. If the
   work is still uncommitted, commit it first (conventional-commit message:
   `feat(...)`, `fix(...)`, etc.), then the bump commit.

6. **Push** to `main`.

7. **Create the GitHub release** with the changelog section as the body:
   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <path-to-section>
   ```
   Tag format is `vX.Y.Z` (matches existing tags). Write the changelog section to a
   temp file for `--notes-file`, or pass it inline with `--notes`.

8. **Report** the release URL to the maintainer.

## Notes

- Push and release are hard to undo — always do the step-2 confirmation first.
- **Release backlog**: tags/releases currently lag the version files (releases stop
  at the last tag while `chore: bump` commits kept going). Base the changelog on
  commits since the last *tag*, and if intermediate versions were never released,
  ask the maintainer whether to fold them into this changelog or cut catch-up
  releases — don't silently bundle a huge range.
- `bin/claude-tmux-hop` runs `src/` directly (no build step), so there's nothing to
  compile or publish beyond the git push + GitHub release.
