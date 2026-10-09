"""The untrusted-content envelope (DD §10.6), shared with the MCP server.

Agent-only tools already wrap CRM bodies on the server. The agents use the same
function for the one other source of free text they handle: memory records.
"""

from watchfloor_core.envelope import wrap_untrusted

__all__ = ["wrap_untrusted"]
