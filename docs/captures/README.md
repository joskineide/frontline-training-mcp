# Wire captures: legacy vs modern MCP

Raw HTTP captured with `curl -i` against the local server (`mcp` 2.3.0). The request bodies are the `*.json` files; the responses (headers + body) are the numbered `.txt` files.

| # | Request | What it shows |
|---|---|---|
| 1 | **modern** `tools/call`: headers `MCP-Protocol-Version: 2026-07-28`, `Mcp-Method`, `Mcp-Name`; version and capabilities in `_meta` | No handshake and no session. A plain JSON reply with `resultType: "complete"`. `serverInfo` rides in the result's `_meta` |
| 2a | **legacy** `initialize` | A *negotiation*: the server returns its version, capabilities, `serverInfo` and `instructions`, once, and issues an `mcp-session-id`. The reply is an event stream (SSE) |
| 2b | legacy `notifications/initialized` | A notification (no `id`), so the server answers `202 Accepted` with no body |
| 2c | legacy `tools/call`, sending the session ID | An SSE reply (`event: message`), with no `resultType` |
| 3 | capture 1's body, **without** the MCP headers | `400 Missing session ID` |

What the comparison shows:
- **Same transport, different rules.** Both eras are HTTP POSTs to `/mcp`, answered by the same server. The era is a protocol choice, not a transport choice.
- **The headers decide the era.** Captures 1 and 3 send the identical body; only the headers differ.
  - Without `MCP-Protocol-Version`, the server assumes an old client, as the spec permits. Under legacy rules, a request other than `initialize` needs a session.
  - So the error names the rule that failed in the mode the server *inferred*, not the root cause.
  - The session ID in capture 3's response is a side effect of that legacy path.
- **Why legacy streams.** A legacy server may send requests back to the client in the middle of a call. Modern servers don't: a result can be `input_required` instead, which is why `resultType` exists. So a simple modern call is plain JSON.
- **A session ID is not authentication.** It continues a negotiated session, and anyone presenting it continues that session. Identity comes from the OAuth token on every request.
- **Why the spec dropped sessions.** Protocol-level state made every server stateful and every load balancer sticky. Application state now travels as explicit handles in tool arguments, and a modern client gets the server's `instructions` from `server/discover` instead of a handshake.
