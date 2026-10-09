"""SQL lives only here: one module per aggregate (TD §5.1).

Every function takes a connection obtained from ``db.ws_tx`` (RLS-scoped) or,
for non-tenant tables, ``db.system_tx``. Queries never filter by workspace
themselves; row-level security supplies that filter.
"""
