"""
mondoo_api.py – Mondoo GraphQL API client for the Splunk TA.

Supports:
  - JSON credential blob  (downloaded from Mondoo console)
  - Raw JWT token string
  - Cursor-based incremental pagination

Log types:
  - audit      : Audit log (org / space activity trail)
  - assets     : Asset / device inventory
  - vulnerabilities : CVE findings
  - advisories : Advisory findings
  - checks     : Policy check findings
  - agents     : Managed client agents
"""

import json
import logging
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# Retry configuration for transient errors (network blip, 5xx, 429).
_MAX_RETRIES = 4
_INITIAL_BACKOFF_SECONDS = 1.0
_BACKOFF_FACTOR = 2.0
_BACKOFF_MAX_SECONDS = 30.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _mask_secret(value: str) -> str:
    """Return a log-safe representation of a token/secret."""
    if not value:
        return ""
    if len(value) <= 12:
        return "****"
    return f"{value[:8]}…{value[-4:]}"


def _redact_blob(text: str) -> str:
    """Best-effort redaction of secrets in arbitrary text (for log/error output)."""
    if not text:
        return text
    # Strip Bearer tokens
    import re
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1****", text)
    text = re.sub(r'("token"\s*:\s*")[^"]+(")', r"\1****\2", text)
    text = re.sub(r"\beyJ[A-Za-z0-9._\-]+", "eyJ****", text)
    return text

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

AUDIT_LOG_QUERY = """\
query AuditLogForwardPagination(
  $first: Int
  $after: String
  $orderBy: AuditLogOrder
  $resourceMrn: String!
  $timestampFilter: TimestampFilter
) {
  auditlog(
    first: $first
    after: $after
    orderBy: $orderBy
    resourceMrn: $resourceMrn
    timestampFilter: $timestampFilter
  ) {
    totalCount
    edges {
      cursor
      node {
        identity {
          name
          mrn
        }
        resource
        action
        timestamp
        msg
      }
    }
    pageInfo {
      startCursor
      endCursor
      hasNextPage
    }
  }
}
"""

