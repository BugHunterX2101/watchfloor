# ADR-016: No reserved Lambda concurrency while the account quota is 10

- **Status:** Accepted (2026-10-09)
- **Refines:** DESIGN.md §18.1 (MCP server reserved concurrency 20, dispatcher 3)

## Context

A new AWS account starts with a Lambda concurrency quota of 10, and Lambda keeps at least 10
unreserved. Any reservation is then refused: the first `dev` deploy failed on the MCP server with
"Specified ReservedConcurrentExecutions for function decreases account's
UnreservedConcurrentExecution below its minimum value of [10]". Raising the quota is a Service
Quotas request that AWS grants on its own schedule.

## Decision

- `reserve_concurrency` in the environment config, true by default. `dev` sets it to false.
- With it false, `PythonLambda` sets no reserved concurrency on any function.

## Consequences

- The functions share the account's 10 executions. A burst of dispatcher work can delay MCP calls;
  at demo traffic that does not happen.
- The account quota still caps the total, so nothing can scale past what the database can take.
- Once the quota is raised (1000 is the usual default), setting `reserve_concurrency` back to true
  restores the design's limits.
