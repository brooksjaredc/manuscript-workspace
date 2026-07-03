# Manuscript Workspace

Manuscript Workspace is a local-first MCP server that lets ChatGPT Developer Mode safely read, search, edit, and restore files in a manuscript folder. It does not create another chatbot UI and it does not call the OpenAI API for reasoning. ChatGPT remains the writing partner; this server only exposes carefully scoped local document tools.

The server exposes Streamable HTTP at `/mcp` and a content-free health endpoint at `/health`.

## Security Model

All tool paths are treated as untrusted input. The server rejects absolute paths, `..` traversal, unsupported extensions, binary files, and symlinks that resolve outside `MANUSCRIPT_ROOT`. Supported editable files are `.md`, `.txt`, `.json`, `.yaml`, and `.yml`.

Writes use same-directory temporary files plus `fsync` and atomic rename. Every mutation first creates a snapshot under `.manuscript-history/`, independent of Git. Existing-file mutations require an `expected_revision` SHA-256 value so manual edits made between a read and write cannot be silently overwritten.

Deletion is disabled by default and the delete tool is not advertised unless `deletion_enabled` is true in `manuscript.config.json`. Logs do not include manuscript contents.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Python 3.12 or newer is required.

## Configure

Point the server at your manuscript folder:

```bash
export MANUSCRIPT_ROOT=/absolute/path/to/my/book
```

You can also pass the root explicitly:

```bash
python -m manuscript_workspace --root /absolute/path/to/my/book
```

Optionally copy `manuscript.config.example.json` into your manuscript root as `manuscript.config.json`:

```json
{
  "project_name": "Untitled Novel",
  "reference_documents": [
    "creative-constitution.md",
    "story-scratchbook.md",
    "characters.md",
    "timeline.md"
  ],
  "chapter_globs": [
    "chapters/*.md",
    "chapter-*.md"
  ],
  "deletion_enabled": false
}
```

Optional project-level writing guidance belongs in `chatgpt-project-instructions.md` in the manuscript root. Ordinary chapter prose is never treated as system-level instruction.

Temporary HTTPS tunnel hosts are accepted by default for `*.trycloudflare.com`, `*.ngrok-free.app`, and `*.ngrok.app`, along with local development hosts. To add another host, set a comma-separated allowlist addition:

```bash
export MANUSCRIPT_ALLOWED_HOSTS=your-domain.example,another-tunnel.example
```

## Run

```bash
python -m manuscript_workspace
```

or:

```bash
python -m manuscript_workspace --root /path/to/book --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Local MCP endpoint:

```text
http://127.0.0.1:8000/mcp
```

Use this local endpoint for MCP Inspector, local smoke tests, and the tunnel client. Do not paste it into ChatGPT's app URL field; ChatGPT requires a publicly reachable HTTPS endpoint, normally ending in `/mcp`.

## Test

```bash
pytest
```

To inspect the MCP server locally:

```bash
MANUSCRIPT_ROOT=/absolute/path/to/my/book mcp dev src/manuscript_workspace/dev.py
```

For local app testing, run the server and connect a local MCP client to `http://127.0.0.1:8000/mcp`.

## Connect To ChatGPT Developer Mode

OpenAI’s current Apps SDK docs describe ChatGPT apps as MCP servers whose tools are selected by ChatGPT, with Streamable HTTP recommended for transport. ChatGPT Developer Mode supports full MCP client access for read and write tools, and write tools should be guarded with confirmation settings.

Recommended ChatGPT app permission setting: **Ask before making changes**.

ChatGPT's app URL field must receive a publicly reachable HTTPS endpoint, usually in this shape:

```text
https://your-public-or-tunnel-host.example/mcp
```

The local URL `http://127.0.0.1:8000/mcp` is the upstream target for a tunnel, not the URL to paste into ChatGPT.

Preferred private setup:

1. Start Manuscript Workspace locally.
2. Create or select a Secure MCP Tunnel in the OpenAI Platform tunnel settings.
3. Run `tunnel-client` so it can reach `http://127.0.0.1:8000/mcp`.
4. In ChatGPT, enable Developer Mode under Settings.
5. Create an app from the tunnel-backed MCP server. Use the HTTPS tunnel endpoint that ChatGPT shows or accepts, ending in `/mcp`; do not use the local `127.0.0.1` URL.
6. In a new ChatGPT conversation, choose Developer Mode from the plus menu and select Manuscript Workspace.

Less-private development alternative:

1. Start Manuscript Workspace locally.
2. Expose `http://127.0.0.1:8000/mcp` with an HTTPS tunnel such as ngrok or Cloudflare Tunnel.
3. In ChatGPT Developer Mode, create an app using the public HTTPS `/mcp` URL from the tunnel, for example `https://your-subdomain.ngrok.app/mcp`.

Use the HTTPS tunnel only when you are comfortable with the tunnel provider and URL exposure. Secure MCP Tunnel is the better default for private manuscripts because the local server does not need inbound internet access.

## Tools

Read tools:

- `manuscript.get_project_overview`
- `manuscript.list_documents`
- `manuscript.read_document`
- `manuscript.read_documents`
- `manuscript.read_project_context`
- `manuscript.search_documents`
- `manuscript.get_document_history`

Write tools:

- `manuscript.create_document`
- `manuscript.append_document`
- `manuscript.apply_patch`
- `manuscript.write_document`
- `manuscript.rename_document`
- `manuscript.restore_document_version`
- `manuscript.delete_document`, only when deletion is enabled

Read tools are annotated with `readOnlyHint: true`. Write tools are annotated as local-world tools, and full overwrite, restore, and delete tools carry destructive hints.

## Version History And Restore

Before every mutation, the previous file content is saved under:

```text
.manuscript-history/versions/<version-id>/
```

Each version records the relative path, timestamp, operation, old and new revisions when available, and change summary. Use `manuscript.get_document_history` to inspect versions and `manuscript.restore_document_version` to restore one. Restoration creates its own undo snapshot first.

This history system does not require Git and never commits or modifies Git state.

## Troubleshooting

- `MANUSCRIPT_ROOT is required`: export `MANUSCRIPT_ROOT` or pass `--root`.
- `range_required`: the file is larger than the configured read limit; ask ChatGPT to read a line range.
- `stale_revision`: the file changed since ChatGPT read it; ask ChatGPT to reread and retry.
- `deletion_disabled`: deletion is off, which is the default.
- Missing tools in ChatGPT: refresh the app in ChatGPT settings after restarting the server.
- ChatGPT does not pick the intended tool: name the app and tool explicitly in the prompt.

## Example Prompts

> Use Manuscript Workspace. First read the project overview, creative constitution, story scratchbook, and Chapter 3. Then continue Chapter 3. Do not modify any document until you show me your proposed direction.

> Use Manuscript Workspace to append the following ideas to the story scratchbook. Do not edit any chapter files.

> Read the constitution, scratchbook, and Chapter 2. Apply a targeted patch only to the final scene of Chapter 2. Preserve everything before that scene.

> Search every chapter and reference document for statements about how Barry accesses hyperspace. Report contradictions but do not edit anything.
