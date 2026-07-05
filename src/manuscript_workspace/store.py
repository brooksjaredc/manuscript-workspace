"""Local manuscript document management with strict path safety."""

from __future__ import annotations

import base64
import binascii
import difflib
import filecmp
import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from pydantic import ValidationError

from manuscript_workspace.errors import ManuscriptError
from manuscript_workspace.models import APP_VERSION, DocumentMetadata, ManuscriptConfig, SUPPORTED_EXTENSIONS

LOGGER = logging.getLogger(__name__)

EXCLUDED_DIRS = {".git", ".manuscript-history", "node_modules", "__pycache__"}
VENV_DIR_NAMES = {".venv", "venv", "env", ".env"}
TEMP_SUFFIXES = {".swp", ".swo", ".tmp", ".temp"}
PROJECT_INSTRUCTIONS = "chatgpt-project-instructions.md"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
IMAGE_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
DIRECT_UPLOAD_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    relative: str
    path: Path


@dataclass(frozen=True, slots=True)
class ImportSource:
    relative: str
    path: Path
    root: Path


class ManuscriptStore:
    """Safe local document store rooted at a manuscript directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            raise ManuscriptError("root_not_found", "MANUSCRIPT_ROOT does not exist.", {"root": str(self.root)})
        if not self.root.is_dir():
            raise ManuscriptError("root_not_directory", "MANUSCRIPT_ROOT must be a directory.", {"root": str(self.root)})
        self.history_dir = self.root / ".manuscript-history"
        self.config = self._load_config()

    def _load_config(self) -> ManuscriptConfig:
        config_path = self.root / "manuscript.config.json"
        if not config_path.exists():
            return ManuscriptConfig()
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            return ManuscriptConfig.model_validate(raw)
        except (json.JSONDecodeError, OSError, ValidationError) as exc:
            raise ManuscriptError("invalid_config", "manuscript.config.json could not be loaded.", {"reason": str(exc)}) from exc

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": APP_VERSION,
            "project_name": self.config.project_name,
            "root_name": self.root.name,
            "deletion_enabled": self.config.deletion_enabled,
        }

    def _is_under_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    def _normalize_relative(self, raw_path: str) -> str:
        if not raw_path or raw_path.strip() == "":
            raise ManuscriptError("invalid_path", "Path must not be empty.")
        candidate = Path(raw_path)
        if candidate.is_absolute():
            raise ManuscriptError("absolute_path_rejected", "Tool paths must be relative to MANUSCRIPT_ROOT.")
        parts = candidate.parts
        if ".." in parts:
            raise ManuscriptError("path_traversal_rejected", "Path traversal using '..' is not allowed.")
        normalized = Path(*[part for part in parts if part not in ("", ".")]).as_posix()
        if normalized in ("", "."):
            raise ManuscriptError("invalid_path", "Path must identify a document.")
        return normalized

    def resolve_document_path(self, raw_path: str, *, must_exist: bool = True) -> ResolvedPath:
        relative = self._normalize_relative(raw_path)
        candidate = self.root / relative
        resolved_parent = candidate.parent.resolve(strict=True) if candidate.parent.exists() else candidate.parent.resolve(strict=False)
        if not self._is_under_root(resolved_parent):
            raise ManuscriptError("path_escape_rejected", "Resolved path escapes MANUSCRIPT_ROOT.", {"path": relative})
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise ManuscriptError("document_not_found", "Document does not exist under MANUSCRIPT_ROOT.", {"path": relative}) from exc
        if must_exist:
            if not self._is_under_root(resolved):
                raise ManuscriptError("symlink_escape_rejected", "Symlink resolves outside MANUSCRIPT_ROOT.", {"path": relative})
            if not resolved.is_file():
                raise ManuscriptError("not_a_file", "Path is not a file.", {"path": relative})
            self._ensure_supported_file(resolved, relative)
        else:
            if not self._is_under_root(resolved_parent):
                raise ManuscriptError("path_escape_rejected", "Destination escapes MANUSCRIPT_ROOT.", {"path": relative})
            self._ensure_supported_extension(Path(relative), relative)
        return ResolvedPath(relative=relative, path=resolved if must_exist else candidate)

    def _ensure_supported_extension(self, path: Path, relative: str) -> None:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ManuscriptError("unsupported_extension", "Only .md, .txt, .json, .yaml, and .yml files are editable.", {"path": relative})

    def _ensure_supported_file(self, path: Path, relative: str) -> None:
        self._ensure_supported_extension(path, relative)
        if self._is_binary(path):
            raise ManuscriptError("binary_file_rejected", "Binary files are not supported.", {"path": relative})

    def _is_excluded_path(self, path: Path) -> bool:
        rel_parts = path.relative_to(self.root).parts
        for part in rel_parts:
            if part in EXCLUDED_DIRS or part in VENV_DIR_NAMES:
                return True
            if part.startswith(".") and part not in {PROJECT_INSTRUCTIONS}:
                return True
        name = path.name
        return name.startswith(".") or name.endswith("~") or path.suffix in TEMP_SUFFIXES

    @staticmethod
    def _is_binary(path: Path) -> bool:
        try:
            chunk = path.read_bytes()[:4096]
        except OSError:
            return True
        return b"\x00" in chunk

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def revision_for_path(self, path: Path) -> str:
        try:
            return self._sha256_bytes(path.read_bytes())
        except OSError as exc:
            raise ManuscriptError("read_failed", "Could not read document.", {"path": self._display_path(path)}) from exc

    def _sha256_for_bytes(self, data: bytes) -> str:
        return self._sha256_bytes(data)

    def _display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def _metadata_for(self, path: Path) -> DocumentMetadata:
        stat = path.stat()
        return DocumentMetadata(
            path=path.relative_to(self.root).as_posix(),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            revision=self.revision_for_path(path),
        )

    def iter_documents(self, *, recursive: bool = True) -> Iterable[Path]:
        iterator = self.root.rglob("*") if recursive else self.root.glob("*")
        for path in sorted(iterator, key=lambda item: item.relative_to(self.root).as_posix()):
            if not path.is_file() or self._is_excluded_path(path):
                continue
            rel = path.relative_to(self.root).as_posix()
            if path.suffix.lower() in SUPPORTED_EXTENSIONS and not self._is_binary(path):
                yield path

    def list_documents(
        self,
        *,
        glob: str | None = None,
        category: str | None = None,
        recursive: bool = True,
        metadata: bool = False,
    ) -> dict[str, Any]:
        paths = list(self.iter_documents(recursive=recursive))
        if category:
            paths = self._filter_category(paths, category)
        if glob:
            pattern = self._normalize_glob(glob)
            paths = [path for path in paths if fnmatch.fnmatch(path.relative_to(self.root).as_posix(), pattern)]
        if metadata:
            return {"documents": [self._metadata_for(path).model_dump() for path in paths]}
        return {"documents": [path.relative_to(self.root).as_posix() for path in paths]}

    def _normalize_glob(self, pattern: str) -> str:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ManuscriptError("invalid_glob", "Glob must be relative and must not contain '..'.")
        return pattern

    def _filter_category(self, paths: list[Path], category: str) -> list[Path]:
        if category == "reference":
            wanted = set(self.config.reference_documents)
            if (self.root / PROJECT_INSTRUCTIONS).exists():
                wanted.add(PROJECT_INSTRUCTIONS)
            return [path for path in paths if path.relative_to(self.root).as_posix() in wanted]
        if category == "chapter":
            return [path for path in paths if self._is_chapter(path)]
        if category == "other":
            refs = {self.root / doc for doc in self.config.reference_documents}
            return [path for path in paths if path not in refs and not self._is_chapter(path)]
        raise ManuscriptError("invalid_category", "Category must be reference, chapter, or other.", {"category": category})

    def _is_chapter(self, path: Path) -> bool:
        rel = path.relative_to(self.root).as_posix()
        return any(fnmatch.fnmatch(rel, pattern) for pattern in self.config.chapter_globs)

    def get_project_overview(self) -> dict[str, Any]:
        all_docs = list(self.iter_documents(recursive=True))
        refs = []
        for rel in self.config.reference_documents:
            try:
                resolved = self.resolve_document_path(rel)
                refs.append(self._metadata_for(resolved.path).model_dump())
            except ManuscriptError:
                refs.append({"path": rel, "missing": True})
        chapters = [self._metadata_for(path).model_dump() for path in all_docs if self._is_chapter(path)]
        known = {item.get("path") for item in refs if isinstance(item, dict)} | {item["path"] for item in chapters}
        others = [self._metadata_for(path).model_dump() for path in all_docs if path.relative_to(self.root).as_posix() not in known]
        return {
            "project_name": self.config.project_name,
            "manuscript_root": self.root.name,
            "reference_documents": refs,
            "chapter_documents": chapters,
            "other_supported_documents": others,
            "deletion_enabled": self.config.deletion_enabled,
        }

    def read_document(
        self,
        relative_path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        max_characters: int | None = None,
    ) -> dict[str, Any]:
        resolved = self.resolve_document_path(relative_path)
        size = resolved.path.stat().st_size
        limit = min(max_characters or self.config.max_read_chars, self.config.max_read_chars)
        if size > self.config.max_read_chars and start_line is None and end_line is None:
            raise ManuscriptError(
                "range_required",
                "Document exceeds the read size limit; request a line range.",
                {"path": resolved.relative, "size_bytes": size, "max_read_chars": self.config.max_read_chars},
            )
        lines = self._read_text_lines(resolved.path)
        total = len(lines)
        start = start_line or 1
        end = end_line or total
        if start < 1 or end < start:
            raise ManuscriptError("invalid_line_range", "Line range must be 1-based and ending line must be after starting line.")
        selected = lines[start - 1 : end]
        content = "".join(selected)
        truncated = False
        if len(content) > limit:
            content = content[:limit]
            truncated = True
        return {
            "path": resolved.relative,
            "content": content,
            "start_line": start,
            "end_line": min(end, total),
            "total_line_count": total,
            "revision": self.revision_for_path(resolved.path),
            "truncated": truncated,
        }

    @staticmethod
    def _read_text_lines(path: Path) -> list[str]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return handle.readlines()
        except UnicodeDecodeError as exc:
            raise ManuscriptError("utf8_required", "Document must be valid UTF-8.", {"path": path.name}) from exc
        except OSError as exc:
            raise ManuscriptError("read_failed", "Could not read document.", {"path": path.name}) from exc

    def read_documents(self, paths: list[str], *, max_combined_characters: int | None = None) -> dict[str, Any]:
        limit = min(max_combined_characters or self.config.max_combined_read_chars, self.config.max_combined_read_chars)
        used = 0
        documents = []
        unread_paths = []
        for path in paths:
            remaining = limit - used
            if remaining <= 0:
                unread_paths.append(path)
                continue
            doc = self.read_document(path, max_characters=remaining)
            used += len(doc["content"])
            if doc.get("truncated"):
                doc["truncation_instruction"] = "This document hit the combined read limit; reread it with read_document and a line range."
            documents.append(doc)
            if used >= limit:
                unread_paths.extend(paths[len(documents) :])
                break
        truncated = bool(unread_paths) or any(doc.get("truncated") for doc in documents)
        return {
            "documents": documents,
            "combined_characters": used,
            "truncated": truncated,
            "unread_paths": unread_paths,
            "continuation_instruction": (
                "Read remaining or truncated documents one at a time with read_document and explicit line ranges."
                if truncated
                else None
            ),
        }

    def read_project_context(self, *, max_combined_characters: int | None = None) -> dict[str, Any]:
        paths = list(self.config.reference_documents)
        if (self.root / PROJECT_INSTRUCTIONS).exists() and PROJECT_INSTRUCTIONS not in paths:
            paths.insert(0, PROJECT_INSTRUCTIONS)
        limit = min(max_combined_characters or self.config.max_combined_read_chars, self.config.max_combined_read_chars)
        used = 0
        documents: list[dict[str, Any]] = []
        for path in paths:
            remaining = limit - used
            if remaining <= 0:
                documents.append(
                    {
                        "path": path,
                        "truncated": True,
                        "content": "",
                        "truncation_instruction": "Combined context limit reached; read this document directly with read_document and a line range.",
                    }
                )
                continue
            try:
                doc = self.read_document(path, start_line=1, end_line=1_000_000_000, max_characters=remaining)
                used += len(doc["content"])
                if doc["truncated"]:
                    doc["truncation_instruction"] = (
                        "This project-context result hit a character limit; call read_document with line ranges to continue."
                    )
                documents.append(doc)
            except ManuscriptError as exc:
                documents.append({"path": path, **exc.to_dict()})
        return {"documents": documents, "combined_characters": used, "truncated": any(doc.get("truncated") for doc in documents)}

    def search_documents(
        self,
        query: str,
        *,
        paths_or_globs: list[str] | None = None,
        mode: str = "literal",
        case_sensitive: bool = False,
        max_results: int | None = None,
        context_lines: int = 1,
    ) -> dict[str, Any]:
        if not query:
            raise ManuscriptError("empty_query", "Search query must not be empty.")
        max_hits = min(max_results or self.config.max_search_results, self.config.max_search_results)
        if context_lines < 0 or context_lines > 10:
            raise ManuscriptError("invalid_context_lines", "Context lines must be between 0 and 10.")
        docs = self._search_scope(paths_or_globs)
        flags = 0 if case_sensitive else re.IGNORECASE
        if mode == "regex":
            if len(query) > 500:
                raise ManuscriptError("regex_too_large", "Regular expression is too long.")
            if self._looks_pathological_regex(query):
                raise ManuscriptError("regex_rejected", "Regular expression uses nested quantifiers that are unsafe for local search.")
            try:
                regex = re.compile(query, flags)
            except re.error as exc:
                raise ManuscriptError("invalid_regex", "Regular expression could not be compiled.", {"reason": str(exc)}) from exc
        elif mode != "literal":
            raise ManuscriptError("invalid_search_mode", "Search mode must be literal or regex.")
        needle = query if case_sensitive else query.lower()
        results = []
        started = time.monotonic()
        for path in docs:
            if path.stat().st_size > self.config.max_search_file_bytes:
                continue
            lines = self._read_text_lines(path)
            revision = self.revision_for_path(path)
            for index, line in enumerate(lines):
                if time.monotonic() - started > 2.0:
                    raise ManuscriptError("search_timeout", "Search exceeded the time limit; narrow the paths or query.")
                haystack = line if case_sensitive else line.lower()
                matched = regex.search(line) is not None if mode == "regex" else needle in haystack
                if not matched:
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                results.append(
                    {
                        "path": path.relative_to(self.root).as_posix(),
                        "line_number": index + 1,
                        "context_start_line": start + 1,
                        "context": "".join(lines[start:end]),
                        "revision": revision,
                    }
                )
                if len(results) >= max_hits:
                    return {"results": results, "truncated": True}
        return {"results": results, "truncated": False}

    def list_importable_images(
        self,
        *,
        import_root_selector: str | None = None,
        max_results: int = 20,
        modified_within_hours: int = 72,
    ) -> dict[str, Any]:
        roots = self._selected_import_roots(import_root_selector)
        cutoff = time.time() - (modified_within_hours * 3600)
        results: list[dict[str, Any]] = []
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.name.startswith("."):
                    continue
                resolved = path.resolve(strict=True)
                if not self._is_under(resolved, root):
                    continue
                stat = resolved.stat()
                if stat.st_mtime < cutoff:
                    continue
                try:
                    image_info = self._validate_image_file(resolved, display_path=path.name)
                except ManuscriptError:
                    continue
                results.append(
                    {
                        "filename": resolved.name,
                        "import_root": root.name,
                        "import_root_path": str(root),
                        "relative_path": resolved.relative_to(root).as_posix(),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                        "image_type": image_info["image_type"],
                        "sha256": self.revision_for_path(resolved),
                        "dimensions": image_info.get("dimensions"),
                    }
                )
        results.sort(key=lambda item: item["modified_at"], reverse=True)
        return {"images": results[: max(max_results, 0)], "truncated": len(results) > max(max_results, 0)}

    def list_workspace_images(self, *, folder_glob: str | None = None, max_results: int = 100) -> dict[str, Any]:
        if folder_glob and (Path(folder_glob).is_absolute() or ".." in Path(folder_glob).parts):
            raise ManuscriptError("invalid_glob", "Image glob must be relative and must not contain '..'.")
        metadata = self._read_image_metadata()
        root = self._image_asset_root_path()
        images: list[dict[str, Any]] = []
        if root.exists():
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                    continue
                resolved = path.resolve(strict=True)
                if not self._is_under_root(resolved):
                    continue
                rel = path.relative_to(self.root).as_posix()
                if folder_glob and not fnmatch.fnmatch(rel, folder_glob):
                    continue
                try:
                    self._validate_image_file(resolved, display_path=rel)
                except ManuscriptError:
                    continue
                stat = resolved.stat()
                images.append(
                    {
                        "relative_path": rel,
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                        "sha256": self.revision_for_path(resolved),
                        "metadata": metadata.get("images", {}).get(rel),
                    }
                )
        return {"images": images[: max(max_results, 0)], "truncated": len(images) > max(max_results, 0)}

    def get_image_metadata(self, relative_path: str) -> dict[str, Any]:
        resolved = self._resolve_workspace_image_destination(relative_path, must_exist=True)
        metadata = self._read_image_metadata().get("images", {}).get(resolved.relative)
        if metadata is None:
            return {"relative_path": resolved.relative, "metadata": None}
        return {"relative_path": resolved.relative, "metadata": metadata}

    def import_image(
        self,
        *,
        source_relative_path: str | None = None,
        latest: bool = False,
        source_import_root: str | None = None,
        destination_relative_path: str,
        description: str | None = None,
        generation_prompt: str | None = None,
        associated_chapter: str | None = None,
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        if latest == (source_relative_path is not None):
            raise ManuscriptError("invalid_image_source", "Provide either latest=true or a source import relative path, but not both.")
        source = self._latest_importable_image(source_import_root) if latest else self._resolve_import_source(source_relative_path or "", source_import_root)
        destination = self._resolve_workspace_image_destination(destination_relative_path, must_exist=False)
        if destination.path.exists() and not overwrite:
            raise ManuscriptError("destination_exists", "Destination image already exists; pass overwrite=true only for intentional replacement.", {"path": destination.relative})
        image_info = self._validate_image_file(source.path, display_path=source.relative)
        self._ensure_matching_image_extension(destination.path, image_info["image_type"], destination.relative)
        snapshot_version = None
        if destination.path.exists():
            snapshot_version = self._snapshot(destination.relative, destination.path, "import_image_overwrite", description, old_revision=self.revision_for_path(destination.path))
        data = source.path.read_bytes()
        self._atomic_write_bytes(destination.path, data)
        metadata_entry = self._upsert_image_metadata(
            source_path=source.path,
            source_relative_path=source.relative,
            source_import_root=source.root,
            destination_relative_path=destination.relative,
            image_type=image_info["image_type"],
            dimensions=image_info.get("dimensions"),
            file_size=len(data),
            sha256=self._sha256_for_bytes(data),
            description=description,
            generation_prompt=generation_prompt,
            associated_chapter=associated_chapter,
            tags=tags,
            overwrite=overwrite,
        )
        if snapshot_version:
            self._update_history_new_revision(snapshot_version, self.revision_for_path(destination.path))
        return {
            "saved_relative_path": destination.relative,
            "source_filename": source.path.name,
            "file_size": len(data),
            "sha256": metadata_entry["sha256"],
            "metadata": metadata_entry,
            "overwritten": overwrite and snapshot_version is not None,
            "snapshot_version": snapshot_version,
        }

    def save_image_base64(
        self,
        *,
        destination_relative_path: str,
        base64_image_data: str,
        declared_mime_type: str,
        description: str | None = None,
        generation_prompt: str | None = None,
        associated_chapter: str | None = None,
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        if declared_mime_type not in IMAGE_MIME_EXTENSIONS:
            raise ManuscriptError("unsupported_mime_type", "Declared MIME type is not a supported image type.", {"mime_type": declared_mime_type})
        estimated_size = (len(base64_image_data) * 3) // 4
        if estimated_size > self.config.max_image_base64_bytes:
            raise ManuscriptError("image_too_large", "Base64 image exceeds configured maximum size.", {"max_image_base64_bytes": self.config.max_image_base64_bytes})
        try:
            data = base64.b64decode(base64_image_data, validate=True)
        except binascii.Error as exc:
            raise ManuscriptError("invalid_base64", "Image data is not valid base64.") from exc
        if len(data) > self.config.max_image_base64_bytes:
            raise ManuscriptError("image_too_large", "Decoded image exceeds configured maximum size.", {"max_image_base64_bytes": self.config.max_image_base64_bytes})
        destination = self._resolve_workspace_image_destination(destination_relative_path, must_exist=False, allow_bare_filename=True)
        if destination.path.exists() and not overwrite:
            raise ManuscriptError("destination_exists", "Destination image already exists; pass overwrite=true only for intentional replacement.", {"path": destination.relative})
        image_info = self._validate_image_bytes(data, destination.path.suffix.lower(), destination.relative)
        expected_ext = IMAGE_MIME_EXTENSIONS[declared_mime_type]
        if destination.path.suffix.lower() not in ({".jpg", ".jpeg"} if expected_ext == ".jpg" else {expected_ext}):
            raise ManuscriptError("mime_extension_mismatch", "Destination extension does not match declared MIME type.", {"path": destination.relative})
        snapshot_version = None
        if destination.path.exists():
            snapshot_version = self._snapshot(destination.relative, destination.path, "save_image_base64_overwrite", description, old_revision=self.revision_for_path(destination.path))
        self._atomic_write_bytes(destination.path, data)
        metadata_entry = self._upsert_image_metadata(
            source_path=None,
            source_relative_path=None,
            source_import_root=None,
            destination_relative_path=destination.relative,
            image_type=image_info["image_type"],
            dimensions=image_info.get("dimensions"),
            file_size=len(data),
            sha256=self._sha256_for_bytes(data),
            description=description,
            generation_prompt=generation_prompt,
            associated_chapter=associated_chapter,
            tags=tags,
            overwrite=overwrite,
            declared_mime_type=declared_mime_type,
        )
        if snapshot_version:
            self._update_history_new_revision(snapshot_version, self.revision_for_path(destination.path))
        return {
            "saved_relative_path": destination.relative,
            "file_size": len(data),
            "sha256": metadata_entry["sha256"],
            "metadata": metadata_entry,
            "overwritten": overwrite and snapshot_version is not None,
            "snapshot_version": snapshot_version,
        }

    def save_generated_image_upload(
        self,
        *,
        image_bytes: bytes,
        content_type: str | None,
        browser_filename: str | None,
        filename: str | None,
        chapter: str | None,
        description: str | None,
        generation_prompt: str | None,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        if len(image_bytes) > self.config.max_image_base64_bytes:
            raise ManuscriptError("image_too_large", "Uploaded image exceeds configured maximum size.", {"max_image_base64_bytes": self.config.max_image_base64_bytes})
        declared_mime_type = (content_type or "").split(";", 1)[0].strip().lower()
        if declared_mime_type not in DIRECT_UPLOAD_MIME_EXTENSIONS:
            raise ManuscriptError("unsupported_mime_type", "Uploaded image MIME type is not supported.", {"mime_type": content_type})
        final_filename = self._safe_upload_filename(filename or browser_filename, declared_mime_type)
        folder = self._safe_chapter_folder(chapter) if chapter else None
        relative = f"{self.config.image_asset_root.rstrip('/')}/{folder + '/' if folder else ''}{final_filename}"
        destination = self._resolve_workspace_image_destination(relative, must_exist=False)
        if destination.path.exists():
            raise ManuscriptError("destination_exists", "Uploaded image destination already exists.", {"path": destination.relative})
        image_info = self._validate_image_bytes(image_bytes, destination.path.suffix.lower(), destination.relative)
        expected_ext = DIRECT_UPLOAD_MIME_EXTENSIONS[declared_mime_type]
        if destination.path.suffix.lower() not in ({".jpg", ".jpeg"} if expected_ext == ".jpg" else {expected_ext}):
            raise ManuscriptError("mime_extension_mismatch", "Destination extension does not match uploaded MIME type.", {"path": destination.relative})
        self._atomic_write_bytes(destination.path, image_bytes)
        metadata_entry = self._upsert_image_metadata(
            source_path=None,
            source_relative_path=None,
            source_import_root=None,
            destination_relative_path=destination.relative,
            image_type=image_info["image_type"],
            dimensions=image_info.get("dimensions"),
            file_size=len(image_bytes),
            sha256=self._sha256_for_bytes(image_bytes),
            description=description,
            generation_prompt=generation_prompt,
            associated_chapter=chapter,
            tags=tags,
            overwrite=False,
            declared_mime_type=declared_mime_type,
            source="chatgpt-browser-extension",
            original_filename=browser_filename,
        )
        return {
            "ok": True,
            "relative_path": destination.relative,
            "sha256": metadata_entry["sha256"],
            "size_bytes": len(image_bytes),
            "metadata": metadata_entry,
        }

    def _safe_upload_filename(self, raw_filename: str | None, mime_type: str) -> str:
        suffix = DIRECT_UPLOAD_MIME_EXTENSIONS[mime_type]
        if raw_filename:
            basename = Path(raw_filename).name
            if basename != raw_filename or ".." in Path(raw_filename).parts:
                raise ManuscriptError("path_traversal_rejected", "Uploaded filename must not contain path separators or traversal.")
            stem = Path(basename).stem
            supplied_suffix = Path(basename).suffix.lower()
            if supplied_suffix and supplied_suffix not in SUPPORTED_IMAGE_EXTENSIONS:
                raise ManuscriptError("unsupported_image_extension", "Uploaded filename has an unsupported image extension.", {"filename": raw_filename})
            if supplied_suffix and supplied_suffix in {".svg"}:
                raise ManuscriptError("unsupported_image_extension", "Direct browser uploads support png, jpg, jpeg, webp, and gif.", {"filename": raw_filename})
            if supplied_suffix:
                suffix = supplied_suffix
        else:
            stem = "chatgpt-image-" + datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")
        if not stem:
            stem = "chatgpt-image-" + datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
        return f"{stem}{suffix}"

    @staticmethod
    def _safe_chapter_folder(raw_chapter: str) -> str:
        if Path(raw_chapter).is_absolute() or ".." in Path(raw_chapter).parts:
            raise ManuscriptError("path_traversal_rejected", "Chapter folder must be a single safe relative folder name.")
        chapter = raw_chapter.strip().strip("/")
        if "/" in chapter or "\\" in chapter:
            raise ManuscriptError("invalid_chapter_folder", "Chapter folder must be a single folder name, such as chapter-02.")
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", chapter).strip(".-_")
        if not sanitized:
            raise ManuscriptError("invalid_chapter_folder", "Chapter folder must not be empty.")
        return sanitized

    def _configured_import_roots(self) -> list[Path]:
        roots: list[Path] = []
        for configured in self.config.asset_import_roots:
            root = Path(configured).expanduser().resolve()
            if root not in roots:
                roots.append(root)
        return roots

    def _selected_import_roots(self, selector: str | None) -> list[Path]:
        roots = self._configured_import_roots()
        if selector is None:
            return roots
        selected = selector.strip()
        if not selected:
            return roots
        matches: list[Path] = []
        for index, root in enumerate(roots):
            configured = self.config.asset_import_roots[index]
            if selected in {root.name, str(root), configured, str(index), str(index + 1)}:
                matches.append(root)
        if not matches:
            raise ManuscriptError("import_root_not_allowed", "Import root selector does not match a configured import root.", {"selector": selector})
        return matches

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _resolve_import_source(self, raw_path: str, import_root_selector: str | None) -> ImportSource:
        if not raw_path or raw_path.strip() == "":
            raise ManuscriptError("invalid_image_source", "Source image path must not be empty.")
        roots = self._selected_import_roots(import_root_selector)
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ManuscriptError("source_not_found", "Source image was not found in the selected import roots.", {"path": raw_path}) from exc
            for root in roots:
                if self._is_under(resolved, root):
                    if not resolved.is_file():
                        raise ManuscriptError("not_a_file", "Source image path is not a file.", {"path": raw_path})
                    self._validate_image_file(resolved, display_path=resolved.name)
                    return ImportSource(relative=resolved.relative_to(root).as_posix(), path=resolved, root=root)
            raise ManuscriptError("source_outside_import_roots", "Source image is not inside an allowed import root.")
        relative = self._normalize_relative(raw_path)
        for root in roots:
            try:
                path = (root / relative).resolve(strict=True)
            except FileNotFoundError:
                continue
            if not self._is_under(path, root):
                raise ManuscriptError("source_path_escape_rejected", "Source image resolves outside its import root.", {"path": raw_path})
            if path.is_file():
                self._validate_image_file(path, display_path=relative)
                return ImportSource(relative=relative, path=path, root=root)
        raise ManuscriptError("source_not_found", "Source image was not found in the selected import roots.", {"path": raw_path})

    def _latest_importable_image(self, import_root_selector: str | None) -> ImportSource:
        newest: ImportSource | None = None
        newest_mtime = -1.0
        for root in self._selected_import_roots(import_root_selector):
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.name.startswith("."):
                    continue
                resolved = path.resolve(strict=True)
                if not self._is_under(resolved, root):
                    continue
                try:
                    self._validate_image_file(resolved, display_path=resolved.name)
                except ManuscriptError:
                    continue
                mtime = resolved.stat().st_mtime
                if mtime > newest_mtime:
                    newest = ImportSource(relative=resolved.relative_to(root).as_posix(), path=resolved, root=root)
                    newest_mtime = mtime
        if newest is None:
            raise ManuscriptError("no_importable_images", "No importable images were found in the selected import roots.")
        return newest

    def _image_asset_root_path(self) -> Path:
        relative = self._normalize_relative(self.config.image_asset_root)
        path = (self.root / relative).resolve(strict=False)
        if not self._is_under_root(path):
            raise ManuscriptError("asset_root_escape_rejected", "Configured image asset root escapes MANUSCRIPT_ROOT.")
        return path

    def _image_metadata_path(self) -> Path:
        relative = self._normalize_relative(self.config.asset_root)
        path = (self.root / relative / "image-metadata.json").resolve(strict=False)
        if not self._is_under_root(path):
            raise ManuscriptError("asset_root_escape_rejected", "Configured asset root escapes MANUSCRIPT_ROOT.")
        return path

    def _resolve_workspace_image_destination(self, raw_path: str, *, must_exist: bool, allow_bare_filename: bool = False) -> ResolvedPath:
        if allow_bare_filename and "/" not in raw_path and "\\" not in raw_path:
            raw_path = f"{self.config.image_asset_root.rstrip('/')}/general/{raw_path}"
        relative = self._normalize_relative(raw_path)
        suffix = Path(relative).suffix.lower()
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ManuscriptError("unsupported_image_extension", "Only .png, .jpg, .jpeg, .webp, .gif, and .svg image assets are supported.", {"path": relative})
        image_root = self._image_asset_root_path()
        candidate = (self.root / relative).resolve(strict=must_exist)
        if not self._is_under(candidate if must_exist else candidate.parent.resolve(strict=False), self.root):
            raise ManuscriptError("path_escape_rejected", "Destination escapes MANUSCRIPT_ROOT.", {"path": relative})
        if not self._is_under(candidate if must_exist else candidate.parent.resolve(strict=False), image_root):
            raise ManuscriptError("asset_destination_rejected", "Image destination must be under the configured image asset root.", {"path": relative})
        if must_exist:
            if not candidate.is_file():
                raise ManuscriptError("not_a_file", "Image path is not a file.", {"path": relative})
            self._validate_image_file(candidate, display_path=relative)
        return ResolvedPath(relative=relative, path=self.root / relative)

    def _read_image_metadata(self) -> dict[str, Any]:
        path = self._image_metadata_path()
        if not path.exists():
            return {"images": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManuscriptError("invalid_image_metadata", "assets/image-metadata.json could not be loaded.", {"reason": str(exc)}) from exc
        if not isinstance(data, dict) or not isinstance(data.get("images", {}), dict):
            raise ManuscriptError("invalid_image_metadata", "assets/image-metadata.json must contain an object with an images object.")
        data.setdefault("images", {})
        return data

    def _upsert_image_metadata(
        self,
        *,
        source_path: Path | None,
        source_relative_path: str | None,
        source_import_root: Path | None,
        destination_relative_path: str,
        image_type: str,
        dimensions: dict[str, int] | None,
        file_size: int,
        sha256: str,
        description: str | None,
        generation_prompt: str | None,
        associated_chapter: str | None,
        tags: list[str] | None,
        overwrite: bool,
        declared_mime_type: str | None = None,
        source: str | None = None,
        original_filename: str | None = None,
    ) -> dict[str, Any]:
        metadata = self._read_image_metadata()
        entry = {
            "original_filename": original_filename if original_filename is not None else (source_path.name if source_path else None),
            "saved_filename": Path(destination_relative_path).name,
            "saved_relative_path": destination_relative_path,
            "imported_at": datetime.now(UTC).isoformat(),
            "timestamp": datetime.now(UTC).isoformat(),
            "source_import_folder": str(source_import_root) if source_import_root else None,
            "source_relative_path": source_relative_path,
            "source": source,
            "file_size": file_size,
            "size_bytes": file_size,
            "sha256": sha256,
            "image_type": image_type,
            "mime_type": declared_mime_type,
            "width": dimensions.get("width") if dimensions else None,
            "height": dimensions.get("height") if dimensions else None,
            "dimensions": dimensions,
            "description": description,
            "generation_prompt": generation_prompt,
            "prompt": generation_prompt,
            "associated_chapter": associated_chapter,
            "chapter": associated_chapter,
            "tags": tags or [],
            "declared_mime_type": declared_mime_type,
            "overwrite": overwrite,
        }
        metadata.setdefault("images", {})[destination_relative_path] = entry
        self._atomic_write(self._image_metadata_path(), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        return entry

    def _validate_image_file(self, path: Path, *, display_path: str) -> dict[str, Any]:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ManuscriptError("unsupported_image_extension", "Only .png, .jpg, .jpeg, .webp, .gif, and .svg image assets are supported.", {"path": display_path})
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ManuscriptError("read_failed", "Could not read image file.", {"path": display_path}) from exc
        return self._validate_image_bytes(data, suffix, display_path)

    def _validate_image_bytes(self, data: bytes, suffix: str, display_path: str) -> dict[str, Any]:
        if not data:
            raise ManuscriptError("invalid_image_bytes", "Image file is empty.", {"path": display_path})
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ManuscriptError("unsupported_image_extension", "Only .png, .jpg, .jpeg, .webp, .gif, and .svg image assets are supported.", {"path": display_path})
        if suffix == ".png":
            return self._validate_png(data, display_path)
        if suffix in {".jpg", ".jpeg"}:
            return self._validate_jpeg(data, display_path)
        if suffix == ".gif":
            return self._validate_gif(data, display_path)
        if suffix == ".webp":
            return self._validate_webp(data, display_path)
        if suffix == ".svg":
            return self._validate_svg(data, display_path)
        raise ManuscriptError("unsupported_image_extension", "Unsupported image extension.", {"path": display_path})

    @staticmethod
    def _validate_png(data: bytes, display_path: str) -> dict[str, Any]:
        if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
            raise ManuscriptError("invalid_image_bytes", "PNG image header is invalid.", {"path": display_path})
        width, height = struct.unpack(">II", data[16:24])
        return {"image_type": "png", "dimensions": {"width": width, "height": height}}

    @staticmethod
    def _validate_gif(data: bytes, display_path: str) -> dict[str, Any]:
        if len(data) < 10 or not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
            raise ManuscriptError("invalid_image_bytes", "GIF image header is invalid.", {"path": display_path})
        width, height = struct.unpack("<HH", data[6:10])
        return {"image_type": "gif", "dimensions": {"width": width, "height": height}}

    @staticmethod
    def _validate_webp(data: bytes, display_path: str) -> dict[str, Any]:
        if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise ManuscriptError("invalid_image_bytes", "WebP image header is invalid.", {"path": display_path})
        return {"image_type": "webp", "dimensions": None}

    @staticmethod
    def _validate_svg(data: bytes, display_path: str) -> dict[str, Any]:
        if b"\x00" in data[:4096]:
            raise ManuscriptError("invalid_image_bytes", "SVG must be text, not binary data.", {"path": display_path})
        try:
            text = data[:4096].decode("utf-8", errors="strict").lstrip()
        except UnicodeDecodeError as exc:
            raise ManuscriptError("invalid_image_bytes", "SVG must be valid UTF-8 text.", {"path": display_path}) from exc
        if "<svg" not in text[:1000].lower():
            raise ManuscriptError("invalid_image_bytes", "SVG image header is invalid.", {"path": display_path})
        return {"image_type": "svg", "dimensions": None}

    @staticmethod
    def _validate_jpeg(data: bytes, display_path: str) -> dict[str, Any]:
        if len(data) < 4 or data[:2] != b"\xff\xd8":
            raise ManuscriptError("invalid_image_bytes", "JPEG image header is invalid.", {"path": display_path})
        index = 2
        while index + 3 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            segment_length = struct.unpack(">H", data[index : index + 2])[0]
            if segment_length < 2 or index + segment_length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and segment_length >= 7:
                height, width = struct.unpack(">HH", data[index + 3 : index + 7])
                return {"image_type": "jpeg", "dimensions": {"width": width, "height": height}}
            index += segment_length
        if data.endswith(b"\xff\xd9"):
            return {"image_type": "jpeg", "dimensions": None}
        raise ManuscriptError("invalid_image_bytes", "JPEG image header is invalid.", {"path": display_path})

    @staticmethod
    def _ensure_matching_image_extension(path: Path, image_type: str, display_path: str) -> None:
        suffix = path.suffix.lower()
        allowed = {".jpg", ".jpeg"} if image_type == "jpeg" else {f".{image_type}"}
        if suffix not in allowed:
            raise ManuscriptError("image_extension_mismatch", "Destination extension does not match detected image type.", {"path": display_path, "image_type": image_type})

    @staticmethod
    def _looks_pathological_regex(pattern: str) -> bool:
        return re.search(r"\((?:[^()\\]|\\.)*[*+](?:[^()\\]|\\.)*\)[*+{]", pattern) is not None

    def _search_scope(self, paths_or_globs: list[str] | None) -> list[Path]:
        if not paths_or_globs:
            return list(self.iter_documents(recursive=True))
        docs: dict[str, Path] = {}
        all_docs = list(self.iter_documents(recursive=True))
        for item in paths_or_globs:
            if any(char in item for char in "*?[]"):
                pattern = self._normalize_glob(item)
                for path in all_docs:
                    rel = path.relative_to(self.root).as_posix()
                    if fnmatch.fnmatch(rel, pattern):
                        docs[rel] = path
            else:
                resolved = self.resolve_document_path(item)
                docs[resolved.relative] = resolved.path
        return [docs[key] for key in sorted(docs)]

    def create_document(self, relative_path: str, content: str, *, overwrite: bool = False, change_summary: str | None = None) -> dict[str, Any]:
        self._check_write_size(content)
        resolved = self.resolve_document_path(relative_path, must_exist=False)
        if resolved.path.exists() and not overwrite:
            raise ManuscriptError("document_exists", "Destination already exists; pass overwrite=true only for intentional replacement.")
        if resolved.path.exists():
            self._snapshot(resolved.relative, resolved.path, "create_overwrite", change_summary, old_revision=self.revision_for_path(resolved.path))
        resolved.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(resolved.path, content)
        return {"path": resolved.relative, "revision": self.revision_for_path(resolved.path), "overwritten": overwrite}

    def append_document(self, relative_path: str, content: str, expected_revision: str, *, change_summary: str | None = None) -> dict[str, Any]:
        resolved = self.resolve_document_path(relative_path)
        old_revision = self._require_revision(resolved.path, expected_revision)
        old_text = resolved.path.read_text(encoding="utf-8")
        new_text = old_text + content
        self._check_write_size(new_text)
        version = self._snapshot(resolved.relative, resolved.path, "append_document", change_summary, old_revision=old_revision)
        self._atomic_write(resolved.path, new_text)
        new_revision = self.revision_for_path(resolved.path)
        self._update_history_new_revision(version, new_revision)
        return {"path": resolved.relative, "old_revision": old_revision, "new_revision": new_revision, "snapshot_version": version}

    def write_document(self, relative_path: str, content: str, expected_revision: str, *, change_summary: str) -> dict[str, Any]:
        self._check_write_size(content)
        resolved = self.resolve_document_path(relative_path)
        old_revision = self._require_revision(resolved.path, expected_revision)
        old_text = resolved.path.read_text(encoding="utf-8")
        diff = "".join(difflib.unified_diff(old_text.splitlines(True), content.splitlines(True), fromfile=f"a/{resolved.relative}", tofile=f"b/{resolved.relative}"))
        version = self._snapshot(resolved.relative, resolved.path, "write_document", change_summary, old_revision=old_revision)
        self._atomic_write(resolved.path, content)
        new_revision = self.revision_for_path(resolved.path)
        self._update_history_new_revision(version, new_revision)
        return {"path": resolved.relative, "old_revision": old_revision, "new_revision": new_revision, "snapshot_version": version, "applied_diff": diff}

    def apply_patch(self, relative_path: str, expected_revision: str, unified_diff_patch: str, *, change_summary: str | None = None) -> dict[str, Any]:
        resolved = self.resolve_document_path(relative_path)
        old_revision = self._require_revision(resolved.path, expected_revision)
        old_text = resolved.path.read_text(encoding="utf-8")
        new_text = self._apply_unified_diff(old_text, unified_diff_patch)
        self._check_write_size(new_text)
        version = self._snapshot(resolved.relative, resolved.path, "apply_patch", change_summary, old_revision=old_revision)
        self._atomic_write(resolved.path, new_text)
        new_revision = self.revision_for_path(resolved.path)
        self._update_history_new_revision(version, new_revision)
        added, removed = self._count_diff_changes(unified_diff_patch)
        return {
            "path": resolved.relative,
            "applied_diff": unified_diff_patch,
            "old_revision": old_revision,
            "new_revision": new_revision,
            "lines_added": added,
            "lines_removed": removed,
            "snapshot_version": version,
        }

    def _apply_unified_diff(self, old_text: str, patch_text: str) -> str:
        if not patch_text.strip().startswith("--- "):
            raise ManuscriptError("malformed_patch", "Patch must be a unified diff with --- and +++ headers.")
        old_lines = old_text.splitlines(keepends=True)
        new_lines: list[str] = []
        old_index = 0
        patch_lines = patch_text.splitlines(keepends=True)
        cursor = 0
        saw_hunk = False
        while cursor < len(patch_lines):
            line = patch_lines[cursor]
            if line.startswith("--- ") or line.startswith("+++ "):
                cursor += 1
                continue
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not match:
                raise ManuscriptError("malformed_patch", "Patch contains content outside a valid hunk.", {"line": line.strip()})
            saw_hunk = True
            old_start = int(match.group(1))
            target_index = old_start - 1
            if target_index < old_index:
                raise ManuscriptError("ambiguous_patch", "Patch hunks overlap or apply out of order.")
            new_lines.extend(old_lines[old_index:target_index])
            old_index = target_index
            cursor += 1
            while cursor < len(patch_lines) and not patch_lines[cursor].startswith("@@ "):
                hunk_line = patch_lines[cursor]
                if hunk_line.startswith(("--- ", "+++ ")) and cursor > 1:
                    raise ManuscriptError("malformed_patch", "Multiple file patches are not supported.")
                if hunk_line.startswith(" "):
                    expected = hunk_line[1:]
                    if old_index >= len(old_lines) or old_lines[old_index] != expected:
                        raise ManuscriptError("patch_context_mismatch", "Patch context does not match the current document.")
                    new_lines.append(expected)
                    old_index += 1
                elif hunk_line.startswith("-"):
                    expected = hunk_line[1:]
                    if old_index >= len(old_lines) or old_lines[old_index] != expected:
                        raise ManuscriptError("patch_context_mismatch", "Patch removal does not match the current document.")
                    old_index += 1
                elif hunk_line.startswith("+"):
                    new_lines.append(hunk_line[1:])
                elif hunk_line.startswith("\\ No newline at end of file"):
                    pass
                else:
                    raise ManuscriptError("malformed_patch", "Unsupported patch hunk line.", {"line": hunk_line.strip()})
                cursor += 1
        if not saw_hunk:
            raise ManuscriptError("malformed_patch", "Patch has no hunks.")
        new_lines.extend(old_lines[old_index:])
        return "".join(new_lines)

    @staticmethod
    def _count_diff_changes(patch_text: str) -> tuple[int, int]:
        added = removed = 0
        for line in patch_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return added, removed

    def rename_document(self, existing_relative_path: str, new_relative_path: str, expected_revision: str) -> dict[str, Any]:
        source = self.resolve_document_path(existing_relative_path)
        old_revision = self._require_revision(source.path, expected_revision)
        dest = self.resolve_document_path(new_relative_path, must_exist=False)
        if dest.path.exists():
            raise ManuscriptError("destination_exists", "Rename destination already exists.", {"path": dest.relative})
        version = self._snapshot(source.relative, source.path, "rename_document", f"Rename to {dest.relative}", old_revision=old_revision)
        dest.path.parent.mkdir(parents=True, exist_ok=True)
        source.path.replace(dest.path)
        self._update_history_new_revision(version, self.revision_for_path(dest.path), current_path=dest.relative)
        return {"old_path": source.relative, "new_path": dest.relative, "revision": self.revision_for_path(dest.path), "snapshot_version": version}

    def delete_document(self, relative_path: str, expected_revision: str, *, change_summary: str | None = None) -> dict[str, Any]:
        if not self.config.deletion_enabled:
            raise ManuscriptError("deletion_disabled", "Deletion is disabled in manuscript.config.json.")
        resolved = self.resolve_document_path(relative_path)
        old_revision = self._require_revision(resolved.path, expected_revision)
        version = self._snapshot(resolved.relative, resolved.path, "delete_document", change_summary, old_revision=old_revision)
        trash = self.history_dir / "trash" / version / resolved.relative
        trash.parent.mkdir(parents=True, exist_ok=True)
        resolved.path.replace(trash)
        self._update_history_new_revision(version, None)
        return {"path": resolved.relative, "deleted_to_version": version, "old_revision": old_revision}

    def get_document_history(self, relative_path: str) -> dict[str, Any]:
        relative = self._normalize_relative(relative_path)
        index = self._history_index()
        versions = [entry for entry in index if entry.get("relative_path") == relative or entry.get("current_path") == relative]
        return {"path": relative, "versions": versions}

    def restore_document_version(self, relative_path: str, version_identifier: str, expected_current_revision: str) -> dict[str, Any]:
        target = self.resolve_document_path(relative_path, must_exist=False)
        if target.path.exists():
            old_revision = self._require_revision(target.path, expected_current_revision)
            restore_snapshot = self._snapshot(target.relative, target.path, "restore_document_version", f"Restore {version_identifier}", old_revision=old_revision)
        else:
            restore_snapshot = None
        snapshot_file = self.history_dir / "versions" / version_identifier / "content"
        if not snapshot_file.exists():
            raise ManuscriptError("version_not_found", "No saved version exists with that identifier.", {"version": version_identifier})
        target.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target.path, snapshot_file.read_text(encoding="utf-8"))
        return {
            "path": target.relative,
            "restored_version": version_identifier,
            "new_revision": self.revision_for_path(target.path),
            "undo_snapshot_version": restore_snapshot,
        }

    def _require_revision(self, path: Path, expected_revision: str) -> str:
        current = self.revision_for_path(path)
        if current != expected_revision:
            raise ManuscriptError(
                "stale_revision",
                "Document changed after it was read; reread it before trying again.",
                {"expected_revision": expected_revision, "current_revision": current},
            )
        return current

    def _check_write_size(self, content: str) -> None:
        if len(content.encode("utf-8")) > self.config.max_write_chars:
            raise ManuscriptError("write_too_large", "Write exceeds configured maximum size.", {"max_write_chars": self.config.max_write_chars})

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            finally:
                raise

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            finally:
                raise

    def _snapshot(
        self,
        relative_path: str,
        path: Path,
        operation: str,
        change_summary: str | None,
        *,
        old_revision: str | None,
    ) -> str:
        version = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]
        version_dir = self.history_dir / "versions" / version
        version_dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, version_dir / "content")
        metadata = {
            "version": version,
            "relative_path": relative_path,
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "change_summary": change_summary,
            "old_revision": old_revision,
            "new_revision": None,
        }
        (version_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        LOGGER.info("snapshot_created", extra={"version": version, "operation": operation, "path": relative_path})
        self._enforce_retention(relative_path)
        return version

    def _history_index(self) -> list[dict[str, Any]]:
        versions_root = self.history_dir / "versions"
        if not versions_root.exists():
            return []
        entries = []
        for metadata_path in sorted(versions_root.glob("*/metadata.json")):
            try:
                entries.append(json.loads(metadata_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return entries

    def _update_history_new_revision(self, version: str, new_revision: str | None, *, current_path: str | None = None) -> None:
        metadata_path = self.history_dir / "versions" / version / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["new_revision"] = new_revision
        if current_path:
            metadata["current_path"] = current_path
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def _enforce_retention(self, relative_path: str) -> None:
        keep = self.config.history_retention_versions
        if keep is None:
            return
        entries = [entry for entry in self._history_index() if entry.get("relative_path") == relative_path]
        entries.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
        for entry in entries[max(keep, 0) :]:
            version = entry.get("version")
            if isinstance(version, str):
                shutil.rmtree(self.history_dir / "versions" / version, ignore_errors=True)

    def assert_atomic_write_replaced(self, before: Path, after: Path) -> bool:
        return not filecmp.cmp(before, after, shallow=False)
