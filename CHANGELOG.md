# Changelog

All notable changes to the Mondoo Splunk apps are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

## [1.0.0] - 2025-XX-XX

Initial release.
