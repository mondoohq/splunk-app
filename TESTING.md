# End-to-end testing

This guide spins up a real Splunk Enterprise instance in Docker and runs the
TA and dashboard app against it. Every step is wrapped by `dev.sh`, so the
common case is three commands:

```bash
./dev.sh up
./dev.sh configure /path/to/mondoo-service-account.json
./dev.sh search 'index=mondoo | stats count by sourcetype'
```

For the full command list: `./dev.sh help`.

## Prerequisites

- **Docker** with Compose v2 (Docker Desktop, Colima, OrbStack, etc.).
  Allocate at least **4 GB RAM** to the engine; Splunk is hungry on startup.
- **Python 3** on the host (used by `./dev.sh configure` to validate and
  minify the credential JSON).
- **A Mondoo service account JSON file** — Mondoo Console →
  *Settings → Service Accounts → Add* → download.
- About **2 GB disk** for the Splunk image + persistent volume.

### Apple Silicon / ARM

Splunk only publishes `linux/amd64` manifests for the `splunk/splunk`
image. The compose file pins `platform: linux/amd64`, so on M-series Macs
Docker runs Splunk under emulation:

- **Docker Desktop with Rosetta** (preferred): enable
  *Settings → General → Use Rosetta for x86_64/amd64 emulation*.
  Acceptable performance for dev.
- **Without Rosetta** (Colima default, OrbStack on older versions): falls
  back to QEMU. Functional but slower, especially on first boot.

First boot under emulation can take 3-5 minutes. `./dev.sh up` allows up
to 10 minutes before giving up.

Splunk Cloud is **not** a viable target for this TA — Cloud stack apps don't
allow custom Python modular inputs. Use the Docker setup here, then forward
events to your Cloud stack if needed.

## What the rig provides

```
┌─ docker-compose.yml ────────────────────────────────────┐
│ splunk/splunk:9.3                                       │
│   • Free 500 MB/day dev license, 60 days                │
│   • Web UI on :8000, REST on :8089                      │
│   • Bind-mounts:                                        │
│       ./TA-mondoo    → /opt/splunk/etc/apps/TA-mondoo   │
│       ./mondoo_app   → /opt/splunk/etc/apps/mondoo_app  │
│       ./.etl-export  → /opt/mondoo-etl/export           │
│   • Persistent named volume: splunk-var                 │
└─────────────────────────────────────────────────────────┘
```

Edits to anything under `default/` in either app become live after
`./dev.sh restart` — no rebuild, no rsync.

## Seven-phase test plan

### Phase 1 — Bring up Splunk

```bash
./dev.sh up
```

Polls `splunk status` until ready, then creates the `mondoo` index. On the
first run, the image takes ~60–90 s to finish its Ansible bootstrap; the
script will time out at 5 minutes and dump logs if anything sticks.

**Verify** by opening <http://localhost:8000> (login: `admin` /
`changeme123!`). Both apps must appear under *Apps → Manage Apps* — if they
don't, the bind mount didn't pick up.

### Phase 2 — Install credentials and enable the input

```bash
./dev.sh configure /path/to/mondoo-service-account.json
```

This writes `TA-mondoo/local/inputs.conf` (gitignored), enables both the
modular input and the file-monitor stanza, and restarts Splunk.

**Verify** within ~2 minutes:

```bash
./dev.sh search 'index=mondoo sourcetype="mondoo:*" | stats count by sourcetype'
./dev.sh tail-mondoo            # live splunkd.log filter for the input
```

You should see counts climbing for `mondoo:rest:audit`,
`mondoo:rest:advisory`, etc.

### Phase 3 — Validate the file-monitor path

If you have real Mondoo ETL JSONL files, drop them into the bind-mounted
host directory:

```bash
cp /path/to/mondoo-export/*.jsonl .etl-export/
```

If you don't have any yet, generate offline samples:

```bash
./dev.sh fake-etl
```

