# Joachim WhatsApp MCP — read-only navigation upgrade

Drop-in replacement for `whatsapp_mcp_step9.py`, designed for the existing Railway `whatsapp-wacli` service. It is **not a public ChatGPT connector yet**. It runs an MCP Streamable HTTP server bound to `127.0.0.1:8765` without public authentication. **Do not expose it via a Railway public domain or proxy before adding OAuth access control.**

## Why this version

`wacli messages list` supports date filtering and a result limit, but not a reliable offset. Instead of retrieving thousands of messages or skipping dense windows, this implementation reads `<store>/wacli.db` with SQLite **`mode=ro` plus `query_only=ON`**, using ordered keyset cursors. It also uses the `wacli --read-only --json` CLI for canonical searches, contact lookup, context and diagnostics. The schema is documented at https://github.com/openclaw/wacli/blob/main/internal/store/schema.sql and direct read-only access at https://github.com/openclaw/wacli/blob/main/docs/integrations.md .

## MCP tools (15)

| Tool | Purpose |
|---|---|
| `archive_status` | Message count, coverage timestamps, stable snapshot high-watermark |
| `list_chats` | Find chats by partial name or JID, including groups |
| `recent_activity` | Chat-level message counts/direction during a date window, no bodies |
| `get_new_messages` | Guaranteed bounded rowid pages for inserted messages with snapshot |
| `get_messages` | Timestamp/date/chat/direction/media filtered keyset pagination |
| `get_edited_or_deleted_messages` | Detect in-place edits and deletions; omit deleted bodies |
| `get_starred_messages` | Browse manually starred messages with pagination |
| `search_messages_paged` | Literal substring search with complete cursor pagination |
| `search_messages_fast` | `wacli` full-text search (FTS5 when supported) |
| `get_message` | Fetch one message by chat ID and message ID |
| `get_message_context` | Neighboring messages around an identified message |
| `find_contacts` | Resolve a person to matching stored contacts |
| `history_coverage` | Inspect historical sync coverage and gaps |
| `sync_diagnostics` | Local-only `wacli doctor` status |
| `review_instructions` | Safe agent workflow, checkpoints and limitations |

No send, edit, delete, mark-read, login, sync or arbitrary shell tool is available.

## Drop into your *local* cloned `wacli` repository

1. Save `whatsapp_mcp.py` next to your local `Dockerfile`.
2. Make sure the **final runtime image** contains Python 3.10+ and the MCP Python SDK. With Alpine, you can use `python3`, `py3-pip` and a virtual environment, but **fix the existing `apk add` build error first**; it is separate from the MCP code. Do not assume the build is solved until Railway prints the actual root cause.
3. Add this to the Dockerfile **after installing Python and before the `USER wacli` line**:

   ```dockerfile
   RUN python3 -m venv /opt/wa-mcp \
       && /opt/wa-mcp/bin/pip install --no-cache-dir 'mcp==1.26.0'
   COPY whatsapp_mcp.py /opt/whatsapp_mcp.py
   ```

4. Deploy using your existing method: `railway up --service whatsapp-wacli`.
5. SSH into the running Railway service and test the archive, without showing message contents:

   ```bash
   WACLI_STORE_DIR=/data/store /opt/wa-mcp/bin/python /opt/whatsapp_mcp.py --self-test
   WACLI_STORE_DIR=/data/store /opt/wa-mcp/bin/python /opt/whatsapp_mcp.py --list-tools
   ```

6. **Do not change the Railway start command yet.** It should keep `wacli sync --follow` running. The MCP server is installed but **not running** until we configure a process supervisor or entrypoint that launches both services, and add OAuth before making it public.

## Suggested task-triage algorithm

1. `sync_diagnostics` / `history_coverage` to establish the index is usable and recognize gaps.
2. `archive_status` to get the current `snapshot_rowid`.
3. `recent_activity(after=..., before=..., snapshot_rowid=...)` to find active chats cheaply.
4. `get_new_messages(after_rowid=checkpoint, snapshot_rowid=..., limit=50)` repeatedly until `has_more=false`. For the **first** review, use `after=YYYY-MM-DD` if only seven days are desired. Later runs should omit the `after` filter so delayed historical imports are not silently skipped.
5. Use `get_message_context` and `get_messages` for ambiguous requests; use `search_messages_fast` for cross-chat topics and `search_messages_paged` if a complete set of search matches is required.
6. `get_edited_or_deleted_messages` on an overlapping time window to catch edits and deletions to already indexed messages.
7. Compare candidate obligations with both active and completed Todoist tasks; never assume every outgoing request is a pending task.
8. Only after **all** relevant Todoist updates succeed should the caller durably save the `next_rowid` checkpoint. This MCP is deliberately stateless and stores no checkpoints itself.

**Limitations:** WhatsApp controls historical backfill; voice-note audio and image contents are not transcribed by this server, only message metadata/captions/fallbacks appear. In-place edits do not change the original message rowid, which is why the overlap and edits tool exist. SQLite schema changes in later `wacli` releases may require updating the bridge. All timestamps are UTC; ISO dates are interpreted at UTC midnight.

## Offline tests

```bash
python3 -m unittest discover -s . -p 'test_*.py' -v
```

Tests use fake SQLite rows and a mocked CLI; they never access WhatsApp, Google, Todoist or Railway.
