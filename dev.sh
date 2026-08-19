#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# dev.sh — one-stop CLI for the local Splunk dev rig.
#
# Quick start:
#   ./dev.sh up
#   ./dev.sh configure /path/to/mondoo-service-account.json
#   ./dev.sh search 'index=mondoo | stats count by sourcetype'
#
# Run ./dev.sh help for the full command list.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Defaults (override via env or .env file picked up by docker compose) ────
SPLUNK_CONTAINER="${SPLUNK_CONTAINER:-splunk-mondoo-dev}"
SPLUNK_PASSWORD="${SPLUNK_PASSWORD:-changeme123!}"
SPLUNK_PORT="${SPLUNK_PORT:-8000}"
SPLUNK_INDEX="${SPLUNK_INDEX:-mondoo}"
SPLUNK_VERSION="${SPLUNK_VERSION:-9.3}"
ETL_DIR="${ETL_DIR:-$SCRIPT_DIR/.etl-export}"
export SPLUNK_CONTAINER SPLUNK_PASSWORD SPLUNK_PORT SPLUNK_VERSION

# ── Helpers ─────────────────────────────────────────────────────────────────
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }

die() { red "ERROR: $*" >&2; exit 1; }

require_docker() {
    command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
    docker compose version >/dev/null 2>&1 || die "docker compose v2 is required."
}

container_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$SPLUNK_CONTAINER" 2>/dev/null || echo false)" = "true" ]
}

require_running() {
    container_running || die "Container $SPLUNK_CONTAINER is not running. Try: $0 up"
}

splunk_in() {
    # Run a splunk command as the splunk user inside the container.
    docker exec -u splunk "$SPLUNK_CONTAINER" /opt/splunk/bin/splunk "$@" -auth "admin:$SPLUNK_PASSWORD"
}

wait_ready() {
    # Generous timeout — on Apple Silicon / ARM the splunk/splunk image only
    # publishes amd64, so Docker emulates and the first boot can take 5+ min.
    local max_iters=120
    printf "Waiting for splunkd to be ready (up to %d minutes)" $((max_iters * 5 / 60))
    local i
    for i in $(seq 1 "$max_iters"); do
        if docker exec "$SPLUNK_CONTAINER" /opt/splunk/bin/splunk status 2>/dev/null | grep -q "is running"; then
            green " ✓"
            return 0
        fi
        sleep 5
        printf "."
    done
    red " timed out after $((max_iters * 5 / 60)) minutes"
    docker compose logs --tail=80 splunk
    exit 1
}

wait_web_ready() {
    # Splunk Web takes noticeably longer than splunkd to come up — especially
    # under x86_64 emulation on ARM. The `splunk restart` command's built-in
    # web wait is too aggressive and produces a misleading WARNING when it
    # times out, so we poll from outside the container instead.
    local max_iters=60
    printf "Waiting for Splunk Web on :%s to be ready" "$SPLUNK_PORT"
    local i code
    for i in $(seq 1 "$max_iters"); do
        code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 3 \
            "http://localhost:$SPLUNK_PORT/en-US/account/login" 2>/dev/null || echo 000)
        if [ "$code" = "200" ] || [ "$code" = "303" ] || [ "$code" = "302" ]; then
            green " ✓"
            return 0
        fi
        sleep 5
        printf "."
    done
    yellow " timed out — Web may still be coming up. Try the browser shortly."
    return 0
}

ensure_index() {
    if splunk_in list index "$SPLUNK_INDEX" >/dev/null 2>&1; then
        green "Index $SPLUNK_INDEX already exists."
    else
        yellow "Creating index $SPLUNK_INDEX..."
        splunk_in add index "$SPLUNK_INDEX"
    fi
}

# ── Commands ────────────────────────────────────────────────────────────────

cmd_up() {
    require_docker
    mkdir -p "$ETL_DIR"

    # On Apple Silicon / ARM hosts the splunk/splunk image is amd64-only and
    # will run under emulation. Warn so the user knows boot is slower than
    # native and large index volumes will feel sluggish.
    local host_arch
    host_arch=$(uname -m 2>/dev/null || echo unknown)
    case "$host_arch" in
        arm64|aarch64)
            yellow "Note: detected ARM host ($host_arch)."
            yellow "      Splunk's image is amd64-only; running under emulation."
            yellow "      First boot may take 3-5 minutes."
            ;;
    esac

    docker compose up -d
    wait_ready
    ensure_index
    wait_web_ready
    echo ""
    green "Splunk is ready."
    echo "  Web UI:        http://localhost:$SPLUNK_PORT"
    echo "  Username:      admin"
    echo "  Password:      $SPLUNK_PASSWORD"
    echo "  TA inputs:     http://localhost:$SPLUNK_PORT/en-US/app/TA-mondoo/inputs"
    echo "  Dashboards:    http://localhost:$SPLUNK_PORT/en-US/app/mondoo_app"
    echo ""
    echo "Next: $0 configure /path/to/mondoo-service-account.json"
}

cmd_down() {
    require_docker
    docker compose down
}

