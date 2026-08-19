#!/usr/bin/env bash
# bump-version.sh – Set the release version across every file that carries it.
#
# Usage:
#   ./bump-version.sh 1.1.0        Set version to 1.1.0 and refresh build numbers
#   ./bump-version.sh --show       Print the current values and exit
#
# A release version lives in five places. release.yml refuses to publish unless
# they all agree with the git tag, so this script exists to move them together:
#
#   <app>/default/app.conf   [launcher] version
#   <app>/default/app.conf   [id] version
#   <app>/app.manifest       info.id.version
#
# It also bumps [install] build, which Splunk uses to decide whether an install
# is an upgrade. build must strictly increase between releases; we use the
# current Unix timestamp.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
APPS=("TA-mondoo" "mondoo_app")

show() {
    for app in "${APPS[@]}"; do
        echo "$app:"
        grep -nE "^(version|build)\s*=" "$app/default/app.conf" | sed 's/^/    app.conf:/'
        python3 -c "import json;print('    app.manifest: version =', json.load(open('$app/app.manifest'))['info']['id']['version'])"
    done
}

case "${1:-}" in
    --help|-h) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --show)    show; exit 0 ;;
    "")        echo "error: version required (e.g. ./bump-version.sh 1.1.0)" >&2; exit 1 ;;
esac

VERSION="$1"
if ! printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "error: '$VERSION' is not semver (expected N.N.N)" >&2
    exit 1
fi
BUILD="$(date -u +%s)"

for app in "${APPS[@]}"; do
    conf="$app/default/app.conf"
    manifest="$app/app.manifest"

    # Both [launcher] version and [id] version, plus [install] build.
    python3 - "$conf" "$VERSION" "$BUILD" <<'PY'
import re, sys
path, version, build = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as fh:
    text = fh.read()
text, nver = re.subn(r"(?m)^version\s*=\s*\S+", f"version = {version}", text)
text, nbld = re.subn(r"(?m)^build\s*=\s*\S+", f"build = {build}", text)
if nver != 2:
    sys.exit(f"{path}: expected 2 version keys, changed {nver}")
if nbld != 1:
    sys.exit(f"{path}: expected 1 build key, changed {nbld}")
with open(path, "w") as fh:
    fh.write(text)
print(f"  {path}: version = {version} (x2), build = {build}")
PY

    # Surgical edit of info.id.version only. A json.load/json.dump round-trip
    # reflows the compact arrays elsewhere in the file, making every release a
    # noisy diff; rewrite just the one line, then re-parse to prove it is still
    # valid JSON.
    python3 - "$manifest" "$VERSION" <<'MANIFEST_PY'
import json, re, sys
path, version = sys.argv[1], sys.argv[2]
with open(path) as fh:
    text = fh.read()

def bump(match):
    return re.sub(r'("version"\s*:\s*")[^"]*(")', r'\g<1>' + version + r'\g<2>', match.group(0))

new_text, n = re.subn(r'"id"\s*:\s*\{[^}]*\}', bump, text, count=1)
if n != 1:
    sys.exit(f"{path}: could not locate the info.id block")
if json.loads(new_text)["info"]["id"]["version"] != version:
    sys.exit(f"{path}: rewrite did not take effect")
with open(path, "w") as fh:
    fh.write(new_text)
print(f"  {path}: info.id.version = {version}")
MANIFEST_PY
done

echo ""
echo "Now add a '## [$VERSION] - $(date -u +%F)' section to CHANGELOG.md,"
echo "commit, then:  git tag -a v$VERSION -m \"Mondoo Splunk apps $VERSION\" && git push origin v$VERSION"
