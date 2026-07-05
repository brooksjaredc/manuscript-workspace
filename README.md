# Manuscript Workspace

Manuscript Workspace is a local-first MCP server that lets ChatGPT Developer Mode safely read, search, edit, and restore files in a manuscript folder. It does not create another chatbot UI and it does not call the OpenAI API for reasoning. ChatGPT remains the writing partner; this server only exposes carefully scoped local document tools.

The server exposes Streamable HTTP at `/mcp`, a static OpenAPI Actions surface at `/actions/openapi.json`, and a content-free health endpoint at `/health`.

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
  "asset_import_roots": [
    "~/Downloads",
    "/Users/jcbrooks/flash-starwars/imports"
  ],
  "asset_root": "assets",
  "image_asset_root": "assets/images",
  "deletion_enabled": false
}
```

Optional project-level writing guidance belongs in `chatgpt-project-instructions.md` in the manuscript root. Ordinary chapter prose is never treated as system-level instruction.

Temporary HTTPS tunnel hosts are accepted by default for `*.trycloudflare.com`, `*.ngrok-free.app`, `*.ngrok-free.dev`, and `*.ngrok.app`, along with local development hosts. To add another host, set a comma-separated allowlist addition:

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

## Fallback: Custom GPT Actions

If ChatGPT Developer Mode discovers MCP tools but keeps routing calls back through schema discovery, use the static Actions surface instead. It exposes the same core manuscript operations as ordinary OpenAPI actions with stable operation IDs such as `read_document`, `list_documents`, and `apply_patch`.

Open the public base URL in a browser for a one-screen setup page:

```text
https://YOUR-NGROK-DOMAIN/
```

Actions OpenAPI URL:

```text
https://YOUR-NGROK-DOMAIN/openapi.json
```

For your current local ngrok setup, that shape is:

```text
https://dynasty-salvation-balmy.ngrok-free.dev/openapi.json
```

In the Custom GPT builder, add an Action and import that OpenAPI URL. The GPT should then receive first-class actions like:

```text
read_document
list_documents
search_documents
apply_patch
```

To read Chapter 6 through Actions, use:

```json
{
  "relative_path": "chapter-06.md",
  "maximum_characters": 30000
}
```

Optional protection for Actions endpoints:

```bash
MANUSCRIPT_ACTIONS_BEARER_TOKEN=choose-a-long-random-token
```

When that variable is set, `/actions/*` POST endpoints require:

```text
Authorization: Bearer choose-a-long-random-token
```

Configure the same bearer token in the Custom GPT Action authentication settings. The `/actions/openapi.json` schema remains readable so the GPT builder can import it. MCP at `/mcp`, local image-save endpoints, and manuscript path security behavior are unchanged.

## Durable macOS Setup With ngrok

Cloudflare Quick Tunnel URLs on `trycloudflare.com` are convenient for testing, but they are temporary. After sleep, restart, or a tunnel process restart, the public URL can change, which means the ChatGPT connector points at a dead endpoint.

Ngrok stable dev domains or reserved domains solve that problem by giving you a durable HTTPS base URL for the same local MCP server. The ChatGPT connector can stay pointed at:

```text
https://YOUR-NGROK-DOMAIN/mcp
```

Your Mac still needs to be awake and reachable while you actively use the connector. The LaunchAgents below restart the local server and ngrok agent after login or crashes, but they cannot serve requests while the Mac is fully asleep or offline.

Install and authenticate ngrok:

```bash
brew install ngrok
ngrok config add-authtoken YOUR_TOKEN
```

Find your assigned ngrok dev domain in the ngrok dashboard under Gateway > Domains. Ngrok documents pre-defined domains with this command shape:

```bash
ngrok http 8000 --url https://YOUR-NGROK-DOMAIN
```

Create local configuration:

```bash
cd /Users/jcbrooks/manuscript-workspace
cp .env.local.example .env.local
```

Edit `.env.local`:

```bash
MANUSCRIPT_ROOT=/Users/jcbrooks/flash-starwars
MANUSCRIPT_HOST=127.0.0.1
MANUSCRIPT_PORT=8000
MANUSCRIPT_PUBLIC_URL=https://YOUR-NGROK-DOMAIN
```

Run manually once to test:

```bash
scripts/run-server.sh
```

In another terminal:

```bash
scripts/run-ngrok.sh
```

Then check everything:

```bash
scripts/status.sh
```

Install the macOS user LaunchAgents:

```bash
scripts/install-macos-launchagents.sh
```

This creates:

```text
~/Library/LaunchAgents/com.jcbrooks.manuscript-workspace.server.plist
~/Library/LaunchAgents/com.jcbrooks.manuscript-workspace.ngrok.plist
```

Logs are written to:

```text
~/Library/Logs/manuscript-workspace/
```

Update the ChatGPT connector one final time to:

```text
https://YOUR-NGROK-DOMAIN/mcp
```

Check service status any time:

```bash
scripts/status.sh
```

Stop and uninstall the LaunchAgents:

```bash
scripts/uninstall-macos-launchagents.sh
```

## Saving Generated Images

Manuscript Workspace can import generated image files into the manuscript workspace without returning image bytes through MCP. The recommended workflow is:

1. Generate an image in ChatGPT.
2. Download the selected image to `~/Downloads`.
3. Ask Manuscript Workspace to list importable images.
4. Ask Manuscript Workspace to import the latest image or a specific filename.
5. The image is saved under `assets/images/`.
6. Metadata is stored in `assets/image-metadata.json`.

By default, importable images are only read from `~/Downloads`. You can configure additional explicit import roots in `manuscript.config.json`:

```json
{
  "asset_import_roots": [
    "~/Downloads",
    "/Users/jcbrooks/flash-starwars/imports"
  ],
  "asset_root": "assets",
  "image_asset_root": "assets/images"
}
```

Supported image types are `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, and `.svg`. Raster images are validated by file headers. SVG files are treated as text files only; the server does not execute or interpret SVG content.

Image metadata includes the original filename, saved filename, saved relative path, import time, source import folder, file size, SHA-256 hash, optional description, optional generation prompt, optional associated chapter, and tags.

Example prompts:

```text
Use Manuscript Workspace. List recently downloaded importable images. Do not edit anything.
```

```text
Use Manuscript Workspace. Import the latest downloaded image into assets/images/chapter-02/ as black-grass-style-test.png. Add the description: graphic novel style exploration for Chapter 2.
```

```text
Use Manuscript Workspace. List all workspace images for chapter-02.
```

## Saving ChatGPT Generated Images Directly

The MCP app cannot automatically receive generated image bytes from ChatGPT. MCP tools can receive text arguments and file paths, but the generated image blob displayed in the browser is not passed into the MCP server. The local browser extension solves that gap by saving the actual displayed image from the ChatGPT page to localhost.

Install the extension:

1. Keep Manuscript Workspace running locally on `http://127.0.0.1:8000`.
2. Open Chrome.
3. Go to `chrome://extensions`.
4. Enable Developer mode.
5. Click Load unpacked.
6. Select the `browser-extension/` folder from this repo.

Use it:

1. Generate an image in ChatGPT.
2. Click `Save to Workspace` on the generated image.
3. Enter an optional filename, chapter folder such as `chapter-02`, and description.
4. The image is posted to `http://127.0.0.1:8000/local/save-generated-image`.
5. The image appears under `assets/images/`.
6. Metadata appears in `assets/image-metadata.json`.

The local save endpoint only accepts localhost Host headers and is not intended to be used through the public ngrok MCP connector.

## Exporting A PDF Book

You can build a local PDF from the command line without using ChatGPT:

```bash
cd /Users/jcbrooks/manuscript-workspace
. .venv/bin/activate
manuscript-export-pdf \
  --root /Users/jcbrooks/flash-starwars \
  --output /Users/jcbrooks/flash-starwars/exports/flash-starwars.pdf
```

The exporter reads chapter files matched by `chapter_globs` in `manuscript.config.json`, renders Markdown-style headings, paragraphs, and simple bullets, and inserts image assets after their matching chapter. A chapter file such as `Chapter 04 - The Interval - Rough Draft.md` maps to images in:

```text
assets/images/chapter-04/
```

Useful options:

```bash
manuscript-export-pdf --root /path/to/book --include-reference creative-constitution.md
manuscript-export-pdf --root /path/to/book --include-general-images
manuscript-export-pdf --root /path/to/book --title "My Book Title"
```

SVG files are skipped during PDF export because the exporter embeds raster images only. They still remain available as workspace assets.

## Tools

Read tools:

- `get_project_overview`
- `list_documents`
- `read_document`
- `read_documents`
- `read_project_context`
- `search_documents`
- `get_document_history`

Write tools:

- `create_document`
- `append_document`
- `apply_patch`
- `write_document`
- `rename_document`
- `restore_document_version`
- `delete_document`, only when deletion is enabled

Read tools are annotated with `readOnlyHint: true`. Write tools are annotated as local-world tools, and full overwrite, restore, and delete tools carry destructive hints.

Tool names are simple by default for ChatGPT compatibility, so ChatGPT should display callable targets such as `Manuscript_Workspace.read_document`. Set `MANUSCRIPT_LEGACY_DOTTED_TOOLS=1` before starting the server only if you need the older MCP names such as `manuscript.read_document`.

Image MCP tools are opt-in because some ChatGPT connector sessions are more reliable with the smaller core discovery surface. Set `MANUSCRIPT_ENABLE_IMAGE_MCP_TOOLS=1` before starting the server to also expose:

- `list_importable_images`
- `list_workspace_images`
- `get_image_metadata`
- `import_image`
- `save_image_base64`

The browser extension's local "Save to Workspace" image endpoint does not require these optional MCP tools.

## Version History And Restore

Before every mutation, the previous file content is saved under:

```text
.manuscript-history/versions/<version-id>/
```

Each version records the relative path, timestamp, operation, old and new revisions when available, and change summary. Use `get_document_history` to inspect versions and `restore_document_version` to restore one. Restoration creates its own undo snapshot first.

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