**Verify** that filename-based sourcetype routing works:

```bash
./dev.sh search 'index=mondoo source="*sample-*" | stats count by sourcetype'
```

You should see `mondoo:json:asset`, `mondoo:json:vuln`, `mondoo:json:check`
— that proves `transforms.conf` is firing.

### Phase 4 — Click through every dashboard

In your browser, walk through each view of the `mondoo_app`:

- Assets — <http://localhost:8000/en-US/app/mondoo_app/assets>
- Vulnerabilities — `/vulnerabilities`
- Checks — `/checks`
- Queries — `/queries`
- Audit — `/audit`
- Data Information — `/data_information`

**Per-dashboard checklist:**
- All panels load (no "Search returned no results" if data exists)
- Time-range picker filters
- Drilldowns (where present) open the right detail view
- Browser devtools console shows no JS errors

### Phase 5 — Verify CIM compliance

The expanded `eventtypes.conf` / `tags.conf` / `props.conf` only earn their
keep if data lands inside Splunk's data models:

```bash
./dev.sh check-cim
```

That command runs three sanity checks in order:

1. `tag=vulnerability` returns events.
2. `tag=change` returns audit events.
3. The `Vulnerabilities` and `Change` data-model searches return rows.

If any block returns zero rows when you know there's data in the raw index,
the field aliases or tag mappings are wrong.

### Phase 6 — Negative tests

These catch problems that the happy path won't show.

**Bad token** — Edit `TA-mondoo/local/inputs.conf`, mangle one byte of the
JWT, then `./dev.sh restart`. Expect a `Mondoo API HTTP 401` in the log:

```bash
./dev.sh tail-mondoo
```

**Retry/backoff** — Simulate a connection error by setting a bogus proxy in
the container environment (edit `docker-compose.yml`, add
`HTTPS_PROXY: http://127.0.0.1:1` under `environment:`, then
`docker compose up -d`). Watch for the `retrying in Ns` log lines.

**Checkpoint reset** —

```bash
./dev.sh reset
```

Wipes the `mondoo` index *and* the checkpoint files, then restarts. Next
poll fetches a full `initial_lookback_days` of history again.

### Phase 7 — AppInspect

Same checks CI runs on every PR, but locally:

```bash
./dev.sh appinspect              # full run
./dev.sh appinspect --dry-run    # just show what would be packaged
./dev.sh appinspect --help       # vet.sh options
```

## Resetting between tests

| Goal                                  | Command                                  |
|---------------------------------------|------------------------------------------|
| Re-fetch all data from scratch        | `./dev.sh reset`                         |
| Stop Splunk but keep all data         | `./dev.sh down`                          |
| Full wipe (index, settings, license)  | `./dev.sh nuke`                          |
| Pick up `default/` edits              | `./dev.sh restart`                       |
| Tail just the modular-input logs      | `./dev.sh tail-mondoo`                   |

## Environment overrides

All defaults can be overridden via env vars:

```bash
SPLUNK_VERSION=9.2 SPLUNK_PASSWORD='different-pass!' ./dev.sh up
SPLUNK_PORT=18000 ./dev.sh up        # if 8000 is taken
SPLUNK_INDEX=mondoo_test ./dev.sh up # alternate index
```

Persist these in a `.env` file (gitignored) so `docker compose` and `dev.sh`
pick them up automatically.

## What this rig won't catch

- **Search-Head Cluster behaviour.** Needs ≥3 SH containers with KV-store
  replication; out of scope for this single-instance rig. Run the modular
  input on a dedicated HF in production — see the [README](README.md#deployment-topology).
- **Splunk Cloud Victoria vetting.** No public sandbox; push a build to a
  paid trial stack to exercise Splunk's Cloud vetting harness.
- **Splunk Cloud install path generally.** Custom Python modular inputs
  aren't permitted in Cloud stack apps. Run on a customer-operated HF.
