from __future__ import annotations

import asyncio
import base64
import difflib
import json
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from manuscript_workspace.errors import ManuscriptError
from manuscript_workspace.export_pdf import chapter_slug, discover_chapters, export_pdf
from manuscript_workspace.server import create_app, create_mcp
from manuscript_workspace.store import ManuscriptStore


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
WEBP_HEADER = b"RIFF\x08\x00\x00\x00WEBPVP8 "


@pytest.fixture()
def manuscript_root(tmp_path: Path) -> Path:
    write(
        tmp_path / "manuscript.config.json",
        json.dumps(
            {
                "project_name": "Test Novel",
                "reference_documents": ["creative-constitution.md", "story-scratchbook.md"],
                "chapter_globs": ["chapters/*.md", "chapter-*.md"],
                "deletion_enabled": False,
                "max_read_chars": 120,
                "max_combined_read_chars": 240,
            }
        ),
    )
    write(tmp_path / "chatgpt-project-instructions.md", "Keep edits targeted.\n")
    write(tmp_path / "creative-constitution.md", "Rule one.\nRule two.\n")
    write(tmp_path / "story-scratchbook.md", "Idea A.\n")
    write(tmp_path / "characters.md", "Barry uses calibrated field harmonics.\n")
    write(tmp_path / "chapters" / "chapter-01.md", "Line 1\nLine 2\nLine 3\n")
    write(tmp_path / "chapter-02.md", "Barry reaches hyperspace carefully.\n")
    write(tmp_path / ".hidden.md", "hidden\n")
    write(tmp_path / "notes.bin", b"\x00\x01")
    write(tmp_path / "image.md", b"hello\x00world")
    return tmp_path


@pytest.fixture()
def image_manuscript_root(tmp_path: Path) -> Path:
    imports = tmp_path / "imports"
    imports.mkdir()
    write(imports / "older.png", PNG_1X1)
    write(imports / "latest.png", PNG_1X1)
    os.utime(imports / "older.png", (1_700_000_000, 1_700_000_000))
    os.utime(imports / "latest.png", (1_800_000_000, 1_800_000_000))
    write(
        tmp_path / "manuscript.config.json",
        json.dumps(
            {
                "project_name": "Image Test Novel",
                "asset_import_roots": [str(imports)],
                "asset_root": "assets",
                "image_asset_root": "assets/images",
                "max_image_base64_bytes": 1024,
            }
        ),
    )
    return tmp_path


