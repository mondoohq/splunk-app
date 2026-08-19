[mondoo_input://<name>]
mondoo_config_blob = <string> JSON credential blob downloaded from the Mondoo console, or a raw JWT token string. Required.
log_types = <string> Comma-separated list of log types to collect. Supported: audit, advisories. Default: audit.
resource_mrn = <string> Space or organisation MRN. Auto-detected from the config blob if omitted. Format: //captain.api.mondoo.app/spaces/<space-id>.
page_size = <integer> Records per API request. Default: 100. Max: 100.
initial_lookback_days = <integer> Days of history to fetch on the first run. 0 = all available. Default: 7. Capped at 365 by the input.

# ---------------------------------------------------------------------------
# Example: minimal stanza
# ---------------------------------------------------------------------------
# [mondoo_input://my_space]
# mondoo_config_blob = {"api_endpoint":"https://us.api.mondoo.com","mrn":"//captain.api.mondoo.app/spaces/your-space","token":"eyJ..."}
#
# ---------------------------------------------------------------------------
# Example: full stanza
# ---------------------------------------------------------------------------
# [mondoo_input://prod_us]
# interval              = 300
# index                 = mondoo
# log_types             = audit,advisories,agents,assets,vulnerabilities,checks
# page_size             = 100
# initial_lookback_days = 30
# mondoo_config_blob    = {"api_endpoint":"https://us.api.mondoo.com","mrn":"//captain.api.mondoo.app/spaces/your-space","token":"eyJ..."}
# resource_mrn          = //captain.api.mondoo.app/spaces/your-space
#
# ---------------------------------------------------------------------------
# Environment variables honoured by the modular input:
# ---------------------------------------------------------------------------
#   MONDOO_CA_BUNDLE     Path to a custom CA bundle (PEM). Use when Mondoo
#                        is fronted by a corporate CA. Falls back to
#                        REQUESTS_CA_BUNDLE.
#   HTTPS_PROXY          HTTPS proxy URL. Honoured for outbound API calls.
