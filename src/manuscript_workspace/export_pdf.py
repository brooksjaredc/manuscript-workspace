"""Command-line PDF export for manuscript text and image assets."""

from __future__ import annotations

import argparse
import html
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer

from manuscript_workspace.errors import ManuscriptError
from manuscript_workspace.store import ManuscriptStore, SUPPORTED_IMAGE_EXTENSIONS


@dataclass(frozen=True)
class Chapter:
    relative_path: str
    path: Path
    image_folder_slug: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a manuscript workspace to a PDF book.")
    parser.add_argument("--root", default=os.environ.get("MANUSCRIPT_ROOT"), help="Manuscript root. Defaults to MANUSCRIPT_ROOT.")
    parser.add_argument("--output", help="Output PDF path. Defaults to <root>/exports/<project-name>.pdf.")
    parser.add_argument("--include-reference", action="append", default=[], help="Reference document to include before chapters. Can be repeated.")
    parser.add_argument("--include-general-images", action="store_true", help="Include assets/images/general at the end.")
    parser.add_argument("--title", help="Override the title shown on the PDF title page.")
    parser.add_argument("--no-title-page", action="store_true", help="Do not add a title page.")
    return parser.parse_args()


def export_pdf(
    store: ManuscriptStore,
    output_path: Path | None = None,
    *,
    include_reference: list[str] | None = None,
    include_general_images: bool = False,
    title: str | None = None,
    title_page: bool = True,
) -> Path:
    output = output_path or default_output_path(store)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output),
        pagesize=LETTER,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=title or store.config.project_name,
        author="Manuscript Workspace",
    )
    styles = build_styles()
    story: list[object] = []

    if title_page:
        story.extend(title_page_flow(title or store.config.project_name, styles))

    for rel in include_reference or []:
        resolved = store.resolve_document_path(rel)
        story.extend(markdown_to_flowables(resolved.path.read_text(encoding="utf-8"), styles, fallback_title=resolved.relative))
        story.append(PageBreak())

    chapters = discover_chapters(store)
    for index, chapter in enumerate(chapters):
        if index > 0 and not isinstance(story[-1], PageBreak):
            story.append(PageBreak())
        story.extend(markdown_to_flowables(chapter.path.read_text(encoding="utf-8"), styles, fallback_title=chapter.relative_path))
        images = chapter_images(store, chapter)
        if images:
            story.append(Spacer(1, 0.2 * inch))
            story.append(Paragraph("Images", styles["MWHeading2"]))
            story.extend(images_to_flowables(store, images, styles))

    if include_general_images:
        general = image_files_under(store, "general")
        if general:
            story.append(PageBreak())
            story.append(Paragraph("General Images", styles["MWHeading1"]))
            story.extend(images_to_flowables(store, general, styles))

    if not story:
        story.append(Paragraph("No manuscript content found.", styles["MWBody"]))

    doc.build(story)
    return output


def default_output_path(store: ManuscriptStore) -> Path:
    project = slugify(store.config.project_name) or "manuscript"
    return store.root / "exports" / f"{project}.pdf"


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {
        "MWTitle": ParagraphStyle("MWTitle", parent=base["Title"], fontName="Times-Bold", fontSize=24, leading=30, alignment=TA_CENTER, spaceAfter=18),
        "MWSubtitle": ParagraphStyle("MWSubtitle", parent=base["Normal"], fontName="Times-Roman", fontSize=11, leading=14, alignment=TA_CENTER, textColor=colors.HexColor("#555555")),
        "MWHeading1": ParagraphStyle("MWHeading1", parent=base["Heading1"], fontName="Times-Bold", fontSize=18, leading=23, spaceBefore=12, spaceAfter=10),
        "MWHeading2": ParagraphStyle("MWHeading2", parent=base["Heading2"], fontName="Times-Bold", fontSize=14, leading=18, spaceBefore=10, spaceAfter=8),
        "MWBody": ParagraphStyle("MWBody", parent=base["BodyText"], fontName="Times-Roman", fontSize=11.5, leading=16, firstLineIndent=0.22 * inch, spaceAfter=7),
        "MWBullet": ParagraphStyle("MWBullet", parent=base["BodyText"], fontName="Times-Roman", fontSize=11, leading=15, leftIndent=0.25 * inch, firstLineIndent=-0.12 * inch, spaceAfter=5),
        "MWCaption": ParagraphStyle("MWCaption", parent=base["BodyText"], fontName="Times-Italic", fontSize=9, leading=12, textColor=colors.HexColor("#555555"), spaceAfter=12),
    }
    return styles


