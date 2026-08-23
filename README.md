# Calendar Safe MCP

An unofficial, local MCP server for Apple Calendar. It uses EventKit, Apple's
supported calendar framework, and never reads Calendar databases, account
credentials, or the macOS permissions database directly.

Requires macOS 14 Sonoma or later and Python 3.12 or later.

It is macOS-only and provides four tools:

- `list_calendars`
- `list_events`
- `create_event`
- `update_event`

## Install

Install [uv](https://docs.astral.sh/uv/), then register the server with your MCP
client. For Codex:

```sh
codex mcp add calendar -- uvx --from git+https://github.com/jasongibby/calendar-safe-mcp calendar-safe-mcp
```

For another stdio MCP client, use:

```json
{
  "mcpServers": {
    "calendar": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/jasongibby/calendar-safe-mcp",
        "calendar-safe-mcp"
      ]
    }
  }
}
```

Call `list_calendars` after starting your MCP client. macOS should request
Calendar access for that client. Grant Full Access under **System Settings →
Privacy & Security → Calendars**.

## Safety and privacy

- Access uses only Apple's EventKit framework and normal macOS consent flow.
- Reads return scheduling fields only unless `include_details=true` is passed.
- Create and update responses stay compact and never echo notes, URLs, or alarms.
- Text queries search titles only unless `search_notes=true` is passed.
- Date ranges are limited to 366 days and response limits are capped at 1,000.
- Recurring updates require an explicit `span` of `this` or `future`.
- Event contents are never logged by the server.
- Delete and recurrence-creation tools are intentionally omitted.

Your MCP client and AI provider can see calendar data returned by the server.
Review their privacy policies before use. This project is not affiliated with
or endorsed by Apple.

See [Apple's EventKit documentation](https://developer.apple.com/documentation/eventkit).

## Development

```sh
uv sync
uv run python -m unittest discover -s tests -v
```

Tests use mocks and do not access Calendar data.

## License

MIT
