# ADR-034: Kiro and Kiro Crew as MCP clients, without AWS

- **Status:** Accepted (2026-10-22)
- **Refines:** ADR-001 (stateless MCP on 2025-11-25), ADR-006 (self-hosted OAuth), ADR-033 (Polly is now off on Render)

## Context

A proposal asked for Watchfloor to be reached from [Kiro Crew](https://github.com/kirodotdev/KiroCrew)
(AWS's open-source agent workspace, Apache-2.0): Slack threads that look up a deal and draft a
follow-up, and a scheduled end-of-day RevOps digest. Checked against the code and Kiro's own
documentation, parts of it were wrong:

- The MCP server is Starlette, not FastAPI, and the data is in Postgres, not DynamoDB.
- It is not free. Kiro Crew's default agent runs on `kiro-cli`, which needs `kiro-cli login` and a
  Kiro plan; each turn spends that plan's credits.
- Watchfloor has no e-mail tool. A follow-up can be drafted as text, not sent.
- No "AWS Builder Mini Challenge" could be found.

Three facts in Watchfloor blocked the idea, whatever the wording:

1. **The version handshake failed for every client but Alexa+.** Kiro Crew's remote probe offers
   `2025-06-18` in `initialize`. The SDK echoes any revision it knows (`mcp/server/runner.py`), so it
   agreed to `2025-06-18`; the version gate allowed only `2025-11-25`, so the client's next request
   was refused with a 400. `2024-11-05` and `2025-03-26` failed the same way.
2. **No dynamic registration.** `kiro-cli` tries Dynamic Client Registration and otherwise falls back
   to a default client. Watchfloor advertises no `registration_endpoint` (ADR-006), so Kiro needs a
   client registered in advance and named with `oauth.clientId`.
3. **Who may write.** Kiro Crew acts unattended (schedules, Slack). Watchfloor's writes are proposed,
   then confirmed by a person (ADR-003).

The person asked for no AWS at all.

## Decision

**Two versions are allowed by default:** `WF_PROTOCOL_VERSIONS=2025-06-18,2025-11-25`. Alexa+ keeps
`2025-11-25`; Kiro and other clients still on `2025-06-18` keep theirs.

**`initialize` gets a counter-offer the gate accepts** (`middleware/version.py`). When the offered
`protocolVersion` is not allowed, the gate rewrites it to the newest allowed handshake revision before
the SDK reads it, so the server answers with that revision as the lifecycle spec asks, and the client
adopts it or disconnects. An unsupported `MCP-Protocol-Version` header is still a 400, even on
`initialize` (CF-06). The rewrite touches only `params.protocolVersion` and fixes `content-length`.

**A read-only public client, `wf-kiro`** (`auth_server/clients.py`): authorization code with PKCE and
refresh tokens, no secret (the Kiro IDE supports public clients only), scope `watchfloor.read` only.
Its redirect URIs are pinned exactly to `http://127.0.0.1:7778/oauth/callback` and
`http://localhost:7778/oauth/callback`, which Kiro's `oauth.redirectUri` sets. RFC 8252 lets a
server accept any loopback port, but that would loosen `validate()` for every client against an
exact-match design, and it would not help: without `clientId` Kiro never reaches this client at all.
With `watchfloor.read`, Kiro sees the read tools and `propose_action`, never `confirm_action`, undo,
updates, preferences or standing orders. Nothing changes until a person confirms in Alexa+ or the
harness.

**Configuration ships as examples, not code** (`integrations/kiro/`): `mcp.json` for Kiro, the entry
for Kiro Crew's agent file, the `oauth_endpoints.json` entry its consent-URL gate needs, and a
digest skill. They follow Kiro's and Kiro Crew's documentation and were tried once on 2026-10-23
(see Evidence); running them needs a Kiro plan, whose credits each turn spends.

**No AWS.** Nothing in this path calls AWS. On Render, `WF_POLLY_VOICE` is to be unset, so
`/api/speak` answers 404 and the page speaks with the browser's voice (ADR-033's fallback). The CDK
stacks are left as they are; there `WF_PROTOCOL_VERSIONS` stays `2025-11-25`, and a Kiro client
would be counter-offered that.

## Risks

- **A 401 still has no `WWW-Authenticate`** (constraint C3, for Alexa+). Kiro Crew's guide says
  `kiro-cli` runs OAuth itself after a 401, and both protected-resource paths are served, with
  `resource` equal to `WF_RESOURCE_URI`. `kiro-cli` 2.31.1 found the sign-in without the header
  (2026-10-23), so this held for that version; a later release could change it.
- **One process may hold the callback port.** `wf-kiro`'s redirect URI is fixed at port 7778, so a
  second Kiro process started while another waits for sign-in fails to bind it and does not load the
  server. It surfaces only as the agent saying it has no such tool.
- **Not yet observed:** a scheduled digest running, and a Slack post. Slack was not connected; the
  agent reported a dashboard notification instead.
- A client that speaks only `2025-06-18` and is counter-offered `2025-11-25` (where an operator
  narrows the list) will disconnect. That is why `2025-06-18` is allowed, not only counter-offered.
- A deployment that sets `WF_PROTOCOL_VERSIONS` explicitly keeps its own list; Render's must be
  changed by hand.

## Evidence

- `tests/unit/test_protocol_version.py`: the counter-offer, the untouched paths, protocol order.
- `tests/conformance/test_transport.py`: a `2025-06-18` client initializes and lists tools; `2024-11-05`,
  `2025-03-26` and `2099-01-01` offers end on `2025-11-25` and are served.
- `tests/integration/test_auth_server.py`: `wf-kiro` links, gets `watchfloor.read` only, sees no write
  tool, and is refused `watchfloor.write`.
- A live run against the Render deployment on 2026-10-23: sign-in as `wf-kiro` granted
  `watchfloor.read` only; `initialize` at `2025-06-18` was agreed and `tools/list` showed 8 read tools and
  no write tool; `confirm_action` was refused as unknown; `2025-11-25` and `2024-11-05` offers ended on
  `2025-11-25`. Then `kiro-cli` 2.31.1 and the Kiro Crew desktop app (Windows) each ran
  `get_risk_briefing` and returned the same three deals, and Kiro Crew accepted a "5 PM UTC" schedule.
- Sources: kiro.dev/docs/mcp/configuration; github.com/kirodotdev/KiroCrew (`docs/architecture/mcp.md`,
  `docs/guides/connecting-remote-oauth-mcp-server.md`, `docs/guides/slack-setup.md`, `skills/README.md`).
