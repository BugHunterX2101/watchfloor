# ADR-015: The harness can run without CloudFront

- **Status:** Accepted (2026-10-09)
- **Refines:** DESIGN.md §17 (the harness page on S3 + CloudFront)

## Context

A new AWS account cannot create CloudFront distributions until AWS Support verifies it: the first
`dev` deploy failed with "Your account must be verified before you can add new CloudFront
resources". Verification is a support case that takes an unknown time. The harness API already
serves its built page itself when one is present (`make dev` does exactly that).

## Decision

- `cdn` in the environment config, true by default. `dev` sets it to false until the account is
  verified.
- With `cdn: false` the harness stack has no bucket or distribution. The harness_api Lambda bundle
  carries the built page (`harness_web/`), `WF_HARNESS_WEB_DIST` points at it, and the API gets a
  `GET /` route. The harness answers on its execute-api host (or its custom domain, mapped on the
  API), which `scripts/default_hosts.py` writes into the config as usual.

## Consequences

- One host for the page and the API still, so the session cookie stays first-party.
- The page loads through the Lambda: no edge caching, and a cold start on the first request.
- Turning `cdn` back on later changes the harness host; the next deploy picks up the new one.