cmd_nuke() {
    require_docker
    yellow "This will DELETE ALL Splunk state (index data, checkpoints, settings)."
    printf "Type YES to continue: "
    read -r ack
    [ "$ack" = "YES" ] || die "Aborted."
    docker compose down -v
    green "All state wiped."
}

cmd_status() {
    require_docker
    docker compose ps
    if container_running; then
        echo ""
        docker exec "$SPLUNK_CONTAINER" /opt/splunk/bin/splunk version 2>/dev/null || true
    fi
}

cmd_logs() {
    require_docker
    if [ "${1:-}" = "--follow" ] || [ "${1:-}" = "-f" ]; then
        docker compose logs -f splunk
    else
        docker compose logs --tail=200 splunk
    fi
}

cmd_tail_mondoo() {
    require_running
    yellow "Tailing splunkd.log for mondoo_input / mondoo_api lines. Ctrl-C to stop."
    docker exec "$SPLUNK_CONTAINER" \
        sh -c 'tail -F /opt/splunk/var/log/splunk/splunkd.log 2>/dev/null | grep -E "mondoo_input|mondoo_api"'
}

cmd_restart() {
    require_running
    # `splunk restart` can exit non-zero with "WARNING: web interface does not
    # seem to be available!" when it actually started successfully — common
    # under emulation. We capture and tolerate the warning, then verify the
    # daemon is up and poll Web ourselves.
    set +e
    docker exec -u splunk "$SPLUNK_CONTAINER" \
        /opt/splunk/bin/splunk restart -auth "admin:$SPLUNK_PASSWORD" \
        2>&1 | tee /tmp/splunk-restart.$$.log
    local rc=$?
    set -e
    if grep -qi "web interface does not seem to be available" /tmp/splunk-restart.$$.log; then
        rc=0
    fi
    rm -f /tmp/splunk-restart.$$.log
    if [ "$rc" -ne 0 ]; then
        die "splunk restart failed (exit $rc). Check: $0 logs"
    fi
    if ! docker exec "$SPLUNK_CONTAINER" /opt/splunk/bin/splunk status 2>/dev/null | grep -q "is running"; then
        die "splunkd is not running after restart. Check: $0 logs"
    fi
    wait_web_ready
}

cmd_shell() {
    require_running
    docker exec -it -u splunk "$SPLUNK_CONTAINER" /bin/bash
}

cmd_configure() {
    local json_file="${1:-}"
    [ -n "$json_file" ] || die "Usage: $0 configure <path-to-mondoo-service-account.json>"
    [ -f "$json_file" ] || die "File not found: $json_file"
    require_running

    # Sanity-check the JSON without requiring jq.
    if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$json_file" 2>/dev/null; then
        die "$json_file is not valid JSON."
    fi

    local blob
    blob=$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(',',':')))" "$json_file")

    mkdir -p TA-mondoo/local
    cat > TA-mondoo/local/inputs.conf <<EOF
# Generated by dev.sh — uncommitted (TA-mondoo/local/ is in .gitignore).

