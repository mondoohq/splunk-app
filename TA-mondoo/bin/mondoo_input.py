#!/usr/bin/env python3
"""
mondoo_input.py – Splunk Modular Input for Mondoo log ingestion.

Implements the Splunk modular input XML streaming protocol directly
(no Splunk SDK dependency) so the TA works out-of-the-box on Splunk 8/9
with the bundled Python 3.9 interpreter.

Supported log types
-------------------
  audit           : Mondoo audit log (org / space audit trail)
  assets          : Asset / device inventory snapshots
  vulnerabilities : CVE findings
  advisories      : Advisory findings
  checks          : Policy check findings
  agents          : Managed client agents
"""

import contextlib
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

# Allow imports from the same bin/ directory when running standalone
_bin_dir = os.path.dirname(os.path.abspath(__file__))
if _bin_dir not in sys.path:
    sys.path.insert(0, _bin_dir)

from mondoo_api import FINDING_TYPE_FILTERS, MondooClient, parse_config_blob  # noqa: E402

# ---------------------------------------------------------------------------
# Logging – write to stderr; Splunk captures it in splunkd.log
# ---------------------------------------------------------------------------
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9._\-]{8,}")
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+")
_JSON_TOKEN_PATTERN = re.compile(r'("token"\s*:\s*")[^"]+(")')


class _SecretRedactingFilter(logging.Filter):
    """Strip JWTs, Bearer tokens and JSON 'token' fields from log records.

    This is a defence-in-depth measure: callers should already mask secrets
    explicitly, but we run a final pass so an accidental %s of a config blob
    or exception text doesn't leak credentials to splunkd.log.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = _JWT_PATTERN.sub("eyJ****", msg)
        redacted = _BEARER_PATTERN.sub(r"\1****", redacted)
        redacted = _JSON_TOKEN_PATTERN.sub(r"\1****\2", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mondoo_input")
# Attach to the root logger so output from mondoo_api and any third-party
# libraries is also redacted.
logging.getLogger().addFilter(_SecretRedactingFilter())

# ---------------------------------------------------------------------------
# Modular input scheme (advertised to Splunk)
# ---------------------------------------------------------------------------
SCHEME = """\
<scheme>
  <title>Mondoo Log Ingestion</title>
  <description>Ingest logs from Mondoo via the GraphQL REST API. Supports: audit, assets, vulnerabilities, advisories, checks, agents.</description>
  <use_external_validation>true</use_external_validation>
  <streaming_mode>xml</streaming_mode>
  <endpoint>
    <args>
      <arg name="mondoo_config_blob">
        <title>Mondoo Config Blob</title>
        <description>JSON credential blob downloaded from the Mondoo console, or a raw JWT token string.</description>
        <required_on_create>true</required_on_create>
        <required_on_edit>false</required_on_edit>
        <encrypted>true</encrypted>
      </arg>
      <arg name="log_types">
        <title>Log Types</title>
        <description>Comma-separated list of log types to collect. Supported: audit, assets, vulnerabilities, advisories, checks, agents</description>
        <required_on_create>false</required_on_create>
        <required_on_edit>false</required_on_edit>
      </arg>
      <arg name="resource_mrn">
        <title>Resource MRN</title>
        <description>Space or organisation MRN to query (auto-detected from the config blob if omitted). Format: //captain.api.mondoo.app/spaces/&lt;space-id&gt;</description>
        <required_on_create>false</required_on_create>
        <required_on_edit>false</required_on_edit>
      </arg>
      <arg name="page_size">
        <title>Page Size</title>
        <description>Number of records fetched per API request (default: 100, max: 100).</description>
        <required_on_create>false</required_on_create>
        <required_on_edit>false</required_on_edit>
        <validation>is_pos_int('page_size')</validation>
      </arg>
      <arg name="initial_lookback_days">
        <title>Initial Lookback (days)</title>
        <description>On the first run, how many days of history to retrieve for audit logs. 0 = all available history (default: 7).</description>
        <required_on_create>false</required_on_create>
        <required_on_edit>false</required_on_edit>
        <validation>is_nonneg_int('initial_lookback_days')</validation>
      </arg>
    </args>
  </endpoint>
