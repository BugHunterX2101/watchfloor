# Watchfloor in Kiro and Kiro Crew

Examples for using Watchfloor's MCP server from [Kiro](https://kiro.dev) and
[Kiro Crew](https://github.com/kirodotdev/KiroCrew) ([ADR-034](../../docs/adr/0034-kiro-as-an-mcp-client.md)).

> **Untested.** These follow Kiro's and Kiro Crew's documentation (October 2026), but nobody has run
> them against Watchfloor yet: that needs `kiro-cli login` and a Kiro plan, whose credits each turn
> spends. Nothing here uses AWS.

What Kiro gets is **read-only**: the `wf-kiro` client may only hold `watchfloor.read`. It can look up
deals, read the risk briefing, explain a score, start a deep triage and propose an action. It cannot
confirm, undo, log updates or change preferences and standing orders; a person does that in Alexa+
or the harness. Watchfloor has no e-mail tool, so a "follow-up e-mail" is a draft in the reply.

## Files

| File | Where it goes |
|---|---|
| [`mcp.json`](mcp.json) | Kiro IDE or CLI: `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user) |
| [`kirocrew-agent.jsonc`](kirocrew-agent.jsonc) | Kiro Crew: merge into `~/.kiro/agents/kirocrew.json` |
| [`oauth_endpoints.json`](oauth_endpoints.json) | Kiro Crew: `~/.kiro/crew/oauth_endpoints.json` (or under `KIROCREW_HOME`) |
| [`skills/watchfloor-digest/SKILL.md`](skills/watchfloor-digest/SKILL.md) | Kiro Crew: `~/.kiro/crew/skills/watchfloor-digest/SKILL.md` |

Replace `watchfloor.onrender.com` if you run your own deployment. The redirect URI must stay
`http://127.0.0.1:7778/oauth/callback` (or the `localhost` form): it is the one `wf-kiro` is
registered with, and Watchfloor matches it exactly. To register another, run
`python -m auth_server.clients --kiro-redirect-uri <uri>` (repeatable).

## Kiro

1. Copy `mcp.json` into place. `oauth.clientId` makes Kiro skip dynamic registration, which
   Watchfloor does not offer; `oauthScopes` stops it asking for `openid email profile offline_access`.
2. Open the MCP panel and connect. Sign in with a Watchfloor demo login and allow access.

## Kiro Crew

1. Install and start it (`curl -fsSL https://download.crew.kiro.dev/cli.sh | sh`, then the dashboard
   at `http://localhost:5476`), and run `kiro-cli login`.
2. Put the `watchfloor` entry in the **agent file** and add `@watchfloor` to its `tools`. The
   `kirocrew` agent pins `"includeMcpJson": false`, so an entry only in `~/.kiro/settings/mcp.json`
   added after a render is silently not loaded. Do not add it to `allowedTools`: that auto-approves
   every call and skips Kiro Crew's governance hooks.
3. Add `oauth_endpoints.json`. Watchfloor's consent URL is not on Kiro Crew's built-in list, and
   without the entry sign-in fails with "URL contained credential or exfiltration pattern".
4. Start a session. `kiro-cli` meets the 401 and runs OAuth; Kiro Crew shows an **Authorize** banner.
   On a remote gateway, paste the callback address into **Complete connection**.
5. Restart the gateway (or drain the warm pool) and start a new chat: tools from a newly signed-in
   server appear only in a fresh process.

### Slack

Follow Kiro Crew's [Slack setup](https://github.com/kirodotdev/KiroCrew/blob/main/docs/guides/slack-setup.md):
`kirocrew manifest --url`, `kirocrew setup --slack` (app token `xapp-…`, bot token `xoxb-…`, your
member ID), `kirocrew doctor`, `kirocrew gateway`. Only the owner can use the gateway through Slack.

### The end-of-day digest

Copy the skill, then schedule it in a Kiro Crew chat in plain words, for example "Every weekday at
17:00 in Asia/Kolkata, run $watchfloor-digest". Schedules are timezone-aware; `kirocrew cron list`,
`cron pause <id>` and `cron remove <id>` manage them. How a schedule's output reaches a Slack channel
is not documented, so check where it lands before relying on it.

## Known risks

- Watchfloor's 401 carries no `WWW-Authenticate` header (Alexa+ cannot take one). Kiro should find
  the sign-in through `/.well-known/oauth-protected-resource`, which is served, but this is unverified.
  If sign-in never starts, check that URL first.
- Kiro Crew's badge "Signed in" means a token file exists, not that it is valid.
