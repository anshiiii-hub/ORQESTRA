# Restricted research tools

Checkpoint 08 adds an authenticated API path for fetching a small set of operator-approved research sources. It is intentionally separate from the workflow worker: current CSV agents receive no network tools, and the worker is not given research network configuration.

## Operator configuration

Research is disabled by default. To enable it, set `RESEARCH_ENABLED=true` and `RESEARCH_ALLOWED_DOMAINS` to comma-separated exact hostnames, for example `docs.example.com,standards.example.org`. Subdomains are not implicitly allowed. Do not put credentials or URLs in this list.

The endpoint accepts one to three caller-provided HTTPS URLs on port 443. Before fetching, it checks the exact hostname, resolves all IPv4 and IPv6 answers, rejects the request if any answer is not globally routable, and connects directly to one validated numeric address while retaining TLS hostname verification. Only GET requests are sent; redirects, IP-literal URLs, credentials, non-text content, and oversized responses are rejected. The fetch has a six-second timeout and a 256 KiB response cap. No proxy settings are read, and the client does not follow links from fetched pages.

## API

- `GET /api/v1/research/config` reports whether the feature is available and its bounds.
- `POST /api/v1/research/search` accepts `workspace_id`, a short query, and one to three `urls`. Owners and members may create runs; viewers may not.
- `GET /api/v1/research/runs/{run_id}` returns a run only to a member of its workspace.

Each run stores its normalized query and completion status. Each source record stores the requested URL, title, bounded text excerpt, content type, SHA-256 of the fetched bytes, fetch time, status, and a safe error code when fetching fails. Records have a database constraint fixing `trust_level` to `untrusted`.

## Trust boundary

Fetched text is untrusted reference data. HTML script, style, iframe, object, template, SVG, and noscript contents are discarded. Text is never evaluated and does not alter the authenticated caller's permissions or the agent/tool policy. The UI labels the text untrusted. Search terms only rank extracted paragraphs; this checkpoint does not perform broad web search, link crawling, or model-assisted source synthesis.

The domain allowlist is an operator trust decision: approved domains may serve user-generated or compromised content. DNS validation and IP pinning reduce SSRF and DNS-rebinding risk, but this feature is not a production deployment approval. Keep the application private and review outbound network policy before enabling it.

## Verification

The test suite covers configuration defaults, exact hostname and URL policy, public-address validation, extraction of active markup, content hashing, redirect/content-type/size rejection, and workspace authorization. API integration tests mock outbound fetching and never contact the public internet.
