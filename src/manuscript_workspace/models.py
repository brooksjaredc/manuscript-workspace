"""Pydantic models shared by the manuscript workspace."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SUPPORTED_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}
APP_VERSION = "0.3.2"


class ManuscriptConfig(BaseModel):
    project_name: str = "Untitled Manuscript"
    reference_documents: list[str] = Field(default_factory=list)
    chapter_globs: list[str] = Field(default_factory=lambda: ["chapters/*.md", "chapter-*.md"])
    deletion_enabled: bool = False
    max_read_chars: int = 80_000
    max_combined_read_chars: int = 40_000
    max_write_chars: int = 500_000
    max_search_results: int = 100
    max_search_file_bytes: int = 1_000_000
    history_retention_versions: int | None = None
    asset_import_roots: list[str] = Field(default_factory=lambda: ["~/Downloads"])
    asset_root: str = "assets"
    image_asset_root: str = "assets/images"
    max_image_base64_bytes: int = 25 * 1024 * 1024


class DocumentMetadata(BaseModel):
    path: str
    size_bytes: int
    modified_at: str
    revision: str


class SearchMode(str):
    literal = "literal"
    regex = "regex"


SearchModeLiteral = Literal["literal", "regex"]
