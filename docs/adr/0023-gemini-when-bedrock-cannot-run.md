# ADR-023: Gemini, through Strands, when Bedrock can't run

- **Status:** Accepted (2026-10-15)
- **Refines:** ADR-022 (Amazon Nova on the Free plan)

## Context

Every Bedrock inference quota on the `dev` account is 0 (on-demand, cross-Region and global, for
every model), and so are AgentCore's (ADR-022, update of 2026-10-15). Only AWS Support can raise
them, and re:Post reports put such cases at days to weeks, sometimes refused. With models off, the
harness answers with its keyword router and follow-up drafts are a template.

Strands, which the harness and the triage agents are built on, is not tied to Bedrock: it ships a
Gemini provider. Google AI Studio's Gemini API has a free tier that needs no card. The AWS Builder
mini challenge names the Strands SDK among the AWS services that qualify.

## Decision

- Each model caller tries its models in order: Bedrock while `models_enabled` is on, then Gemini
  while `gemini_enabled` is on, then the deterministic fallback (the harness's keyword router, the
  draft template). A throttled or refused model hands over at once; nothing waits on a quota.
- The harness runs Gemini through Strands' `GeminiModel`. Drafts call the Gemini API's
  `generateContent` directly, as they call Bedrock's Converse directly, and drop any thought parts.
- The model is `gemini-flash-lite-latest`, Google's alias for its current Flash-Lite model, so a
  model retirement doesn't break the environment. Flash-Lite has the largest free daily allowance.
- The key is a plain-string secret, `wf-<env>/models/gemini`, stored by hand. CDK references it by
  name and grants read access to the harness and the dispatcher only. It is read at most every five
  minutes, so storing or replacing it needs no deploy. It never goes into an environment variable,
  a log line or a response; `/healthz` lists the model names only.
- Gemini keeps its default temperature: Google advises against lowering it for tool use.

## Alternatives

- **Groq or Cerebras free tiers.** Groq's free tokens-per-minute limit (6,000 to 8,000) is about one
  harness turn a minute, since a turn carries 19 tool schemas. Cerebras's free tier has become a
  card trial in some reports, with an 8,000-token context cap.
- **Upgrade to the Paid plan.** It doesn't raise quotas set to 0, and it ends the plan's guarantee
  that nothing is charged.
- **Wait for AWS Support.** That is the fallback state this ADR improves on.

## Consequences

- The free tier's limits are per Google Cloud project and change over time. A run of deep reviews or
  evals can exhaust a day's allowance; the fallbacks keep every feature answering.
- Google may use free-tier prompts to improve its products. `dev` holds only the synthetic
  Harbor & Pine data, so no customer data reaches it.
- When AWS raises the quotas, `models_enabled: true` puts Bedrock first again, with Gemini behind it.
- AgentCore stays refused, so deep triage runs on Lambda (below).

## Update (2026-10-15): deep triage on Lambda

- `agents_host: lambda` deploys the triage agents as a plain function, `wf-<env>-triage`, in the
  async stack, outside the VPC: it reaches only the MCP server and the model. Its handler runs the
  same event stream as the AgentCore entrypoint and returns the events; the dispatcher invokes it
  synchronously, once, and reads the terminal event as it reads the runtime's stream.
- The agents run on Bedrock while `models_enabled` is on, else on the Gemini model for both tiers.
  Strands' retry back-off is off for every agent, so a throttled model fails at once: a specialist
  returns an error the Lead works around, and a Lead whose model fails hands the review to the
  offline engine. The result then says `"engine": "offline"`, and the gate and evidence check run
  as for any other draft.
- There is no AgentCore Memory on Lambda: `recall_preferences` returns an empty list.
- `start_deep_triage` is offered where `agents_enabled` is on and Bedrock or a Gemini model is.
- Once AWS enables AgentCore, `agents_host: agentcore` (with `models_enabled: true`) moves the
  agents back to the runtime.
