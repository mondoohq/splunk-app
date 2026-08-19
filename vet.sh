#!/usr/bin/env bash
# vet.sh – Package both apps and run splunk-appinspect (precert mode).
#
# Usage:
#   ./vet.sh                  Package + vet both apps
#   ./vet.sh --dry-run        Only show what would be packaged
#   ./vet.sh --list-excludes  Show the exclude patterns and exit
#   ./vet.sh --help           Show this help
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
APPS=("TA-mondoo" "mondoo_app")

# ── Exclude patterns (explicit, documented) ──────────────────────────
# Anything in this list is filtered out of the .tar.gz handed to AppInspect.
# These are dev-only artifacts that should never ship to Splunkbase.
EXCLUDES=(
    '__pycache__'        # Python bytecode caches
    '*.pyc'              # Python bytecode files
    '.venv'              # Local virtualenvs from tests/setup_venv.sh
    'tests'              # Unit + live API tests (CI runs these separately)
    '.git'               # Git metadata
    '._*'                # macOS resource forks
    '.DS_Store'          # macOS finder metadata
    '.bump'              # bump2version state file (if present)
    'local'              # Per-instance overrides; defaults only ship in default/
    'local.meta'         # ditto for permissions
    'lib'                # Vendored deps (none today, but reserved)
)

DRY_RUN=0
case "${1:-}" in
    --help|-h)
        sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    --list-excludes)
        printf '%s\n' "${EXCLUDES[@]}"
        exit 0
        ;;
    --dry-run)
        DRY_RUN=1
        shift
        ;;
esac

# Build the rolling --exclude='...' flag list once.
TAR_EXCLUDES=()
for pat in "${EXCLUDES[@]}"; do
    TAR_EXCLUDES+=("--exclude=$pat")
done

# ── Ensure splunk-appinspect is available ────────────────────────────
if [ "$DRY_RUN" -eq 0 ] && ! command -v splunk-appinspect &>/dev/null; then
    echo "splunk-appinspect not found. Installing via pip..."
    pip3 install splunk-appinspect
fi

# ── Clean previous build artifacts ───────────────────────────────────
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

# ── Package and vet each app ────────────────────────────────────────
EXIT_CODE=0
for APP in "${APPS[@]}"; do
    APP_DIR="$SCRIPT_DIR/$APP"
    if [ ! -d "$APP_DIR" ]; then
        echo "WARNING: $APP_DIR not found, skipping."
        continue
    fi

    echo ""
    echo "================================================================"
    echo "  Packaging: $APP"
    echo "================================================================"

    TARBALL="$BUILD_DIR/${APP}.tar.gz"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] Would create $TARBALL with these files:"
        COPYFILE_DISABLE=1 tar cf - "${TAR_EXCLUDES[@]}" -C "$SCRIPT_DIR" "$APP" | tar tf - | sed 's/^/  /'
        echo "[dry-run] (no tarball written, no AppInspect run)"
        continue
    fi

    COPYFILE_DISABLE=1 tar czf "$TARBALL" "${TAR_EXCLUDES[@]}" -C "$SCRIPT_DIR" "$APP"

    echo "  Created: $TARBALL"

    echo ""
    echo "================================================================"
    echo "  Vetting: $APP"
    echo "================================================================"

    REPORT_JSON="$BUILD_DIR/${APP}-appinspect.json"
    splunk-appinspect inspect "$TARBALL" \
        --output-file "$REPORT_JSON" \
        --data-format json \
        --mode precert || true

    # ── Parse results ────────────────────────────────────────────────
    echo ""
    echo "  Results for $APP:"
    echo "  ────────────────────────────────────────────────────"

    python3 - "$REPORT_JSON" "$APP" <<'PYEOF' || EXIT_CODE=1
import json, sys

report_path, app_name = sys.argv[1], sys.argv[2]
with open(report_path) as f:
    data = json.load(f)

reports = data.get("reports", [])
if not reports:
    print("  No report data found.")
    sys.exit(1)

report = reports[0]
summary = report.get("summary", {})

passed   = summary.get("success", 0)
failed   = summary.get("failure", 0)
errors   = summary.get("error", 0)
warnings = summary.get("warning", 0)
skipped  = summary.get("skipped", 0)
manual   = summary.get("manual_check", 0)
na       = summary.get("not_applicable", 0)

total = passed + failed + errors + warnings + skipped + manual + na
print(f"  Total checks: {total}")
print(f"  Passed: {passed}  |  Failed: {failed}  |  Errors: {errors}  |  Warnings: {warnings}")
print(f"  Skipped: {skipped}  |  Manual: {manual}  |  N/A: {na}")

# Show failures and errors
issues = []
for group in report.get("groups", []):
    for check in group.get("checks", []):
        result = check.get("result", "")
        if result in ("failure", "error", "warning"):
            issues.append({
                "result": result.upper(),
                "name": check.get("name", "?"),
                "desc": check.get("description", ""),
                "msgs": [m.get("message", "") if isinstance(m, dict) else str(m)
                         for m in check.get("messages", [])]
            })

if issues:
    print("")
    print(f"  Issues ({len(issues)}):")
    print(f"  ────────────────────────────────────────────────────")
    for issue in issues:
        print(f"  [{issue['result']}] {issue['name']}")
        if issue['desc']:
            print(f"           {issue['desc']}")
        for msg in issue['msgs'][:3]:
            print(f"           -> {msg}")
else:
    print("")
    print("  No issues found.")

sys.exit(1 if (failed + errors) > 0 else 0)
PYEOF
done

echo ""
echo "================================================================"
echo "  Reports in: $BUILD_DIR/"
echo "================================================================"
ls -lh "$BUILD_DIR"

exit $EXIT_CODE