</scheme>"""

ALL_LOG_TYPES = ["audit", "assets", "vulnerabilities", "advisories", "checks", "agents"]

# Reasonable upper bound on initial backfill. Larger values risk hitting the
# Mondoo API rate-limit budget and producing a very long first-run. Users who
# need more history can still bypass this by setting 0 (= all available).
_MAX_LOOKBACK_DAYS = 365


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _xml_text(element: Optional[ET.Element]) -> str:
    return (element.text or "").strip() if element is not None else ""


def parse_input_config(xml_str: str) -> dict:
    """Parse the XML config that Splunk sends to stdin."""
    root = ET.fromstring(xml_str)
    result = {
        "server_host": _xml_text(root.find("server_host")),
        "server_uri": _xml_text(root.find("server_uri")),
        "session_key": _xml_text(root.find("session_key")),
        "checkpoint_dir": _xml_text(root.find("checkpoint_dir")),
        "stanzas": {},
    }
    for stanza in root.findall(".//stanza"):
        name = stanza.get("name", "")
        params = {}
        for param in stanza.findall("param"):
            params[param.get("name")] = _xml_text(param)
        result["stanzas"][name] = params
    return result


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _checkpoint_path(checkpoint_dir: str, stanza_name: str, log_type: str) -> str:
    safe = stanza_name.replace("/", "_").replace(":", "_").replace(" ", "_")
    return os.path.join(checkpoint_dir, f"{safe}_{log_type}.json")


def load_checkpoint(checkpoint_dir: str, stanza_name: str, log_type: str) -> dict:
    path = _checkpoint_path(checkpoint_dir, stanza_name, log_type)
    if os.path.isfile(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read checkpoint %s: %s", path, exc)
    return {}


def save_checkpoint(checkpoint_dir: str, stanza_name: str, log_type: str, data: dict) -> None:
    path = _checkpoint_path(checkpoint_dir, stanza_name, log_type)
    tmp = path + ".tmp"
    try:
        # Open with restrictive perms (owner-only) before writing.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to save checkpoint %s: %s", path, exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Event output helpers
# ---------------------------------------------------------------------------

def _write_event_stream_open():
    sys.stdout.write("<stream>")
    sys.stdout.flush()


def _write_event_stream_close():
    sys.stdout.write("</stream>")
    sys.stdout.flush()


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


_PARSE_TIME_WARNED = False


def _parse_time(ts_str: str) -> Optional[float]:
    """Parse ISO-8601 timestamp to epoch float.

    Returns ``None`` on parse failure. A single WARN is logged the first time
    we encounter an unparseable value (per process) so operators have a sample
    to investigate without spamming splunkd.log on every event.
    """
    global _PARSE_TIME_WARNED
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    if not _PARSE_TIME_WARNED:
        logger.warning(
            "Could not parse timestamp %r — falling back to indexer time. "
            "Further unparseable timestamps will not be logged.",
            ts_str[:64],
        )
        _PARSE_TIME_WARNED = True
    return None


def emit_event(
    data: str,
    sourcetype: str,
    source: str,
    index: str = "main",
    time: Optional[float] = None,
    host: str = "mondoo",
):
    """Write a single Splunk event in XML streaming format."""
    parts = ["<event>"]
    if time is not None:
        parts.append(f"<time>{time:.3f}</time>")
    parts.append(f"<index>{_escape_xml(index)}</index>")
    parts.append(f"<sourcetype>{_escape_xml(sourcetype)}</sourcetype>")
    parts.append(f"<source>{_escape_xml(source)}</source>")
    parts.append(f"<host>{_escape_xml(host)}</host>")
    parts.append(f"<data>{_escape_xml(data)}</data>")
    parts.append("</event>")
    sys.stdout.write("".join(parts))
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Generic ingestion helper
# ---------------------------------------------------------------------------

def _ingest_generic(
    fetcher,
    log_type: str,
    sourcetype: str,
    stanza_name: str,
    checkpoint_dir: str,
    index: str,
    source: str,
    time_field: str,
    flatten_fn=None,
    full_fetch: bool = False,
):
    """Generic ingestion loop: paginate, emit events, checkpoint.

    When *full_fetch* is True the stored cursor is ignored and all records are
    fetched from the start every run.  Use this for APIs whose edge cursors are
    unstable across calls (findings, check scores).
    """
    checkpoint = load_checkpoint(checkpoint_dir, stanza_name, log_type)
    last_cursor: Optional[str] = None if full_fetch else checkpoint.get("last_cursor")

    new_cursor: Optional[str] = last_cursor
    event_count = 0

    try:
        for node in fetcher(after_cursor=last_cursor):
            raw = dict(node)
            cursor = raw.pop("_cursor", None)

            event_time = _parse_time(raw.get(time_field, ""))

            if flatten_fn:
                raw = flatten_fn(raw)

            event_json = json.dumps(raw, ensure_ascii=False)
            emit_event(
                data=event_json,
                sourcetype=sourcetype,
                source=source,
                index=index,
                time=event_time,
                host="mondoo",
            )

            if cursor:
                new_cursor = cursor
            event_count += 1

    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching %s for %s: %s", log_type, stanza_name, exc)
    finally:
        if not full_fetch and new_cursor and new_cursor != last_cursor:
            save_checkpoint(checkpoint_dir, stanza_name, log_type, {"last_cursor": new_cursor})
        if event_count:
            logger.info("Ingested %d %s events for %s", event_count, log_type, stanza_name)
        else:
            logger.info("No new %s events for %s", log_type, stanza_name)


# ---------------------------------------------------------------------------
# Per-log-type ingestion
# ---------------------------------------------------------------------------

def _flatten_audit(raw: dict) -> dict:
    identity = raw.pop("identity", {}) or {}
    raw["identity_name"] = identity.get("name", "")
    raw["identity_mrn"] = identity.get("mrn", "")
    return raw


def _flatten_asset(raw: dict) -> dict:
    platform = raw.pop("platform", {}) or {}
    raw["platform_name"] = platform.get("name", "")
    raw["platform_version"] = platform.get("version", "")
    raw["platform_arch"] = platform.get("arch", "")
    raw["platform_runtime"] = platform.get("runtime", "")
    labels = raw.pop("labels", []) or []
    raw["labels"] = {kv.get("key", ""): kv.get("value", "") for kv in labels}
    annotations = raw.pop("annotations", []) or []
    if annotations:
        raw["annotations"] = {kv.get("key", ""): kv.get("value", "") for kv in annotations}
    # Flatten score object
    score = raw.pop("score", {}) or {}
    if score:
        raw["base_score"] = score.get("value")
        raw["score_grade"] = score.get("grade")
        raw["score_completion"] = score.get("completion")
    # Flatten risk factors
    risk_factors = raw.pop("riskFactors", []) or []
    if risk_factors:
        raw["risk_factors"] = [
            {"mrn": rf.get("mrn", ""), "title": rf.get("title", ""),
             "isPositive": rf.get("isPositive")}
            for rf in risk_factors
        ]
    return raw


def _flatten_finding(raw: dict) -> dict:
    asset = raw.pop("asset", {}) or {}
    raw["asset_name"] = asset.get("name", "")
    raw["asset_mrn"] = asset.get("mrn", "")
    if asset.get("id"):
        raw["asset_id"] = asset["id"]
    cvss = raw.pop("cvss", {}) or {}
    if cvss:
        raw["cvss_score"] = cvss.get("value")
        raw["cvss_vector"] = cvss.get("vector", "")
        raw["cvss_source"] = cvss.get("source", "")
    # Flatten risk factors
    risk_factors = raw.pop("riskFactors", []) or []
    if risk_factors:
        raw["risk_factors"] = [
            {"mrn": rf.get("mrn", ""), "indicator": rf.get("indicator"),
             "title": rf.get("title", ""), "isPositive": rf.get("isPositive"),
             "magnitude": rf.get("magnitude")}
            for rf in risk_factors
        ]
    # Rename fields to match ETL convention
    if "id" in raw:
        raw["vuln_id"] = raw.pop("id")
    if "mrn" in raw:
        raw["vuln_mrn"] = raw.pop("mrn")
    return raw


def _flatten_check_score(raw: dict) -> dict:
    """Flatten a CheckScore node from the checkScores query."""
    # Extract asset info
    asset = raw.pop("asset", {}) or {}
    raw["asset_name"] = asset.get("name", "")
    raw["asset_mrn"] = asset.get("mrn", "")
    if asset.get("id"):
        raw["asset_id"] = asset["id"]
    # Extract mql from nested mquery
    mquery = raw.pop("mquery", None) or {}
    if mquery:
        raw["mql"] = mquery.get("mql", "")
        raw["action"] = mquery.get("action", "")
    # Flatten risk factors
    risk_factors = raw.pop("riskFactors", []) or []
    if risk_factors:
        raw["risk_factors"] = [
            {"mrn": rf.get("mrn", ""), "indicator": rf.get("indicator"),
             "title": rf.get("title", ""), "isPositive": rf.get("isPositive")}
            for rf in risk_factors
        ]
    # Rename mrn → query_mrn to match ETL convention
    if "mrn" in raw:
        raw["query_mrn"] = raw.pop("mrn")
    # Derive status from state (OPEN→fail, CLOSED→pass, etc.)
    state = raw.get("state", "")
    if state == "OPEN":
        raw["status"] = "fail"
    elif state == "CLOSED":
        raw["status"] = "pass"
    elif state:
        raw["status"] = state.lower()
    # Map score fields to ETL conventions
    if "baseScore" in raw:
        raw["score"] = raw["baseScore"]
        raw["base_score"] = raw["baseScore"]
        del raw["baseScore"]
    if "riskScore" in raw:
        raw["risk_score"] = raw.pop("riskScore")
    if "riskValue" in raw:
        raw["risk_value"] = raw.pop("riskValue")
    return raw


def _flatten_agent(raw: dict) -> dict:
    platform = raw.pop("platform", {}) or {}
    raw["platform_name"] = platform.get("name", "")
    raw["platform_version"] = platform.get("version", "")
    raw["platform_arch"] = platform.get("arch", "")
    status = raw.pop("status", {}) or {}
    raw["state"] = status.get("state", "")
    return raw


def ingest_audit_logs(client, stanza_name, params, checkpoint_dir, index):
    """Ingest audit logs using timestamp-based resumption.

    The audit API's ``after`` cursor is a page-offset that is only valid within
    a single pagination run.  Between poll cycles we therefore resume by
    filtering on the last seen timestamp via ``timestampFilter``.
    """
    checkpoint = load_checkpoint(checkpoint_dir, stanza_name, "audit")
    last_timestamp = checkpoint.get("last_timestamp")
    resource_mrn = params.get("resource_mrn") or client.space_mrn

    # Determine the timestamp to fetch from
    timestamp_after = last_timestamp  # resume from where we left off
    if not timestamp_after:
        try:
            lookback_days = int(params.get("initial_lookback_days") or "7")
        except ValueError:
            logger.warning(
                "Invalid initial_lookback_days %r for %s — defaulting to 7",
                params.get("initial_lookback_days"), stanza_name,
            )
            lookback_days = 7
        if lookback_days < 0:
            logger.warning(
                "Negative initial_lookback_days for %s — clamping to 0 (full history)",
                stanza_name,
            )
            lookback_days = 0
        elif lookback_days > _MAX_LOOKBACK_DAYS:
            logger.warning(
                "initial_lookback_days %d for %s exceeds cap %d — clamping",
                lookback_days, stanza_name, _MAX_LOOKBACK_DAYS,
            )
            lookback_days = _MAX_LOOKBACK_DAYS
        if lookback_days > 0:
            since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            timestamp_after = since.strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.info("First run for %s/audit – fetching from %s", stanza_name, timestamp_after)
        else:
            logger.info("First run for %s/audit – fetching full history", stanza_name)

    source = f"mondoo://{resource_mrn}"
    new_timestamp = last_timestamp
    event_count = 0

    try:
        for node in client.fetch_audit_logs(
            resource_mrn=resource_mrn,
            after_cursor=None,
            timestamp_after=timestamp_after,
        ):
            raw = dict(node)
            raw.pop("_cursor", None)

            event_ts = raw.get("timestamp", "")
            event_time = _parse_time(event_ts)

            # Track the latest timestamp we've seen
            if event_ts and (not new_timestamp or event_ts > new_timestamp):
                new_timestamp = event_ts

            raw = _flatten_audit(raw)
            event_json = json.dumps(raw, ensure_ascii=False)
            emit_event(
                data=event_json,
                sourcetype="mondoo:rest:audit",
                source=source,
                index=index,
                time=event_time,
                host="mondoo",
            )
            event_count += 1

            if event_count % 500 == 0:
                save_checkpoint(checkpoint_dir, stanza_name, "audit", {"last_timestamp": new_timestamp})

    except Exception as exc:  # noqa: BLE001
        logger.error("Error fetching audit for %s: %s", stanza_name, exc)
    finally:
        if new_timestamp and new_timestamp != last_timestamp:
            save_checkpoint(checkpoint_dir, stanza_name, "audit", {"last_timestamp": new_timestamp})
            logger.info("Ingested %d audit events for %s", event_count, stanza_name)
        else:
            logger.info("No new audit events for %s", stanza_name)


def ingest_assets(client, stanza_name, checkpoint_dir, index):
    _ingest_generic(
        fetcher=client.fetch_assets,
        log_type="assets",
        sourcetype="mondoo:rest:asset",
        stanza_name=stanza_name,
        checkpoint_dir=checkpoint_dir,
        index=index,
        source=f"mondoo://{client.space_mrn}/assets",
        time_field="updatedAt",
        flatten_fn=_flatten_asset,
    )


def ingest_findings(client, stanza_name, checkpoint_dir, index, log_type):
    finding_types = FINDING_TYPE_FILTERS.get(log_type, [])
    if not finding_types:
        return

    space_mrn = client.space_mrn
    space_id = space_mrn.split("/spaces/")[-1] if "/spaces/" in space_mrn else ""

    def fetcher(after_cursor=None):
        return client.fetch_findings(finding_types=finding_types, after_cursor=after_cursor)

    def flatten_with_space(raw):
        raw = _flatten_finding(raw)
        raw["space_mrn"] = space_mrn
        raw["space_id"] = space_id
        return raw

    sourcetype_name = log_type.rstrip("ies") + "y" if log_type.endswith("ies") else log_type.rstrip("s")

    _ingest_generic(
        fetcher=fetcher,
        log_type=log_type,
        sourcetype=f"mondoo:rest:{sourcetype_name}",
        stanza_name=stanza_name,
        checkpoint_dir=checkpoint_dir,
        index=index,
        source=f"mondoo://{client.space_mrn}/{log_type}",
        time_field="lastUpdated",
        flatten_fn=flatten_with_space,
        full_fetch=True,
    )


def ingest_checks(client, stanza_name, checkpoint_dir, index):
    """Ingest checks via space-level checkScores (includes per-asset breakdown)."""
    space_mrn = client.space_mrn
    space_id = space_mrn.split("/spaces/")[-1] if "/spaces/" in space_mrn else ""

    def fetcher(after_cursor=None):
        return client.fetch_check_scores(after_cursor=after_cursor)

    def flatten_check(raw):
        raw = _flatten_check_score(raw)
        raw["space_mrn"] = space_mrn
        raw["space_id"] = space_id
        return raw

    _ingest_generic(
        fetcher=fetcher,
        log_type="checks",
        sourcetype="mondoo:rest:check",
        stanza_name=stanza_name,
        checkpoint_dir=checkpoint_dir,
        index=index,
        source=f"mondoo://{space_mrn}/checks",
        time_field="lastUpdated",
        flatten_fn=flatten_check,
        full_fetch=True,
    )


def ingest_agents(client, stanza_name, checkpoint_dir, index):
    _ingest_generic(
        fetcher=client.fetch_agents,
        log_type="agents",
        sourcetype="mondoo:rest:agent",
        stanza_name=stanza_name,
        checkpoint_dir=checkpoint_dir,
        index=index,
        source=f"mondoo://{client.space_mrn}/agents",
        time_field="createdAt",
        flatten_fn=_flatten_agent,
        full_fetch=True,
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def run_scheme():
    sys.stdout.write(SCHEME)
    sys.stdout.flush()


def run_validate(xml_str: str):
    """Validate arguments passed by Splunk during input configuration."""
    config = parse_input_config(xml_str)
    for _stanza_name, params in config["stanzas"].items():
        blob = params.get("mondoo_config_blob", "")
        if not blob:
            print(
                "<error><message>mondoo_config_blob is required</message></error>",
                flush=True,
            )
            sys.exit(1)
        try:
            creds = parse_config_blob(blob)
        except ValueError as exc:
            print(
                f"<error><message>{_escape_xml(str(exc))}</message></error>",
                flush=True,
            )
            sys.exit(1)

        page_size = int(params.get("page_size") or "100")
        client = MondooClient(
            token=creds["token"],
            api_endpoint=creds["api_endpoint"],
            space_mrn=params.get("resource_mrn") or creds["space_mrn"],
            page_size=page_size,
        )
        try:
            client.validate()
        except Exception as exc:  # noqa: BLE001
            print(
                f"<error><message>Mondoo API validation failed: {_escape_xml(str(exc))}</message></error>",
                flush=True,
            )
            sys.exit(1)


def run_input(xml_str: str):
    config = parse_input_config(xml_str)
    checkpoint_dir = config.get("checkpoint_dir", "/tmp")

    _write_event_stream_open()
    try:
        for stanza_name, params in config["stanzas"].items():
            blob = params.get("mondoo_config_blob", "")
            if not blob:
                logger.error("No mondoo_config_blob for stanza %s – skipping", stanza_name)
                continue

            try:
                creds = parse_config_blob(blob)
            except ValueError as exc:
                logger.error("Invalid config blob for %s: %s", stanza_name, exc)
                continue

            page_size = int(params.get("page_size") or "100")
            resource_mrn = params.get("resource_mrn") or creds["space_mrn"]
            index = params.get("index", "main")

            client = MondooClient(
                token=creds["token"],
                api_endpoint=creds["api_endpoint"],
                space_mrn=resource_mrn,
                page_size=page_size,
            )

            raw_log_types = params.get("log_types", "audit")
            log_types = [lt.strip().lower() for lt in raw_log_types.split(",") if lt.strip()]

            for log_type in log_types:
                if log_type == "audit":
                    ingest_audit_logs(client, stanza_name, params, checkpoint_dir, index)
                elif log_type == "assets":
                    ingest_assets(client, stanza_name, checkpoint_dir, index)
                elif log_type in ("vulnerabilities", "advisories"):
                    ingest_findings(client, stanza_name, checkpoint_dir, index, log_type)
                elif log_type == "checks":
                    ingest_checks(client, stanza_name, checkpoint_dir, index)
                elif log_type == "agents":
                    ingest_agents(client, stanza_name, checkpoint_dir, index)
                else:
                    logger.warning("Unknown log type '%s' in stanza %s – skipping", log_type, stanza_name)
    finally:
        _write_event_stream_close()


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--scheme":
            run_scheme()
            return
        if arg == "--validate-arguments":
            run_validate(sys.stdin.read())
            return

    # Normal execution – Splunk passes config via stdin
    run_input(sys.stdin.read())


if __name__ == "__main__":
    main()
