# ADR-014: Free-tier-eligible instance types on a Free plan account

- **Status:** Accepted (2026-10-09)
- **Supersedes in part:** ADR-009 (the t4g.nano size); DESIGN.md §18.1 (7-day backups)

## Context

The `dev` account is on the AWS Free plan and runs on its credits only. On that plan EC2 refuses to
launch an instance type that is not free-tier eligible: the first deploy's fck-nat Auto Scaling
group retried `t4g.nano` for over half an hour with "The specified instance type is not eligible for
Free Tier". The eligible arm64 types in us-east-1 are `t4g.micro` and `t4g.small`. The plan also
limits RDS features, and backup retention is one of them.

## Decision

- The NAT instance is fck-nat on `t4g.micro` in every environment.
- RDS backup retention is `db_backup_days` in the environment config: 7 by default, 1 in `dev`.

## Consequences

- The NAT instance costs about $3 more a month than the nano would, paid from the credits.
- `dev` can be restored to at most a day back. `demo` keeps 7 days.
