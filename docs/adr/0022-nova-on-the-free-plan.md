# ADR-022: Amazon Nova on the Free plan, with AgentCore behind its own switch

- **Status:** Accepted (2026-10-10)
- **Refines:** DESIGN.md §26 model choice (Claude Sonnet- and Haiku-class) for Free plan environments,
  and the `models_enabled: false` setting in implementation-notes.md

## Context

The `dev` account is on the AWS Free plan and runs on its credits only. Claude on Bedrock is sold
through AWS Marketplace, which the Free plan restricts, so `dev` ran with `models_enabled: false`.
The harness used its offline keyword router, follow-up drafts used a template, and deep triage was
refused. That left the AWS Builder entry without a live Bedrock call.

Amazon Nova is Amazon's own Bedrock model family. Nova Micro, Lite and Pro run on demand in-Region
in us-east-1, so they need no cross-Region inference profile, and their usage is paid from Free
Tier credits like any other service. Nova 2 Lite needs a cross-Region profile in us-east-1, so it
is not used. AWS lists Bedrock AgentCore among the Free Tier services, but nothing here has
deployed AgentCore on this account yet.

Nova differs from Claude in two ways that matter here (Amazon Nova user guide, "Tool usage"):

- Before a tool call it writes its reasoning in `<thinking>` tags. AWS advises discarding it.
- It calls tools most reliably with greedy decoding: temperature 0 and top-k 1.

## Decision

- Each model slot takes either a Claude inference-profile id or an Amazon Nova model id. `dev` and
  `demo` use `amazon.nova-lite-v1:0` for the fast slot (`haiku`: harness, drafts, Analyst, Context)
  and `amazon.nova-pro-v1:0` for the reasoning slot (`sonnet`: Lead, Strategist, Critic).
- `watchfloor_core.bedrock` strips `<thinking>` blocks from every model reply that reaches a person
  or a parser: harness replies, drafts, specialist answers and the output gate. It also gives Nova
  greedy sampling in every Strands agent.
- A new `agents_enabled` switch separates AgentCore from Bedrock. With models on and agents off,
  the harness and drafts run on Nova, there is no Agents stack, and `start_deep_triage` is refused
  up front as before.
- An invoke grant names the exact model for an in-Region id
  (`arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`). For an inference profile
  it names the profile and the foundation models behind it, as before.
- The first time agents are switched on, `deploy-dev` deploys without the runtime, which creates
  the image repository, then pushes the image, then creates the runtime.

## Alternatives

- **Nova Micro for the fast slot.** It is the cheapest ($0.035 input and $0.14 output per million
  tokens), but the harness picks among 19 tools, and Lite ($0.06 and $0.24) is the safer choice for
  that. Switching is one config line.
- **Keep the offline engine.** The product would make no Bedrock call at all.
- **A model outside AWS, such as Gemini.** It is not paid from the AWS credits and does not meet the
  challenge's Bedrock requirement.

## Consequences

- A harness turn is roughly 5,000 input tokens, about $0.0004 on Nova Lite. Drafts cost less.
- Risk scores, health and bands stay deterministic (ADR-002, ADR-010), so no number a user hears
  comes from the model.
- Moving back to Claude after a Paid-plan upgrade is a change to the config alone.
