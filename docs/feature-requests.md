# Feature requests

Requests for the platforms Watchfloor is built on, collected while building (DESIGN.md §27). Each
links to the friction-log entry that prompted it.

Priority: **P0** blocks the product · **P1** needs a workaround · **P2** nice to have.

| # | Platform | Request | Why | Priority | Source |
|---|---|---|---|---|---|
| 1 | Strands Agents | Support MCP Python SDK 2.x (widen the `mcp<2.2` bound) | Agents and the 2025-11-25 MCP servers they call can't share one lock | P1 | [friction 1](friction-log.md#1-strands-agents-cannot-share-an-environment-with-the-current-mcp-sdk) |
| 2 | AgentCore Memory | List a memory resource's strategy namespaces from the SDK; document prefix retrieval | Retrieval needs the exact namespace layout, which is invisible from code | P2 | [friction 2](friction-log.md#2-agentcore-memory-retrieval-needs-exactly-one-namespace-argument) |
| 3 | AgentCore Runtime SDK | A documented host setting for `BedrockAgentCoreApp.run` | The listen address depends on Docker marker files | P2 | [friction 3](friction-log.md#3-the-agentcore-app-picks-its-listen-address-from-docker-markers) |