def test_normal_and_recursive_listing(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    shallow = store.list_documents(recursive=False)["documents"]
    recursive = store.list_documents(recursive=True)["documents"]
    assert "creative-constitution.md" in shallow
    assert "chapters/chapter-01.md" not in shallow
    assert "chapters/chapter-01.md" in recursive
    assert ".hidden.md" not in recursive


def test_list_documents_metadata_and_category(manuscript_root: Path) -> None:
    docs = ManuscriptStore(manuscript_root).list_documents(category="chapter", metadata=True)["documents"]
    paths = {doc["path"] for doc in docs}
    assert paths == {"chapter-02.md", "chapters/chapter-01.md"}
    assert all("revision" in doc and "modified_at" in doc for doc in docs)


def test_line_range_read_and_multi_read(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    doc = store.read_document("chapters/chapter-01.md", start_line=2, end_line=3)
    assert doc["content"] == "Line 2\nLine 3\n"
    assert doc["total_line_count"] == 3
    result = store.read_documents(["creative-constitution.md", "story-scratchbook.md"])
    assert [item["path"] for item in result["documents"]] == ["creative-constitution.md", "story-scratchbook.md"]


def test_missing_document_returns_structured_error(manuscript_root: Path) -> None:
    with pytest.raises(ManuscriptError) as exc_info:
        ManuscriptStore(manuscript_root).read_document("Chapter 04 - The Interval - Rough Draft.md")
    assert exc_info.value.code == "document_not_found"


def test_multi_document_read_truncates_with_continuation(tmp_path: Path) -> None:
    write(tmp_path / "one.md", "a" * 30)
    write(tmp_path / "two.md", "b" * 30)
    write(tmp_path / "three.md", "c" * 30)
    store = ManuscriptStore(tmp_path)
    result = store.read_documents(["one.md", "two.md", "three.md"], max_combined_characters=50)
    assert result["truncated"] is True
    assert result["continuation_instruction"]
    assert result["documents"][1]["truncated"] is True
    assert "three.md" in result["unread_paths"]


def test_project_context_includes_project_instructions(manuscript_root: Path) -> None:
    result = ManuscriptStore(manuscript_root).read_project_context()
    assert [doc["path"] for doc in result["documents"]][:2] == ["chatgpt-project-instructions.md", "creative-constitution.md"]


def test_project_context_reports_truncation(manuscript_root: Path) -> None:
    write(manuscript_root / "creative-constitution.md", "\n".join(f"Rule {index}" for index in range(80)))
    result = ManuscriptStore(manuscript_root).read_project_context(max_combined_characters=80)
    truncated = [doc for doc in result["documents"] if doc.get("truncated")]
    assert truncated
    assert "line ranges" in truncated[0]["truncation_instruction"]


def test_literal_and_regex_search(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    literal = store.search_documents("hyperspace", paths_or_globs=["chapter-*.md"])
    regex = store.search_documents(r"Barry .* harmonics", mode="regex", paths_or_globs=["characters.md"])
    assert literal["results"][0]["path"] == "chapter-02.md"
    assert regex["results"][0]["line_number"] == 1


def test_pathological_regex_rejected(manuscript_root: Path) -> None:
    with pytest.raises(ManuscriptError, match="regex_rejected"):
        ManuscriptStore(manuscript_root).search_documents(r"(a+)+$", mode="regex")


@pytest.mark.parametrize("bad_path", ["../outside.md", "/tmp/outside.md"])
def test_path_traversal_and_absolute_rejection(manuscript_root: Path, bad_path: str) -> None:
    store = ManuscriptStore(manuscript_root)
    with pytest.raises(ManuscriptError):
        store.read_document(bad_path)


def test_symlink_escape_rejected(manuscript_root: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    write(outside, "outside\n")
    link = manuscript_root / "escape.md"
    link.symlink_to(outside)
    with pytest.raises(ManuscriptError, match="symlink_escape"):
        ManuscriptStore(manuscript_root).read_document("escape.md")


def test_unsupported_extension_and_binary_rejected(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    with pytest.raises(ManuscriptError, match="unsupported_extension"):
        store.read_document("notes.bin")
    with pytest.raises(ManuscriptError, match="binary_file_rejected"):
        store.read_document("image.md")


def test_file_size_limit_requires_range(manuscript_root: Path) -> None:
    write(manuscript_root / "large.md", "x" * 200)
    store = ManuscriptStore(manuscript_root)
    with pytest.raises(ManuscriptError, match="range_required"):
        store.read_document("large.md")
    ranged = store.read_document("large.md", start_line=1, end_line=1)
    assert ranged["truncated"] is True


def test_successful_patch_application_and_snapshot(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    before = store.read_document("chapters/chapter-01.md")
    old = before["content"]
    new = old.replace("Line 2\n", "Line two revised.\n")
    patch = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/chapters/chapter-01.md", tofile="b/chapters/chapter-01.md"))
    result = store.apply_patch("chapters/chapter-01.md", before["revision"], patch, change_summary="revise line")
    assert result["lines_added"] == 1
    assert result["lines_removed"] == 1
    assert store.read_document("chapters/chapter-01.md")["content"] == new
    history = store.get_document_history("chapters/chapter-01.md")["versions"]
    assert history[0]["version"] == result["snapshot_version"]
    assert history[0]["operation"] == "apply_patch"


def test_malformed_and_partially_applicable_patch_rejected(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    rev = store.read_document("chapter-02.md")["revision"]
    with pytest.raises(ManuscriptError, match="malformed_patch"):
        store.apply_patch("chapter-02.md", rev, "not a diff")
    bad_patch = "--- a/chapter-02.md\n+++ b/chapter-02.md\n@@ -1 +1 @@\n-Not current\n+New\n"
    with pytest.raises(ManuscriptError, match="patch_"):
        store.apply_patch("chapter-02.md", rev, bad_patch)
    assert store.read_document("chapter-02.md")["content"] == "Barry reaches hyperspace carefully.\n"


def test_stale_revision_rejected(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    rev = store.read_document("story-scratchbook.md")["revision"]
    write(manuscript_root / "story-scratchbook.md", "Manual edit.\n")
    with pytest.raises(ManuscriptError, match="stale_revision"):
        store.append_document("story-scratchbook.md", "Idea B.\n", rev)


def test_atomic_write_leaves_no_temp_and_appends(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    rev = store.read_document("story-scratchbook.md")["revision"]
    store.append_document("story-scratchbook.md", "Idea B.\n", rev)
    assert (manuscript_root / "story-scratchbook.md").read_text(encoding="utf-8").endswith("Idea B.\n")
    assert not list(manuscript_root.glob(".story-scratchbook.md.*.tmp"))


def test_version_restore(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    first = store.read_document("story-scratchbook.md")
    append = store.append_document("story-scratchbook.md", "Idea B.\n", first["revision"])
    current = store.read_document("story-scratchbook.md")
    restored = store.restore_document_version("story-scratchbook.md", append["snapshot_version"], current["revision"])
    assert restored["undo_snapshot_version"] is not None
    assert store.read_document("story-scratchbook.md")["content"] == "Idea A.\n"


def test_history_retention_configurable(tmp_path: Path) -> None:
    write(tmp_path / "manuscript.config.json", json.dumps({"history_retention_versions": 1}))
    write(tmp_path / "scratch.md", "one\n")
    store = ManuscriptStore(tmp_path)
    first = store.read_document("scratch.md")
    store.append_document("scratch.md", "two\n", first["revision"])
    second = store.read_document("scratch.md")
    store.append_document("scratch.md", "three\n", second["revision"])
    assert len(store.get_document_history("scratch.md")["versions"]) == 1


def test_rename_collision_and_delete_disabled(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    rev = store.read_document("chapter-02.md")["revision"]
    with pytest.raises(ManuscriptError, match="destination_exists"):
        store.rename_document("chapter-02.md", "creative-constitution.md", rev)
    with pytest.raises(ManuscriptError, match="deletion_disabled"):
        store.delete_document("chapter-02.md", rev)


def test_create_document_and_overwrite_guard(manuscript_root: Path) -> None:
    store = ManuscriptStore(manuscript_root)
    created = store.create_document("chapters/chapter-03.md", "# Chapter 3\n")
    assert created["revision"]
    with pytest.raises(ManuscriptError, match="document_exists"):
        store.create_document("chapters/chapter-03.md", "replacement")


def test_list_importable_images_from_configured_root(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    result = store.list_importable_images(max_results=10, modified_within_hours=1_000_000)
    names = [image["filename"] for image in result["images"]]
    assert names[:2] == ["latest.png", "older.png"]
    assert result["images"][0]["image_type"] == "png"
    assert result["images"][0]["dimensions"] == {"width": 1, "height": 1}


def test_default_import_root_uses_downloads_without_touching_real_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    downloads = home / "Downloads"
    write(downloads / "downloaded.png", PNG_1X1)
    manuscript = tmp_path / "book"
    manuscript.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result = ManuscriptStore(manuscript).list_importable_images(modified_within_hours=1_000_000)
    assert result["images"][0]["filename"] == "downloaded.png"


def test_import_latest_image_creates_metadata(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    result = store.import_image(
        latest=True,
        destination_relative_path="assets/images/chapter-02/black-grass-style-test.png",
        description="graphic novel style exploration for Chapter 2",
        associated_chapter="chapter-02",
        tags=["style-test", "black-grass"],
    )
    saved = image_manuscript_root / result["saved_relative_path"]
    assert saved.exists()
    assert result["source_filename"] == "latest.png"
    assert result["metadata"]["description"] == "graphic novel style exploration for Chapter 2"
    metadata = json.loads((image_manuscript_root / "assets" / "image-metadata.json").read_text(encoding="utf-8"))
    assert result["saved_relative_path"] in metadata["images"]


def test_import_specific_image_and_list_workspace_images(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    imported = store.import_image(
        source_relative_path="older.png",
        destination_relative_path="assets/images/chapter-01/older-copy.png",
        generation_prompt="a tiny test image",
    )
    workspace = store.list_workspace_images(folder_glob="assets/images/chapter-01/*")
    assert workspace["images"][0]["relative_path"] == imported["saved_relative_path"]
    assert workspace["images"][0]["metadata"]["generation_prompt"] == "a tiny test image"
    metadata = store.get_image_metadata(imported["saved_relative_path"])
    assert metadata["metadata"]["saved_filename"] == "older-copy.png"


def test_image_source_and_destination_traversal_rejected(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    with pytest.raises(ManuscriptError, match="path_traversal_rejected"):
        store.import_image(source_relative_path="../latest.png", destination_relative_path="assets/images/general/test.png")
    with pytest.raises(ManuscriptError, match="path_traversal_rejected"):
        store.import_image(source_relative_path="latest.png", destination_relative_path="assets/images/../bad.png")


def test_image_unsupported_extension_and_invalid_bytes_rejected(image_manuscript_root: Path) -> None:
    imports = image_manuscript_root / "imports"
    write(imports / "not-image.txt", "nope")
    write(imports / "bad.png", b"not a png")
    store = ManuscriptStore(image_manuscript_root)
    with pytest.raises(ManuscriptError, match="unsupported_image_extension"):
        store.import_image(source_relative_path="not-image.txt", destination_relative_path="assets/images/general/not-image.txt")
    with pytest.raises(ManuscriptError, match="invalid_image_bytes"):
        store.import_image(source_relative_path="bad.png", destination_relative_path="assets/images/general/bad.png")


def test_image_destination_collision_and_overwrite_snapshot(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    destination = "assets/images/general/collision.png"
    store.import_image(source_relative_path="older.png", destination_relative_path=destination)
    with pytest.raises(ManuscriptError, match="destination_exists"):
        store.import_image(source_relative_path="latest.png", destination_relative_path=destination)
    overwritten = store.import_image(source_relative_path="latest.png", destination_relative_path=destination, overwrite=True)
    assert overwritten["overwritten"] is True
    assert overwritten["snapshot_version"]
    history = store.get_document_history(destination)["versions"]
    assert history[0]["operation"] == "import_image_overwrite"


def test_metadata_updates_for_multiple_images(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    first = store.import_image(source_relative_path="older.png", destination_relative_path="assets/images/general/one.png")
    second = store.import_image(source_relative_path="latest.png", destination_relative_path="assets/images/general/two.png")
    metadata = json.loads((image_manuscript_root / "assets" / "image-metadata.json").read_text(encoding="utf-8"))
    assert set(metadata["images"]) == {first["saved_relative_path"], second["saved_relative_path"]}


def test_save_image_base64(image_manuscript_root: Path) -> None:
    store = ManuscriptStore(image_manuscript_root)
    result = store.save_image_base64(
        destination_relative_path="assets/images/general/base64.png",
        base64_image_data=base64.b64encode(PNG_1X1).decode("ascii"),
        declared_mime_type="image/png",
        description="saved from base64",
    )
    assert (image_manuscript_root / result["saved_relative_path"]).read_bytes() == PNG_1X1
    assert result["metadata"]["description"] == "saved from base64"


def test_save_image_base64_bare_filename_and_size_limit(tmp_path: Path) -> None:
    write(tmp_path / "manuscript.config.json", json.dumps({"max_image_base64_bytes": 4}))
    store = ManuscriptStore(tmp_path)
    with pytest.raises(ManuscriptError, match="image_too_large"):
        store.save_image_base64(
            destination_relative_path="too-big.png",
            base64_image_data=base64.b64encode(PNG_1X1).decode("ascii"),
            declared_mime_type="image/png",
        )
    write(tmp_path / "manuscript.config.json", json.dumps({"max_image_base64_bytes": 1024}))
    store = ManuscriptStore(tmp_path)
    saved = store.save_image_base64(
        destination_relative_path="bare.png",
        base64_image_data=base64.b64encode(PNG_1X1).decode("ascii"),
        declared_mime_type="image/png",
    )
    assert saved["saved_relative_path"] == "assets/images/general/bare.png"


def test_chapter_slug_for_spaced_chapter_filename() -> None:
    assert chapter_slug("Chapter 04 - The Interval - Rough Draft.md") == "chapter-04"
    assert chapter_slug("chapters/chapter-12.md") == "chapter-12"


def test_export_pdf_combines_chapters_and_images(tmp_path: Path) -> None:
    write(
        tmp_path / "manuscript.config.json",
        json.dumps(
            {
                "project_name": "PDF Test Novel",
                "chapter_globs": ["Chapter *.md"],
                "asset_root": "assets",
                "image_asset_root": "assets/images",
            }
        ),
    )
    write(tmp_path / "Chapter 01 - Opening.md", "# Chapter 1\n\nA first paragraph.\n")
    write(tmp_path / "Chapter 02 - Image Chapter.md", "# Chapter 2\n\nA second paragraph with *emphasis*.\n")
    write(tmp_path / "assets" / "images" / "chapter-02" / "style-test.png", PNG_1X1)
    write(
        tmp_path / "assets" / "image-metadata.json",
        json.dumps(
            {
                "images": {
                    "assets/images/chapter-02/style-test.png": {
                        "description": "A tiny style test image."
                    }
                }
            }
        ),
    )
    store = ManuscriptStore(tmp_path)
    chapters = discover_chapters(store)
    assert [chapter.image_folder_slug for chapter in chapters] == ["chapter-01", "chapter-02"]
    output = export_pdf(store, tmp_path / "exports" / "book.pdf")
    assert output.exists()
    assert output.read_bytes().startswith(b"%PDF")
    assert output.stat().st_size > 1000


def test_mcp_tool_discovery_and_annotations(manuscript_root: Path) -> None:
    tools = asyncio.run(create_mcp(ManuscriptStore(manuscript_root)).list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert len(by_name) == 13
    assert "manuscript.read_document" in by_name
    assert "manuscript.apply_patch" in by_name
    assert "manuscript.list_importable_images" not in by_name
    assert "manuscript.import_image" not in by_name
    assert "manuscript.save_image_base64" not in by_name
    assert "manuscript.delete_document" not in by_name
    assert by_name["manuscript.read_document"].annotations.readOnlyHint is True
    assert by_name["manuscript.read_document"].annotations.openWorldHint is False
    assert by_name["manuscript.apply_patch"].annotations.readOnlyHint is False
    assert by_name["manuscript.write_document"].annotations.destructiveHint is True


def test_mcp_image_tools_are_opt_in(monkeypatch: pytest.MonkeyPatch, manuscript_root: Path) -> None:
    monkeypatch.setenv("MANUSCRIPT_ENABLE_IMAGE_MCP_TOOLS", "1")
    tools = asyncio.run(create_mcp(ManuscriptStore(manuscript_root)).list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert len(by_name) == 18
    assert "manuscript.list_importable_images" in by_name
    assert "manuscript.list_workspace_images" in by_name
    assert "manuscript.get_image_metadata" in by_name
    assert "manuscript.import_image" in by_name
    assert "manuscript.save_image_base64" in by_name
    assert by_name["manuscript.list_importable_images"].annotations.readOnlyHint is True
    assert by_name["manuscript.import_image"].annotations.readOnlyHint is False


def test_mcp_initialize_post_uses_started_lifespan(manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(manuscript_root))
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.1.0"},
        },
    }

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/mcp", json=payload, headers={"Accept": "application/json, text/event-stream"})
        health = client.get("/health")

    assert response.status_code != 500
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert body["result"]["serverInfo"]["name"] == "Manuscript Workspace"
    assert "tools" in body["result"]["capabilities"]
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_mcp_initialize_accepts_tunnel_host_header(manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(manuscript_root))
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "manual-test", "version": "0.1.0"},
        },
    }

    with TestClient(app, base_url="https://example.trycloudflare.com") as client:
        response = client.post(
            "/mcp",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "example.trycloudflare.com",
            },
        )

    assert response.status_code != 421
    assert response.text != "Invalid Host header"
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Manuscript Workspace"


def test_local_status_rejects_non_local_host(manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(manuscript_root))
    with TestClient(app, base_url="https://example.ngrok-free.dev") as client:
        response = client.get("/local/status")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "local_host_required"


def test_local_save_endpoint_saves_valid_png_upload(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("browser-original.png", PNG_1X1, "image/png")},
            data={"filename": "saved.png", "description": "from browser", "prompt": "tiny image", "tags": "chatgpt, test"},
            headers={"Origin": "https://chatgpt.com"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["relative_path"] == "assets/images/saved.png"
    assert (image_manuscript_root / body["relative_path"]).read_bytes() == PNG_1X1
    metadata = json.loads((image_manuscript_root / "assets" / "image-metadata.json").read_text(encoding="utf-8"))
    entry = metadata["images"][body["relative_path"]]
    assert entry["source"] == "chatgpt-browser-extension"
    assert entry["original_filename"] == "browser-original.png"
    assert entry["mime_type"] == "image/png"
    assert entry["width"] == 1
    assert entry["height"] == 1
    assert entry["prompt"] == "tiny image"
    assert entry["tags"] == ["chatgpt", "test"]


def test_local_save_endpoint_saves_valid_webp_upload(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("generated.webp", WEBP_HEADER, "image/webp")},
            data={"chapter": "chapter-02"},
            headers={"Origin": "https://chatgpt.com"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"].startswith("assets/images/chapter-02/")
    assert body["relative_path"].endswith(".webp")


def test_local_save_endpoint_rejects_unsupported_extension(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("generated.png", PNG_1X1, "image/png")},
            data={"filename": "bad.txt"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_image_extension"


def test_local_save_endpoint_rejects_invalid_image_bytes(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("bad.png", b"not a png", "image/png")},
            data={"filename": "bad.png"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_image_bytes"


def test_local_save_endpoint_rejects_path_traversal_filename(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("generated.png", PNG_1X1, "image/png")},
            data={"filename": "../bad.png"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_traversal_rejected"


def test_local_save_endpoint_saves_into_chapter_subfolder(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("chapter.png", PNG_1X1, "image/png")},
            data={"filename": "style-test.png", "chapter": "chapter-04"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"] == "assets/images/chapter-04/style-test.png"
    metadata = json.loads((image_manuscript_root / "assets" / "image-metadata.json").read_text(encoding="utf-8"))
    assert metadata["images"][body["relative_path"]]["chapter"] == "chapter-04"


def test_local_save_endpoint_rejects_non_local_host(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="https://example.ngrok-free.dev") as client:
        response = client.post(
            "/local/save-generated-image",
            files={"image": ("generated.png", PNG_1X1, "image/png")},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "local_host_required"


def test_local_extension_cors_preflight(image_manuscript_root: Path) -> None:
    app = create_app(ManuscriptStore(image_manuscript_root))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.options(
            "/local/save-generated-image",
            headers={
                "Origin": "https://chatgpt.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        rejected = client.options(
            "/local/save-generated-image",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://chatgpt.com"
    assert rejected.status_code == 400
