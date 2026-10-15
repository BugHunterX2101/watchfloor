# ADR-024: An Alexa+ service token for tool discovery

- **Status:** Accepted (2026-10-15)
- **Refines:** ADR-006 (self-hosted OAuth)

## Context

The MCP server is private: every `POST /mcp`, `initialize` and `tools/list` included, answers 401
without a bearer token. Alexa+ reads an add-on's tools when it is deployed (`alexa-ai deploy`),
before any customer has linked an account. Amazon's MCP toolkit documents two tiers of
authentication for this case:

- **Tier 1, service level**, "if you have a private MCP Server": `client_credentials`, an
  `mcp:service` token that must not reach user data.
- **Tier 2, user level**: account linking (`authorization_code` + PKCE), which we already serve.

Its Tier 1 checklist: `client_credentials` in the metadata's `grant_types_supported`; HTTP Basic or
POST body client credentials; `resource` checked against the MCP server URI; `mcp:service` only;
`expires_in` at most 3600; no refresh token; OAuth error bodies; no user data behind the token.

## Decision

- `wf-alexa` also holds `client_credentials` and the `mcp:service` scope. Its token has
  `sub = svc:wf-alexa`, no `ws` and no `job`, lasts 3600 s, and comes with no refresh token. A
  `resource` other than this MCP server is `invalid_target`. With no `scope`, the grant issues the
  app's service scope, never a user scope.
- Sign-in still offers only user scopes (`authorize` intersects with them), so a linked customer's
  token never carries `mcp:service`, and the protected-resource metadata does not list it.
- The MCP server accepts a service token only alone: with any other scope, a `ws` or a `job`, it is
  401. Such a token may call `initialize`, `notifications/initialized`, `ping`, `tools/list` and the
  `resources/*` methods (the `ui://` views are static). `tools/list` shows the catalogue a linked
  user sees. Anything else is 403, which Alexa+ takes as the cue to link an account.

## Consequences

- `alexa-ai deploy` can introspect the server without a customer's token, and no tool call is
  possible with it.
- The authorization server metadata does not list `mcp:service` in `scopes_supported`, because
  Alexa+ requests the listed scopes during linking. If the Local Inspector or certification asks for
  it there, list it and drop it in `authorize` instead.
