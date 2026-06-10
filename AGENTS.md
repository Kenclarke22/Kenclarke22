# AGENTS.md

## Project overview

This repository (`Kenclarke22/Kenclarke22`) is a **GitHub profile special repository**. It contains only `README.md`, which is displayed on the GitHub user profile. There is no application source code, package manifest, Docker setup, or test suite.

## Cursor Cloud specific instructions

### Services

| Service | Required | Notes |
|---------|----------|-------|
| *(none)* | — | No servers, databases, or background processes exist in this repo |

### Development workflow

There is nothing to build, lint, or test. Typical agent tasks here are limited to editing `README.md` (profile bio, links, badges).

### Verification

After changes to `README.md`:

1. `git diff README.md` — review edits
2. `git status` — confirm only intended files changed
3. Preview locally (optional): `cat README.md` or open the file in the editor

GitHub renders the README on the profile page after push to `main`; there is no local dev server for this repo.

### Environment variables / secrets

None required.
