"""Platform stand-in (design decision D14): a small web app over the same service layer.

It shows what each user sees and is where a person publishes drafts. It is not part of the
MCP server. The browser UI is AI-generated and labeled as such; the security layer here is
not: it reuses service.py's rules and the tenant-scoped data access.
"""