def title_page_flow(project_name: str, styles: dict[str, ParagraphStyle]) -> list[object]:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return [
        Spacer(1, 2.1 * inch),
        Paragraph(html.escape(project_name), styles["MWTitle"]),
        Paragraph(f"Exported {generated}", styles["MWSubtitle"]),
        PageBreak(),
    ]


def discover_chapters(store: ManuscriptStore) -> list[Chapter]:
    docs = list(store.iter_documents(recursive=True))
    chapters: dict[str, Path] = {}
    for pattern in store.config.chapter_globs:
        for path in docs:
            rel = path.relative_to(store.root).as_posix()
            if rel in chapters:
                continue
            if path.suffix.lower() == ".md" and matches_glob(rel, pattern):
                chapters[rel] = path
    return [Chapter(relative_path=rel, path=path, image_folder_slug=chapter_slug(rel)) for rel, path in sorted(chapters.items())]


def matches_glob(path: str, pattern: str) -> bool:
    from fnmatch import fnmatch

    return fnmatch(path, pattern)


def chapter_slug(relative_path: str) -> str | None:
    stem = Path(relative_path).stem.lower()
    match = re.search(r"chapter[\s_-]*(\d+)", stem)
    if not match:
        return None
    return f"chapter-{int(match.group(1)):02d}"


def markdown_to_flowables(markdown: str, styles: dict[str, ParagraphStyle], *, fallback_title: str) -> list[object]:
    flow: list[object] = []
    paragraph_lines: list[str] = []
    saw_heading = False

    def flush() -> None:
        if paragraph_lines:
            text = " ".join(line.strip() for line in paragraph_lines if line.strip())
            if text:
                flow.append(Paragraph(inline_markdown(text), styles["MWBody"]))
            paragraph_lines.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            text = line[level:].strip()
            if text:
                flow.append(Paragraph(inline_markdown(text), styles["MWHeading1" if level == 1 else "MWHeading2"]))
                saw_heading = True
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet:
            flush()
            flow.append(Paragraph(f"&bull; {inline_markdown(bullet.group(1))}", styles["MWBullet"]))
            continue
        paragraph_lines.append(line)

    flush()
    if not saw_heading:
        flow.insert(0, Paragraph(html.escape(fallback_title), styles["MWHeading1"]))
    return flow


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", escaped)
    return escaped


def image_files_under(store: ManuscriptStore, folder_slug: str) -> list[Path]:
    root = store.root / store.config.image_asset_root / folder_slug
    if not root.exists():
        return []
    files = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and path.suffix.lower() != ".svg"]
    return sorted(files, key=lambda path: path.name.lower())


def chapter_images(store: ManuscriptStore, chapter: Chapter) -> list[Path]:
    if chapter.image_folder_slug is None:
        return []
    return image_files_under(store, chapter.image_folder_slug)


def images_to_flowables(store: ManuscriptStore, images: Iterable[Path], styles: dict[str, ParagraphStyle]) -> list[object]:
    flow: list[object] = []
    metadata = store._read_image_metadata().get("images", {})
    max_width = 6.4 * inch
    max_height = 7.2 * inch
    for path in images:
        rel = path.relative_to(store.root).as_posix()
        try:
            image = Image(str(path))
            scale = min(max_width / image.drawWidth, max_height / image.drawHeight, 1.0)
            image.drawWidth *= scale
            image.drawHeight *= scale
            flow.append(Spacer(1, 0.12 * inch))
            flow.append(image)
            entry = metadata.get(rel) or {}
            caption = entry.get("description") or entry.get("prompt") or path.name
            flow.append(Paragraph(html.escape(caption), styles["MWCaption"]))
        except Exception as exc:
            flow.append(Paragraph(f"Skipped image {html.escape(rel)}: {html.escape(str(exc))}", styles["MWCaption"]))
    return flow


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-_.").lower()


def main() -> None:
    args = parse_args()
    if not args.root:
        raise SystemExit("MANUSCRIPT_ROOT is required, or pass --root /absolute/path/to/book.")
    try:
        store = ManuscriptStore(Path(args.root))
        output = export_pdf(
            store,
            Path(args.output) if args.output else None,
            include_reference=args.include_reference,
            include_general_images=args.include_general_images,
            title=args.title,
            title_page=not args.no_title_page,
        )
    except ManuscriptError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from exc
    print(f"Exported PDF: {output}")


if __name__ == "__main__":
    main()