[mondoo_input://dev]
python.required       = 3.13
python.version        = python3
interval              = 60
index                 = $SPLUNK_INDEX
log_types             = audit,advisories,agents,assets,vulnerabilities,checks
page_size             = 100
initial_lookback_days = 7
disabled              = 0
mondoo_config_blob    = $blob

[monitor:///opt/mondoo-etl/export/*.jsonl]
disabled   = 0
index      = $SPLUNK_INDEX
sourcetype = mondoo:json
crcSalt    = <SOURCE>
EOF
    green "Wrote TA-mondoo/local/inputs.conf"
    cmd_restart
    echo ""
    echo "Wait ~60s, then check ingestion:"
    echo "  $0 search 'index=$SPLUNK_INDEX | stats count by sourcetype'"
    echo "  $0 tail-mondoo"
}

cmd_fake_etl() {
    mkdir -p "$ETL_DIR"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$ETL_DIR/dev-sample-assets.jsonl" <<EOF
{"asset_id":"sample-a1","name":"test-host-1","platform_name":"ubuntu","platform_version":"22.04","platform_arch":"amd64","base_score":42,"risk_score":40,"vuln_base_score":35,"updatedAt":"$ts","space_id":"dev","space_name":"dev","labels":{"created-by":"dev.sh"},"risk_factors":[]}
{"asset_id":"sample-a2","name":"test-host-2","platform_name":"ubuntu","platform_version":"24.04","platform_arch":"arm64","base_score":78,"risk_score":80,"vuln_base_score":75,"updatedAt":"$ts","space_id":"dev","space_name":"dev","labels":{},"risk_factors":[]}
EOF
    cat > "$ETL_DIR/dev-sample-vuln.jsonl" <<EOF
{"vuln_id":"CVE-2024-12345","title":"Sample critical vulnerability","rating":"critical","cvss_score":9.8,"asset_name":"test-host-1","asset_id":"sample-a1","lastUpdated":"$ts","space_id":"dev","state":"open","risk_factors":[]}
EOF
    cat > "$ETL_DIR/dev-sample-checks.jsonl" <<EOF
{"query_mrn":"//policy.api.mondoo.app/queries/dev-check-1","title":"Dev test check","rating":"medium","status":"fail","asset_name":"test-host-1","asset_id":"sample-a1","lastUpdated":"$ts","space_id":"dev"}
EOF
    green "Wrote sample JSONL files to $ETL_DIR/"
    echo ""
    echo "Verify routing (wait ~30s for the file-monitor to pick them up):"
    echo "  $0 search 'index=$SPLUNK_INDEX source=\"*dev-sample-*\" | stats count by sourcetype'"
}

cmd_search() {
    local query="${1:-}"
    [ -n "$query" ] || die "Usage: $0 search '<spl query>'"
    require_running
    docker exec -u splunk "$SPLUNK_CONTAINER" \
        /opt/splunk/bin/splunk search "$query" -maxout 50 -auth "admin:$SPLUNK_PASSWORD"
}

cmd_check_cim() {
    require_running
    echo "=== tag=vulnerability ==="
    cmd_search "tag=vulnerability index=$SPLUNK_INDEX | stats count by sourcetype" || true
    echo ""
    echo "=== tag=change ==="
    cmd_search "tag=change index=$SPLUNK_INDEX | stats count by sourcetype" || true
    echo ""
    echo "=== Vulnerabilities data model ==="
    cmd_search '| datamodel Vulnerabilities Vulnerabilities search | head 3 | table _time, signature, severity, dest' || true
    echo ""
    echo "=== Change data model ==="
    cmd_search '| datamodel Change Change search | head 3 | table _time, user, change_type, object' || true
    echo ""
    echo "=== Field-extraction sanity ==="
    cmd_search "index=$SPLUNK_INDEX sourcetype=mondoo:json:vuln | head 1 | table cve, cvss, severity, dest, signature, vendor" || true
}

cmd_reset() {
    require_running
    yellow "Wiping index $SPLUNK_INDEX and modular-input checkpoints..."
    splunk_in clean eventdata -index "$SPLUNK_INDEX" -f
    docker exec "$SPLUNK_CONTAINER" rm -rf /opt/splunk/var/lib/splunk/modinputs/mondoo_input 2>/dev/null || true
    cmd_restart
    green "Reset complete."
}

cmd_appinspect() {
    [ -x "$SCRIPT_DIR/vet.sh" ] || die "vet.sh not found or not executable."
    "$SCRIPT_DIR/vet.sh" "$@"
}

cmd_help() {
    cat <<EOF
dev.sh — local Splunk dev rig for the Mondoo TA + dashboard app.

Usage:
  $0 <command> [args]

Lifecycle:
  up                       Start Splunk (idempotent) + create the index
  down                     Stop the container (keeps the index data)
  nuke                     Stop AND delete all Splunk state (irreversible)
  status                   Show container + Splunk version
  restart                  Restart Splunk (after config changes)
  shell                    Open a bash shell in the container as splunk user

Configuration:
  configure <json>         Install Mondoo credentials and enable the input
  fake-etl                 Drop sample JSONL files in the ETL export dir
  reset                    Wipe the mondoo index + checkpoints (keep Splunk)

Inspection:
  logs [-f|--follow]       Show / follow Splunk startup logs
  tail-mondoo              Tail splunkd.log lines from the modular input
  search '<spl>'           Run a one-shot SPL search
  check-cim                Sanity-check CIM coverage (tags + data models)

Quality gates:
  appinspect [args...]     Run ./vet.sh (passes args through)

Environment overrides (current values shown):
  SPLUNK_CONTAINER         $SPLUNK_CONTAINER
  SPLUNK_PASSWORD          $SPLUNK_PASSWORD
  SPLUNK_PORT              $SPLUNK_PORT
  SPLUNK_INDEX             $SPLUNK_INDEX
  SPLUNK_VERSION           $SPLUNK_VERSION  (Splunk image tag)
  ETL_DIR                  $ETL_DIR

See TESTING.md for the end-to-end walkthrough.
EOF
}

# ── Dispatch ────────────────────────────────────────────────────────────────
case "${1:-help}" in
    up)              shift; cmd_up "$@" ;;
    down)            shift; cmd_down "$@" ;;
    nuke)            shift; cmd_nuke "$@" ;;
    status)          shift; cmd_status "$@" ;;
    logs)            shift; cmd_logs "$@" ;;
    tail-mondoo|tail)shift; cmd_tail_mondoo "$@" ;;
    restart)         shift; cmd_restart "$@" ;;
    shell)           shift; cmd_shell "$@" ;;
    configure)       shift; cmd_configure "$@" ;;
    fake-etl)        shift; cmd_fake_etl "$@" ;;
    search)          shift; cmd_search "$@" ;;
    check-cim)       shift; cmd_check_cim "$@" ;;
    reset)           shift; cmd_reset "$@" ;;
    appinspect)      shift; cmd_appinspect "$@" ;;
    help|-h|--help)  cmd_help ;;
    *)               red "Unknown command: ${1:-}"; cmd_help; exit 1 ;;
esac
