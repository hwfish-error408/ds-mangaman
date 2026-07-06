"""
pdf_importer.py
================
Converts a manga PDF ("scan" pages, one image per page) into the folder
structure required by DS-Mangaman (main_gui.py):

    assets/jpg_comic/
    |-- 0010/                  <- 4-digit chapter folder
    |   |-- 001.jpg
    |   |-- 002.jpg
    |   `-- ...
    `-- 0020/
        |-- 001.jpg
        `-- ...

Can be used:
  1) from the command line:
        python pdf_importer.py "My Manga.pdf" --mode split --pages-per-chapter 20
  2) imported as a library by main_gui.py (import_pdf() function).

Dependencies:
    pip install pymupdf
(Pillow is already a dependency of ds-mangaman, so it doesn't need to be installed separately)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import fitz  # PyMuPDF


SUPPORTED_FORMATS = ("jpg", "png", "webp")

# Pillow format -> file extension and "format=" name to pass to Image.save()
_PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


class PdfImportError(RuntimeError):
    """Application error (message ready for the end user)."""


@dataclass
class ChapterPlan:
    """A chapter to be generated: PDF page range -> 4-digit folder."""
    chapter_id: int          # e.g. 10  -> folder "0010"
    start_page: int          # PDF page index, 0-based, inclusive
    end_page: int            # PDF page index, 0-based, inclusive
    title: Optional[str] = None

    @property
    def folder_name(self) -> str:
        return f"{self.chapter_id:04d}"

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1


@dataclass
class ImportResult:
    chapters: List[Tuple[str, int]] = field(default_factory=list)  # (folder, n_pages)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Internal utilities
# --------------------------------------------------------------------------- #

def _validate_format(fmt: str) -> str:
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in SUPPORTED_FORMATS:
        raise PdfImportError(
            f"Unsupported image format: '{fmt}'. "
            f"Valid formats: {', '.join(SUPPORTED_FORMATS)}"
        )
    return fmt


def _next_free_chapter_id(out_base: str, start_id: int, step: int) -> int:
    """Finds the first chapter id (>= start_id) whose 4-digit folder
    does not already exist under out_base, advancing by `step` at a time."""
    cid = start_id
    while os.path.isdir(os.path.join(out_base, f"{cid:04d}")):
        cid += step
    return cid


def _build_chapter_plans(
    doc: "fitz.Document",
    mode: str,
    start_chapter: int,
    chapter_step: int,
    pages_per_chapter: Optional[int],
    warnings: List[str],
) -> List[ChapterPlan]:
    """Divides the PDF pages into one or more chapters based on the chosen mode."""
    n_pages = doc.page_count

    if mode == "single":
        return [ChapterPlan(start_chapter, 0, n_pages - 1)]

    if mode == "split":
        if not pages_per_chapter or pages_per_chapter < 1:
            raise PdfImportError(
                "--pages-per-chapter is required (and >= 1) when --mode split."
            )
        plans = []
        cid = start_chapter
        page = 0
        while page < n_pages:
            end = min(page + pages_per_chapter - 1, n_pages - 1)
            plans.append(ChapterPlan(cid, page, end))
            page = end + 1
            cid += chapter_step
        return plans

    if mode == "toc":
        toc = doc.get_toc(simple=True)  # [[level, title, 1_based_page], ...]
        top_level = [e for e in toc if e[0] == 1] if toc else []
        if not top_level:
            warnings.append(
                "The PDF does not contain top-level bookmarks (TOC): "
                "automatically falling back to 'single' mode (a single chapter)."
            )
            return [ChapterPlan(start_chapter, 0, n_pages - 1)]

        plans = []
        cid = start_chapter
        for idx, entry in enumerate(top_level):
            _, title, page_1based = entry[0], entry[1], entry[2]
            start = max(page_1based - 1, 0)
            if idx + 1 < len(top_level):
                end = max(top_level[idx + 1][2] - 2, start)
            else:
                end = n_pages - 1
            plans.append(ChapterPlan(cid, start, end, title=title))
            cid += chapter_step
        return plans

    raise PdfImportError(f"Unknown mode: '{mode}' (use single | split | toc)")


def _page_to_pil_image(page: "fitz.Page", dpi: int):
    """Rasterizes a PDF page into an RGB PIL image."""
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    return pix.pil_image()


def _extract_raw_page_image(doc: "fitz.Document", page: "fitz.Page", dpi_fallback: int):
    """Attempts to extract the original embedded image in the page (no
    recompression). If the page does not contain exactly one image, it returns
    None and the caller must fall back to standard rendering."""
    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    xref = images[0][0]
    try:
        info = doc.extract_image(xref)
    except Exception:
        return None
    if not info or "image" not in info:
        return None

    from io import BytesIO
    from PIL import Image

    try:
        pil_img = Image.open(BytesIO(info["image"]))
        pil_img.load()
    except Exception:
        return None

    if pil_img.mode in ("CMYK", "P", "LA"):
        pil_img = pil_img.convert("RGB")
    elif pil_img.mode == "RGBA":
        # jpg does not support alpha: flatten onto white background
        background = pil_img.convert("RGB")
        pil_img = background
    return pil_img


def _save_page_image(pil_img, out_path: str, fmt: str, quality: int) -> None:
    save_fmt = _PIL_FORMAT[fmt]
    if fmt == "jpg" and pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    kwargs = {}
    if fmt in ("jpg", "webp"):
        kwargs["quality"] = quality
    if fmt == "jpg":
        kwargs["optimize"] = True
    pil_img.save(out_path, format=save_fmt, **kwargs)


# --------------------------------------------------------------------------- #
# Main API
# --------------------------------------------------------------------------- #

def import_pdf(
    pdf_path: str,
    out_base: str,
    mode: str = "single",
    pages_per_chapter: Optional[int] = None,
    start_chapter: int = 10,
    chapter_step: int = 10,
    img_format: str = "jpg",
    dpi: int = 200,
    quality: int = 92,
    raw: bool = False,
    pad: Optional[int] = None,
    overwrite: bool = False,
    avoid_collisions: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> ImportResult:
    """Converts `pdf_path` into the folder structure expected by ds-mangaman,
    inside `out_base` (typically assets/jpg_comic).

    Main parameters:
      mode: "single" (one chapter with all pages), "split" (a new
            chapter every N pages), "toc" (one chapter for each top-level
            bookmark in the PDF).
      img_format: "jpg" | "png" | "webp"
      raw: if True, attempts to extract the original embedded image instead
           of re-rendering the page (useful for "scan" PDFs already composed
           of one image per page: zero quality loss).
      overwrite: if True, overwrites already existing chapter folders;
                 if False (default) and a collision exists, the same chapter id
                 is automatically shifted forward when avoid_collisions=True,
                 otherwise an error is raised.
      progress_cb(current_page, total_pages, message): optional callback
           to update a progress bar / GUI log.

    Returns an ImportResult containing the generated (folder, n_pages) list.
    """
    fmt = _validate_format(img_format)

    if not os.path.isfile(pdf_path):
        raise PdfImportError(f"PDF file not found: {pdf_path}")

    os.makedirs(out_base, exist_ok=True)

    result = ImportResult()

    if start_chapter % chapter_step != 0:
        rounded = ((start_chapter + chapter_step - 1) // chapter_step) * chapter_step
        result.warnings.append(
            f"Start chapter {start_chapter:04d} is not a multiple of the chapter step "
            f"({chapter_step}): rounded up to {rounded:04d}."
        )
        start_chapter = rounded

    doc = fitz.open(pdf_path)
    try:
        if doc.page_count == 0:
            raise PdfImportError("The PDF contains no pages.")

        plans = _build_chapter_plans(
            doc, mode, start_chapter, chapter_step, pages_per_chapter, result.warnings
        )

        # Resolves any folder name collisions BEFORE writing anything,
        # so a halfway failure doesn't leave incomplete work on the wrong ids.
        used_ids = set()
        for plan in plans:
            dst_dir = os.path.join(out_base, plan.folder_name)
            if os.path.isdir(dst_dir) and os.listdir(dst_dir) and not overwrite:
                if not avoid_collisions:
                    raise PdfImportError(
                        f"Chapter folder '{plan.folder_name}' already exists and is not empty. "
                        f"Use --overwrite or choose a different --start-chapter."
                    )
                new_id = plan.chapter_id
                while (
                    new_id in used_ids
                    or (
                        os.path.isdir(os.path.join(out_base, f"{new_id:04d}"))
                        and os.listdir(os.path.join(out_base, f"{new_id:04d}"))
                    )
                ):
                    new_id += chapter_step
                if new_id != plan.chapter_id:
                    result.warnings.append(
                        f"Chapter {plan.folder_name} already exists: automatically shifted to {new_id:04d}."
                    )
                plan.chapter_id = new_id
            used_ids.add(plan.chapter_id)

        total_pages = sum(p.page_count for p in plans)
        done_pages = 0

        for plan in plans:
            dst_dir = os.path.join(out_base, plan.folder_name)
            os.makedirs(dst_dir, exist_ok=True)

            width = pad or max(3, len(str(plan.page_count)))

            for i, page_idx in enumerate(range(plan.start_page, plan.end_page + 1), start=1):
                page = doc.load_page(page_idx)

                pil_img = None
                if raw:
                    pil_img = _extract_raw_page_image(doc, page, dpi)
                if pil_img is None:
                    pil_img = _page_to_pil_image(page, dpi)

                out_name = f"{i:0{width}d}.{fmt}"
                out_path = os.path.join(dst_dir, out_name)
                _save_page_image(pil_img, out_path, fmt, quality)

                done_pages += 1
                if progress_cb:
                    label = plan.title or plan.folder_name
                    progress_cb(
                        done_pages, total_pages,
                        f"Chapter {plan.folder_name} ({label}): page {i}/{plan.page_count}",
                    )

            result.chapters.append((plan.folder_name, plan.page_count))

    finally:
        doc.close()

    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf_importer.py",
        description="Converts a manga PDF into the folder structure required by ds-mangaman.",
    )
    parser.add_argument("pdf", help="Path of the input PDF file.")
    parser.add_argument(
        "--out", default=None,
        help="Base destination folder (default: assets/jpg_comic next to this script).",
    )
    parser.add_argument(
        "--mode", choices=["single", "split", "toc"], default="single",
        help="single = one single chapter; split = one chapter every N pages; "
             "toc = one chapter for each top-level bookmark in the PDF.",
    )
    parser.add_argument("--pages-per-chapter", type=int, default=None,
                         help="Number of pages per chapter (required with --mode split).")
    parser.add_argument("--start-chapter", type=int, default=10,
                         help="Id of the first chapter, e.g. 10 -> folder 0010 (default: 10).")
    parser.add_argument("--chapter-step", type=int, default=10,
                         help="Increment between a chapter and the next (default: 10).")
    parser.add_argument("--format", choices=SUPPORTED_FORMATS, default="jpg",
                         help="Output image format (default: jpg).")
    parser.add_argument("--dpi", type=int, default=200,
                         help="Rendering DPI when --raw is not used (default: 200).")
    parser.add_argument("--quality", type=int, default=92,
                         help="JPEG/WEBP quality 1-100 (default: 92).")
    parser.add_argument("--raw", action="store_true",
                         help="Extracts the original embedded image in each page instead of "
                              "re-rendering it (max fidelity if the PDF is already a scan per page).")
    parser.add_argument("--pad", type=int, default=None,
                         help="Forces the number of digits in the filename (default: automatic, min 3).")
    parser.add_argument("--overwrite", action="store_true",
                         help="Overwrites already existing chapter folders instead of shifting forward.")
    return parser


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.out is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        args.out = os.path.join(base_dir, "assets", "jpg_comic")

    def cli_progress(done, total, message):
        print(f"[{done:4d}/{total:4d}] {message}")

    try:
        result = import_pdf(
            pdf_path=args.pdf,
            out_base=args.out,
            mode=args.mode,
            pages_per_chapter=args.pages_per_chapter,
            start_chapter=args.start_chapter,
            chapter_step=args.chapter_step,
            img_format=args.format,
            dpi=args.dpi,
            quality=args.quality,
            raw=args.raw,
            pad=args.pad,
            overwrite=args.overwrite,
            progress_cb=cli_progress,
        )
    except PdfImportError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print("\nCompleted:")
    for folder, n_pages in result.chapters:
        print(f"  - {folder}/  ({n_pages} pages)")
    for warn in result.warnings:
        print(f"[WARNING] {warn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())