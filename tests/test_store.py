from __future__ import annotations

import asyncio
import difflib
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from manuscript_workspace.errors import ManuscriptError
from manuscript_workspace.server import create_app, create_mcp
from manuscript_workspace.store import ManuscriptStore


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


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


def test_mcp_tool_discovery_and_annotations(manuscript_root: Path) -> None:
    tools = asyncio.run(create_mcp(ManuscriptStore(manuscript_root)).list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert "manuscript.read_document" in by_name
    assert "manuscript.apply_patch" in by_name
    assert "manuscript.delete_document" not in by_name
    assert by_name["manuscript.read_document"].annotations.readOnlyHint is True
    assert by_name["manuscript.read_document"].annotations.openWorldHint is False
    assert by_name["manuscript.apply_patch"].annotations.readOnlyHint is False
    assert by_name["manuscript.write_document"].annotations.destructiveHint is True


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