ASSETS_QUERY = """\
query Assets(
  $spaceMrn: String!
  $first: Int
  $after: String
  $orderBy: AssetOrder
) {
  assets(
    spaceMrn: $spaceMrn
    first: $first
    after: $after
    orderBy: $orderBy
  ) {
    totalCount
    edges {
      cursor
      node {
        id
        mrn
        name
        state
        asset_type
        createdAt
        updatedAt
        lastScoredAt
        score {
          grade
          value
          type
          completion
          weight
          message
        }
        platform {
          name
          version
          arch
          runtime
        }
        labels {
          key
          value
        }
        annotations {
          key
          value
        }
        riskFactors {
          mrn
          title
          isPositive
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

FINDINGS_QUERY = """\
query Findings(
  $scopeMrn: String!
  $first: Int
  $after: String
  $orderBy: FindingsOrder
  $filter: FindingsFilter
) {
  findings(
    scopeMrn: $scopeMrn
    first: $first
    after: $after
    orderBy: $orderBy
    filter: $filter
  ) {
    ... on FindingsConnection {
      totalCount
      edges {
        cursor
        node {
          ... on CveFinding {
            __typename
            id
            mrn
            title
            state
            rating
            riskScore
            riskValue
            baseValue
            baseRating
            firstDetectedAt
            publishedAt
            lastUpdated
            cvss {
              value
              vector
              source
            }
            riskFactors {
              mrn
              indicator
              title
              affected
              total
              isPositive
            }
            asset {
              id
              name
              mrn
            }
          }
          ... on AdvisoryFinding {
            __typename
            id
            mrn
            title
            state
            rating
            riskScore
            riskValue
            baseValue
            baseRating
            firstDetectedAt
            publishedAt
            lastUpdated
            cvss {
              value
              vector
              source
            }
            riskFactors {
              mrn
              indicator
              title
              affected
              total
              isPositive
            }
            asset {
              id
              name
              mrn
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

CHECK_SCORES_QUERY = """\
query CheckScores(
  $entityMrn: String!
  $first: Int
  $after: String
  $orderBy: CheckScoreOrder
) {
  checkScores(
    entityMrn: $entityMrn
    first: $first
    after: $after
    orderBy: $orderBy
  ) {
    ... on CheckScoresConnection {
      totalCount
      edges {
        cursor
        node {
          mrn
          title
          state
          baseScore
          riskScore
          riskValue
          rating
          lastUpdated
          resultType
          queryState
          impactRating
          mquery {
            mql
            action
          }
          asset {
            id
            name
            mrn
          }
          riskFactors {
            mrn
            indicator
            title
            affected
            total
            isPositive
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

AGENTS_QUERY = """\
query Agents(
  $spaceMrn: String!
  $first: Int
  $after: String
  $orderBy: AgentOrder
) {
  agents(
    spaceMrn: $spaceMrn
    first: $first
    after: $after
    orderBy: $orderBy
  ) {
    totalCount
    edges {
      cursor
      node {
        id
        mrn
        name
        hostname
        platform {
          name
          version
          arch
          runtime
        }
        status {
          state
        }
        createdAt
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# Map of finding type filters for the findings query
FINDING_TYPE_FILTERS = {
    "vulnerabilities": ["CVE"],
    "advisories": ["ADVISORY"],
}


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def parse_config_blob(blob: str) -> dict:
    """
    Parse the Mondoo config blob.

    Accepts:
      1. JSON string with fields: token, api_endpoint, space_mrn / mrn
      2. Raw JWT string (starts with "eyJ")

    Returns a normalised dict with keys:
      token        : Bearer token string
      api_endpoint : Base URL e.g. "https://eu.api.mondoo.com"
      space_mrn    : captain-format space MRN
    """
    blob = blob.strip()

    # Try JSON first
    if blob.startswith("{"):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ValueError(f"mondoo_config_blob looks like JSON but failed to parse: {exc}") from exc

        token = data.get("token") or data.get("api_token") or data.get("api_key")
        if not token:
            raise ValueError(
                "mondoo_config_blob JSON does not contain a 'token' field. "
                "Certificate-based service accounts are not yet supported – "
                "please create a token-based service account in the Mondoo console."
            )

        # Normalise the API endpoint (strip trailing slash + /query path)
        raw_ep = data.get("api_endpoint", "https://us.api.mondoo.com")
        api_endpoint = raw_ep.rstrip("/").removesuffix("/query")

        # Prefer space_mrn; fall back to mrn and convert agents-format to captain-format
        space_mrn = data.get("space_mrn") or _agents_mrn_to_captain(data.get("mrn", ""))

        return {
            "token": token,
            "api_endpoint": api_endpoint,
            "space_mrn": space_mrn,
        }

    # Treat as raw JWT
    if blob.startswith("eyJ") or "." in blob:
        # Decode sub claim to get the space MRN
        space_mrn = _extract_space_mrn_from_jwt(blob)
        api_endpoint = _guess_endpoint_from_mrn(space_mrn)
        return {
            "token": blob,
            "api_endpoint": api_endpoint,
            "space_mrn": space_mrn,
        }

    raise ValueError("mondoo_config_blob must be a JSON credential blob or a raw JWT token.")


def _agents_mrn_to_captain(mrn: str) -> str:
    """Convert //agents.api.mondoo.app/spaces/<id>/... → //captain.api.mondoo.app/spaces/<id>"""
    if not mrn:
        return ""
    parts = mrn.replace("//agents.api.mondoo.app/", "").split("/")
    # parts = ["spaces", "<space-id>", ...]
    if len(parts) >= 2 and parts[0] == "spaces":
        return f"//captain.api.mondoo.app/spaces/{parts[1]}"
    return mrn


def _extract_space_mrn_from_jwt(token: str) -> str:
    """Decode the JWT payload (no signature verification) and extract space MRN."""
    import base64

    try:
        payload_b64 = token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        sub = payload.get("sub", "")
        return _agents_mrn_to_captain(sub)
    except Exception:  # noqa: BLE001
        return ""


def _guess_endpoint_from_mrn(mrn: str) -> str:
    """Guess the API endpoint from the space name (eu- prefix → EU endpoint)."""
    if mrn:
        space_id = mrn.split("/spaces/")[-1] if "/spaces/" in mrn else ""
        if space_id.startswith("eu-"):
            return "https://eu.api.mondoo.com"
    return "https://us.api.mondoo.com"


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class MondooClient:
    def __init__(
        self,
        token: str,
        api_endpoint: str,
        space_mrn: str,
        page_size: int = 100,
        ca_bundle: Optional[str] = None,
        proxy_url: Optional[str] = None,
        timeout: int = 30,
    ):
        self.token = token
        self.api_endpoint = api_endpoint.rstrip("/")
        self.query_url = f"{self.api_endpoint}/query"
        self.space_mrn = space_mrn
        self.page_size = min(int(page_size), 100)  # Mondoo API hard limit is 100
        self.timeout = timeout

        # TLS context — always verify; allow a custom CA bundle for self-hosted
        # Mondoo deployments behind a corporate CA.
        ca_bundle = ca_bundle or os.environ.get("MONDOO_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if ca_bundle:
            self._ssl_context = ssl.create_default_context(cafile=ca_bundle)
        else:
            self._ssl_context = ssl.create_default_context()

        # Proxy — explicit setting wins, else honour environment.
        proxy_url = proxy_url or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy_url:
            proxy_handler = urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
            https_handler = urllib.request.HTTPSHandler(context=self._ssl_context)
            self._opener = urllib.request.build_opener(proxy_handler, https_handler)
        else:
            https_handler = urllib.request.HTTPSHandler(context=self._ssl_context)
            self._opener = urllib.request.build_opener(https_handler)

    # ------------------------------------------------------------------
    def _graphql(self, query: str, variables: dict) -> dict:
        payload = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(
            self.query_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "Splunk-TA-Mondoo/1.0",
            },
            method="POST",
        )

        last_error: Optional[BaseException] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    charset = resp.headers.get_content_charset("utf-8")
                    body = resp.read().decode(charset)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    retry_after = self._compute_retry_after(exc, attempt)
                    logger.warning(
                        "Mondoo API HTTP %s on attempt %d/%d — retrying in %.1fs",
                        exc.code, attempt + 1, _MAX_RETRIES + 1, retry_after,
                    )
                    time.sleep(retry_after)
                    last_error = exc
                    continue
                err_charset = exc.headers.get_content_charset("utf-8") if exc.headers else "utf-8"
                err_body = _redact_blob(exc.read().decode(err_charset, errors="replace"))
                raise RuntimeError(f"Mondoo API HTTP {exc.code}: {err_body[:500]}") from exc
            except urllib.error.URLError as exc:
                if attempt < _MAX_RETRIES:
                    retry_after = self._backoff_seconds(attempt)
                    logger.warning(
                        "Mondoo API connection error on attempt %d/%d (%s) — retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES + 1, exc.reason, retry_after,
                    )
                    time.sleep(retry_after)
                    last_error = exc
                    continue
                raise RuntimeError(f"Mondoo API connection error: {exc.reason}") from exc
        else:
            # Loop completed without `break` — shouldn't happen because of the
            # raises above, but be defensive.
            raise RuntimeError(f"Mondoo API exhausted retries: {last_error}")

        data = json.loads(body)
        if "errors" in data:
            raise RuntimeError(f"Mondoo GraphQL errors: {_redact_blob(json.dumps(data['errors']))}")
        return data

    # ------------------------------------------------------------------
    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        """Exponential backoff with full jitter."""
        base = min(_BACKOFF_MAX_SECONDS, _INITIAL_BACKOFF_SECONDS * (_BACKOFF_FACTOR ** attempt))
        return random.uniform(0, base)

    @classmethod
    def _compute_retry_after(cls, exc: urllib.error.HTTPError, attempt: int) -> float:
        """Honour a server-provided Retry-After header (delta-seconds or HTTP-date).

        Falls back to exponential backoff when the header is missing or
        unparseable. Capped at ``_BACKOFF_MAX_SECONDS`` to bound worst-case
        wait time.
        """
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                return min(_BACKOFF_MAX_SECONDS, float(header))
            except (TypeError, ValueError):
                pass  # HTTP-date format — not worth parsing here
        return cls._backoff_seconds(attempt)

    # ------------------------------------------------------------------
    def _paginate(self, query: str, variables: dict, data_path: list[str]):
        """Generic cursor-based pagination. Yields (node_dict, cursor) tuples."""
        while True:
            result = self._graphql(query, variables)
            container = result.get("data", {})
            for key in data_path:
                container = container.get(key, {})

            edges = container.get("edges", [])
            page_info = container.get("pageInfo", {})

            for edge in edges:
                node = edge.get("node", {})
                cursor = edge.get("cursor")
                yield node, cursor

            if not page_info.get("hasNextPage"):
                break
            end_cursor = page_info.get("endCursor")
            if not end_cursor:
                break
            variables["after"] = end_cursor

    # ------------------------------------------------------------------
    # Audit logs
    # ------------------------------------------------------------------
    def fetch_audit_logs(
        self,
        resource_mrn: Optional[str] = None,
        after_cursor: Optional[str] = None,
        timestamp_after: Optional[str] = None,
    ):
        mrn = resource_mrn or self.space_mrn
        if not mrn:
            raise ValueError("resource_mrn / space_mrn is required for audit log queries")

        variables: dict = {
            "first": self.page_size,
            "resourceMrn": mrn,
            "orderBy": {"direction": "ASC", "field": "TIMESTAMP"},
        }
        if after_cursor:
            variables["after"] = after_cursor
        if timestamp_after:
            variables["timestampFilter"] = {"timestamp": timestamp_after, "operator": "GT"}

        for node, cursor in self._paginate(AUDIT_LOG_QUERY, variables, ["auditlog"]):
            node["_cursor"] = cursor
            yield node

    # ------------------------------------------------------------------
    # Assets / devices
    # ------------------------------------------------------------------
    def fetch_assets(self, after_cursor: Optional[str] = None):
        variables: dict = {
            "spaceMrn": self.space_mrn,
            "first": self.page_size,
            "orderBy": {"direction": "ASC", "field": "NAME"},
        }
        if after_cursor:
            variables["after"] = after_cursor

        for node, cursor in self._paginate(ASSETS_QUERY, variables, ["assets"]):
            node["_cursor"] = cursor
            yield node

    # ------------------------------------------------------------------
    # Findings (CVEs, advisories)
    # ------------------------------------------------------------------
    def fetch_findings(
        self,
        finding_types: list[str],
        after_cursor: Optional[str] = None,
    ):
        variables: dict = {
            "scopeMrn": self.space_mrn,
            "first": self.page_size,
            "orderBy": {"direction": "DESC", "field": "RISK_SCORE"},
            "filter": {"types": finding_types},
        }
        if after_cursor:
            variables["after"] = after_cursor

        for node, cursor in self._paginate(FINDINGS_QUERY, variables, ["findings"]):
            node["_cursor"] = cursor
            yield node

    # ------------------------------------------------------------------
    # Check scores (per-asset)
    # ------------------------------------------------------------------
    def fetch_check_scores(self, after_cursor: Optional[str] = None):
        """Fetch all check scores for the space (includes per-asset breakdown)."""
        variables: dict = {
            "entityMrn": self.space_mrn,
            "first": self.page_size,
        }
        if after_cursor:
            variables["after"] = after_cursor

        for node, cursor in self._paginate(CHECK_SCORES_QUERY, variables, ["checkScores"]):
            node["_cursor"] = cursor
            yield node

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------
    def fetch_agents(self, after_cursor: Optional[str] = None):
        variables: dict = {
            "spaceMrn": self.space_mrn,
            "first": self.page_size,
            "orderBy": {"direction": "ASC", "field": "NAME"},
        }
        if after_cursor:
            variables["after"] = after_cursor

        for node, cursor in self._paginate(AGENTS_QUERY, variables, ["agents"]):
            node["_cursor"] = cursor
            yield node

    # ------------------------------------------------------------------
    def validate(self) -> bool:
        """Do a minimal query to verify credentials and connectivity."""
        mrn = self.space_mrn
        if not mrn:
            raise ValueError("space_mrn is required for validation")
        variables = {
            "first": 1,
            "resourceMrn": mrn,
            "orderBy": {"direction": "DESC", "field": "TIMESTAMP"},
        }
        result = self._graphql(AUDIT_LOG_QUERY, variables)
        return "data" in result
