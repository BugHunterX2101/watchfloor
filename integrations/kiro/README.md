# Watchfloor in Kiro and Kiro Crew

Setup for using Watchfloor's MCP server from [Kiro](https://kiro.dev) and
[Kiro Crew](https://github.com/kirodotdev/KiroCrew) ([ADR-034](../../docs/adr/0034-kiro-as-an-mcp-client.md)).
Nothing here uses AWS.

What Kiro gets is **read-only**: the `wf-kiro` client may only hold `watchfloor.read`. It can look up
deals, read the risk briefing, explain a score, start a deep triage and propose an action. It cannot
confirm, undo, log updates or change preferences and standing orders; a person does that in Alexa+
or the harness. Watchfloor has no e-mail tool, so a "follow-up e-mail" is a draft in the reply.

## What has been run

Tried once, on 2026-10-23, on Windows with the Kiro Crew desktop app (its bundled `kiro-cli` 2.31.1)
against the live Render deployment.

| Step | Result |
|---|---|
| Sign-in as `wf-kiro` (authorization code with PKCE, `watchfloor.read`) | Worked, although Watchfloor's 401 carries no `WWW-Authenticate` hint |
| `initialize` with `2025-06-18`, then `tools/list` | Worked. The server agreed to `2025-06-18` and listed 8 read tools, none that write |
| `confirm_action` | Refused as an unknown tool |
| `get_risk_briefing` from `kiro-cli` | Worked in 0.48 s, about 0.09 credits |
| Kiro Crew chat: "top 3 at-risk deals" | Worked. It returned name, amount, stage, owner, health and band: Kestrel Biotech (29), Harbor & Pine Logistics (34), Orbital Dental Group (42) |
| Scheduling a digest in the chat | Created from plain words; the agent asked for a time zone and set "5 PM UTC, every day" |

Kiro Crew's chat turns cost about 0.13 to 0.33 credits each.

**Not yet seen:**

- **The digest firing.** It was created, not observed running. The saved job
  (`~/.kiro/crew/crons.json`) is `0 17 * * *` in UTC, enabled, never run. Its message is the
  agent's own wording and tells it to post to Slack with `send_message`.
- **Slack.** It was not connected in this run. The message went to the dashboard instead: Kiro Crew's
  notification log (`~/.kiro/crew/notifications.jsonl`) holds it as an "Agent Message". A real Slack
  post is untried.
- **The `watchfloor-digest` skill.** The digest above was an ad hoc prompt, not this skill, so none
  of the skill's rules (read only, no invented numbers) applied to it. See the digest section.

## Files

| File | Where it goes |
|---|---|
| [`mcp.json`](mcp.json) | Kiro IDE or CLI: `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user) |
| [`kirocrew-agent.jsonc`](kirocrew-agent.jsonc) | Kiro Crew: merge into `~/.kiro/agents/kirocrew.json` if the app has not already added it (see below) |
| [`oauth_endpoints.json`](oauth_endpoints.json) | Kiro Crew: `~/.kiro/crew/oauth_endpoints.json` (or under `KIROCREW_HOME`) |
| [`skills/watchfloor-digest/SKILL.md`](skills/watchfloor-digest/SKILL.md) | Kiro Crew: `~/.kiro/crew/skills/watchfloor-digest/SKILL.md` |

Replace `watchfloor.onrender.com` if you run your own deployment. The redirect URI must stay
`http://127.0.0.1:7778/oauth/callback` (or the `localhost` form): it is the one `wf-kiro` is
registered with, and Watchfloor matches it exactly. To register another, run
`python -m auth_server.clients --kiro-redirect-uri <uri>` (repeatable).

## Kiro

1. Copy `mcp.json` into place. `oauth.clientId` makes Kiro skip dynamic registration, which
   Watchfloor does not offer; `oauthScopes` stops it asking for `openid email profile offline_access`.
   Write the file by hand: `kiro-cli mcp add` has no OAuth options.
2. Connect. Sign in with a Watchfloor demo login and allow access. Ask for "the top 3 at-risk deals".

## Kiro Crew

1. Install it and run `kiro-cli login` (a browser sign-in with your Kiro account, and a plan with
   credits). On Windows it is a desktop app under `%LOCALAPPDATA%\Programs\KiroCrew` that bundles its
   own `kiro-cli` in `resources\backend-dist\kiro-cli`, which is not on `PATH`. On macOS and Linux,
   Kiro Crew's docs give `curl -fsSL https://download.crew.kiro.dev/cli.sh | sh`, then the dashboard
   at `http://localhost:5476`.
2. Put `mcp.json` at `~/.kiro/settings/mcp.json`, then restart Kiro Crew. In our run the app then
   copied the entry into `~/.kiro/agents/kirocrew.json` itself: it added `watchfloor` under
   `mcpServers` and `@watchfloor` under `tools`. Check that file. If the entry is missing, merge
   [`kirocrew-agent.jsonc`](kirocrew-agent.jsonc) (Kiro Crew's docs say the `kirocrew` agent pins
   `"includeMcpJson": false`).
3. **Calls then run without asking.** The same step also added `@watchfloor` to `allowedTools` and a
   `watchfloor/*` allow rule. That is acceptable here because the `wf-kiro` token cannot write. If you
   want a prompt per call, delete those two entries.
4. Add `oauth_endpoints.json`. Kiro Crew's docs say a consent URL that is not on its built-in list
   fails with "URL contained credential or exfiltration pattern". We installed the file before
   signing in and did not try without it.
5. Sign in once. `kiro-cli` meets the 401 and runs OAuth, and Kiro Crew shows an **Authorize** banner.
   On a remote gateway, paste the callback address into **Complete connection**. We signed in from a
   `kiro-cli` terminal; afterwards the command-line client and Kiro Crew's chat both worked.
6. Restart Kiro Crew (or drain its warm pool) and start a **new** chat: a process that started before
   the server was configured never loads it.

### Slack

Follow Kiro Crew's [Slack setup](https://github.com/kirodotdev/KiroCrew/blob/main/docs/guides/slack-setup.md):
`kirocrew manifest --url`, `kirocrew setup --slack` (app token `xapp-…`, bot token `xoxb-…`, your
member ID), `kirocrew doctor`, `kirocrew gateway`. Only the owner can use the gateway through Slack.
Not set up in our run.

### The end-of-day digest

Copy the skill into `~/.kiro/crew/skills/watchfloor-digest/SKILL.md`, then schedule it in a Kiro Crew
chat in plain words, for example "Every weekday at 17:00 in Asia/Kolkata, run $watchfloor-digest".
Name the skill; our run asked for the digest without it, so the schedule holds only the agent's own
wording. Say "weekday" if you mean it: our "5 PM UTC" became every day. The agent asks for a time zone
if you leave it out. `kirocrew cron list`, `cron pause <id>` and `cron remove <id>` manage schedules.
Without Slack, the agent reported that the output goes to the dashboard as a notification. How a
schedule's output reaches a Slack channel is not documented, so check where it lands.

## Known risks

- Only one Kiro process can hold the sign-in callback port (7778) at a time. In our run an interactive
  `kiro-cli` waiting for the Watchfloor sign-in held it, and every other Kiro process then failed with
  "Only one usage of each socket address" and did not load the server: the chat answered that it had no
  such tool. Finish or close the first sign-in.
- Watchfloor's 401 carries no `WWW-Authenticate` header (Alexa+ cannot take one). `kiro-cli` still
  found the sign-in, through `/.well-known/oauth-protected-resource`. If a future Kiro release stops
  doing so, check that URL first.
- If sign-in ends with `invalid_scope`, the client asked for more than `watchfloor.read` (for example
  `offline_access`, which Watchfloor does not offer). Check that `oauthScopes` reached the request.
- Kiro Crew's badge "Signed in" means a token file exists, not that it is valid.
- Credits are spent on every turn and every scheduled run.
