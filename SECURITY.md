# Security

SwissDataMCP is intended for a trusted local user and one server process per workspace.
The HTTP command listens on 127.0.0.1, validates Host and Origin headers, and limits
request bodies. It does not provide OAuth, authentication, tenancy, or a public service.
Other trusted local applications can access the local endpoint.

Downloads accept public HTTP(S) sources, reject credentials and private/local DNS
answers, revalidate redirect targets, and block HTTPS downgrades. Size limits apply
to declared and streamed bytes. DNS is checked before connecting; the HTTP transport
resolves again, so these checks do not eliminate DNS-rebinding races. Adversarial
deployments need network egress controls and a separate authenticated deployment design.
Environment proxy settings are disabled for resource downloads.

Table names are validated, internal registry names are reserved, and filter values
are parameterized. Loaded column identifiers are escaped. No arbitrary SQL tool is
exposed. Source descriptions and cells remain untrusted input to the assistant.

`swissdatamcp serve` serves the runtime workspace, including downloads and the database.
Keep it on loopback and do not expose it to untrusted networks. HTML dashboards load
Plotly from a public CDN and need network access to display interactive charts.
Workspace resources and export manifests expose local paths to the connected MCP client.

Report vulnerabilities through GitHub's private vulnerability reporting if enabled,
or contact the maintainer via the GitHub profile before disclosing exploit details.
For ordinary defects, open an issue with a reproducible example excluding private data.
