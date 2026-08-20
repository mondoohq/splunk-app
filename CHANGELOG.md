# Changelog

All notable changes to the Mondoo Splunk apps are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `.github/workflows/release.yml`: tag-driven release pipeline. Verifies the tag
  against both `app.conf` files and both `app.manifest` files, re-runs the CI
  gates against the tagged commit, packages both apps, checks the packages for
  `local/`, `tests/` and token-shaped strings, and publishes a GitHub release
  with checksums. Tag pushes previously matched no workflow trigger at all.
- `vet.sh --package-only`: packages without running AppInspect, so release
  artifacts and locally built ones come from the same code path.
- `bump-version.sh`: sets the version across all five fields and refreshes
  `[install] build` in one step. The release workflow now also rejects a build
  number that has not increased since the previous tag — Splunk uses it to
  decide whether an install is an upgrade, and it had been static since the
  apps were created.

### Changed
- `.github/workflows/ci.yml`: exposed via `workflow_call` so the release
  workflow reuses the pipeline instead of duplicating it.
- `README.md`: added an Acknowledgments section crediting Alexander Skripnik
  and Ilker Duman of Netdescribe, whose work on these apps landed inside the
  squashed initial commit and so is not visible in the git history.

## [1.0.0] - 2026-08-19

Initial release.

### Added
- `LICENSE` and per-app LICENSE copies (Apache-2.0).
- `mondoo_app/app.manifest` with dependency on `TA-mondoo` and CIM mappings.
- `.gitignore` covering Python caches, `local/` overrides, AppInspect artifacts and IDE files.
- `CHANGELOG.md`.

### Changed
- `TA-mondoo/default/app.conf`: added `[package] check_for_updates`, `[install] state`, `[install] requires_splunk_version`, and richer description.
- `mondoo_app/default/app.conf`: filled `[package] id`, description, and install metadata.
- `TA-mondoo/app.manifest`: expanded `commonInformationModels`, tightened `targetWorkloads` (no indexers — modular input must run on a single instance), corrected author/company.

### Removed
- Committed `.DS_Store`.
- Stray file `mondoo mark.svg` (renamed to `mondoo_mark.svg`).

[Unreleased]: https://github.com/mondoohq/splunk-app/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mondoohq/splunk-app/releases/tag/v1.0.0
