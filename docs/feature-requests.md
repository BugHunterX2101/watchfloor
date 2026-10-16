# Feature requests

Requests for the platforms Watchfloor is built on, collected while building (DESIGN.md §27). Each
links to the friction-log entry that prompted it.

Priority: **P0** blocks the product · **P1** needs a workaround · **P2** nice to have.

| # | Platform | Request | Why | Priority | Source |
|---|---|---|---|---|---|
| 1 | Strands Agents | Support MCP Python SDK 2.x (widen the `mcp<2.2` bound) | Agents and the 2025-11-25 MCP servers they call can't share one lock | P1 | [friction 1](friction-log.md#1-strands-agents-cannot-share-an-environment-with-the-current-mcp-sdk) |
| 2 | AgentCore Memory | List a memory resource's strategy namespaces from the SDK; document prefix retrieval | Retrieval needs the exact namespace layout, which is invisible from code | P2 | [friction 2](friction-log.md#2-agentcore-memory-retrieval-needs-exactly-one-namespace-argument) |
| 3 | AgentCore Runtime SDK | A documented host setting for `BedrockAgentCoreApp.run` | The listen address depends on Docker marker files | P2 | [friction 3](friction-log.md#3-the-agentcore-app-picks-its-listen-address-from-docker-markers) |
| 4 | Alexa+ MCP Toolkit | Publish the `alexa-ai` CLI and the Local Inspector on public npm, or link an access-request form from the setup page | An account outside the access window cannot run the CLI, the Inspector or the simulator, and `AccessDenied` doesn't say why | P0 | [friction 4](friction-log.md#4-the-alexa-toolkit-is-closed-to-an-account-that-missed-the-access-window) |
| 5 | EC2 Auto Scaling | Fail fast on an instance type the Free plan can't launch, naming the eligible types | The group retried for over half an hour | P2 | [friction 5](friction-log.md#5-ec2-on-the-free-plan-retries-an-ineligible-instance-type-instead-of-failing) |
| 6 | CloudFront | Document account verification in the getting-started guide; show its status in the console | A new account's first distribution fails with no stated turnaround | P1 | [friction 6](friction-log.md#6-cloudfront-is-closed-to-a-new-account-until-aws-support-verifies-it) |
| 7 | Lambda | Say in the reserved-concurrency error that the account quota is the limit, with a Service Quotas link | No reservation can succeed at the default quota of 10 | P2 | [friction 7](friction-log.md#7-a-new-account-cannot-reserve-any-lambda-concurrency) |
| 8 | Bedrock | Mark in the model catalog which models a Free plan account can invoke | Claude is sold through Marketplace, which the Free plan restricts | P1 | [friction 8](friction-log.md#8-claude-on-bedrock-is-out-of-reach-on-the-free-plan) |
| 9 | Bedrock / AgentCore | Make inference quotas adjustable through Service Quotas; say at sign-up when they start at 0 | Every inference and AgentCore quota was 0 and only AWS Support could raise them | P1 | [friction 9](friction-log.md#9-every-bedrock-and-agentcore-quota-on-the-account-is-0) |
| 10 | Strands Agents | A total deadline per agent call, alongside the retry count | The default backoff outlasts a 30 s API Gateway limit | P2 | [friction 10](friction-log.md#10-strands-retries-outlast-the-api-gateway-timeout) |
| 11 | Lambda / AWS Health | A distinct error and a Health event when an account-level restriction denies a service | An empty `AccessDeniedException` on every call reads like an IAM problem and points nowhere | P0 | [friction 11](friction-log.md#11-lambda-refuses-every-call-on-the-account-without-a-reason) |
