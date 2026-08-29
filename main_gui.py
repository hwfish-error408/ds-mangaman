import os
import sys
import shutil
import glob
import re
import platform
import subprocess
import threading
import tempfile
import unicodedata
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk, scrolledtext, filedialog
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import pdf_importer

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEVKITPRO_ROOT = None
MAKE_EXECUTABLE_PATH = "make"
GRIT_EXECUTABLE_PATH = "grit"

PAGE_WIDTH = 960
PAGE_HEIGHT = 1440
PREVIEW_WIDTH = 256
PREVIEW_HEIGHT = 384
FULL_PAGE_RAW_SIZE_BYTES = PAGE_WIDTH * PAGE_HEIGHT * 2
PREVIEW_RAW_SIZE_BYTES = PREVIEW_WIDTH * PREVIEW_HEIGHT * 2
RAW_PAGE_ASSET_SIZE_BYTES = FULL_PAGE_RAW_SIZE_BYTES + PREVIEW_RAW_SIZE_BYTES
MAX_CHAPTERS = 256
MAX_BOOKS = 128
BOOK_TITLE_CAPACITY = 49
BOOK_ID_CAPACITY = 49
MANIFEST_MAGIC = "DS_MANGAMAN_BOOK_V3_ASSET"
LAST_POSITION_PREFIX = "last_position="
ASSET_MAGIC_PALETTE8 = b"DSMP8L10"
ASSET_MAGIC_RGB555 = b"DSM16L10"
ASSET_PALETTE_SIZE_BYTES = 256 * 2
NDS_HEADER_SIZE = 0x4000
NDS_UNIT_CODE_OFFSET = 0x12
DSI_ENHANCED_UNIT_CODE = 0x02
MAX_READER_ROM_SIZE_BYTES = 4 * 1024 * 1024

ROTATIONS = {
    "No rotation": None,
    "90 degrees clockwise": Image.Transpose.ROTATE_270,
    "90 degrees counterclockwise": Image.Transpose.ROTATE_90,
    "180 degrees": Image.Transpose.ROTATE_180,
}

RESAMPLERS = {
    "Lanczos": Image.Resampling.LANCZOS,
    "Bicubic": Image.Resampling.BICUBIC,
}

BACKGROUND_COLORS = {
    "White": (255, 255, 255),
    "Black": (0, 0, 0),
}

ENHANCEMENT_MODES = (
    "Crisp text / manga",
    "Balanced",
    "None",
)

STORAGE_MODES = (
    "Compact 256 colors",
    "Full color RGB555",
)


@dataclass(frozen=True)
class PreprocessSettings:
    rotation: str = "No rotation"
    fit_mode: str = "Fit entire page"
    background: str = "White"
    resampling: str = "Lanczos"
    enhancement: str = "Crisp text / manga"
    storage: str = "Compact 256 colors"


def _candidate_devkitpro_roots():
    """Returns validated search candidates without duplicating paths."""
    candidates = [
        os.environ.get('DEVKITPRO'),
        '/opt/devkitpro',
    ]

    if platform.system() == 'Windows':
        candidates.extend([
            'C:\\devkitPro',
            'C:\\devkitpro',
            'D:\\devkitPro',
            'D:\\devkitpro',
        ])

    unique = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized = os.path.abspath(os.path.expanduser(candidate))
        key = os.path.normcase(normalized)
        if key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def verify_toolchain_infrastructure():
    """Validates the hardware toolchain footprint and injects standard paths into environment."""
    global DEVKITPRO_ROOT, MAKE_EXECUTABLE_PATH, GRIT_EXECUTABLE_PATH
    for root in _candidate_devkitpro_roots():
        if os.path.exists(os.path.join(root, 'devkitARM')) and os.path.exists(os.path.join(root, 'libnds')):
            DEVKITPRO_ROOT = root
            break

    if DEVKITPRO_ROOT:
        clean_root = DEVKITPRO_ROOT.replace('\\', '/') if platform.system() == 'Windows' else DEVKITPRO_ROOT
        os.environ['DEVKITPRO'] = clean_root
        os.environ['DEVKITARM'] = f"{clean_root}/devkitARM"

        devkitarm_bin = f"{clean_root}/devkitARM/bin"
        tools_bin = f"{clean_root}/tools/bin"
        toolchain_paths = [devkitarm_bin, tools_bin]

        if platform.system() == 'Windows':
            toolchain_paths.append(f"{clean_root}/msys2/usr/bin")

        current_path = os.environ.get('PATH', '')
        current_entries = current_path.split(os.pathsep) if current_path else []
        new_paths = [p for p in toolchain_paths if p not in current_entries]
        if new_paths:
            os.environ['PATH'] = os.pathsep.join(new_paths + current_entries)
            print("[GUI] devkitPro environment configured")

        msys_make = os.path.join(DEVKITPRO_ROOT, 'msys2', 'usr', 'bin', 'make.exe')
        if platform.system() == 'Windows' and os.path.exists(msys_make):
            MAKE_EXECUTABLE_PATH = msys_make
        else:
            detected_make = shutil.which('make')
            if detected_make:
                MAKE_EXECUTABLE_PATH = detected_make

        grit_name = 'grit.exe' if platform.system() == 'Windows' else 'grit'
        detected_grit = shutil.which(grit_name)
        bundled_grit = os.path.join(DEVKITPRO_ROOT, 'tools', 'bin', grit_name)
        if detected_grit:
            GRIT_EXECUTABLE_PATH = detected_grit
        elif os.path.isfile(bundled_grit):
            GRIT_EXECUTABLE_PATH = bundled_grit
        else:
            root_win = tk.Tk()
            root_win.withdraw()
            messagebox.showerror(
                "Toolchain Incomplete",
                "devkitPro was found, but its grit image compressor is missing. "
                "Reinstall or update the NDS development tools.",
            )
            root_win.destroy()
            sys.exit(0)

        print(f"[GUI] Found build utility: {MAKE_EXECUTABLE_PATH}")
        print(f"[GUI] Found image compressor: {GRIT_EXECUTABLE_PATH}")

        return True

    root_win = tk.Tk()
    root_win.withdraw()

    if platform.system() == 'Windows':
        installer_path = os.path.join(BASE_DIR, 'assets', 'devkitProUpdater-3.0.3.exe')
        alert_msg = (
            "devkitPro was not detected.\n\n"
            "Would you like to execute the bundled setup utility now?\n\n"
            f"Target utility: {installer_path}"
        )
        execute_wizard = messagebox.askyesno("Toolchain Missing", alert_msg, icon='warning')
        if execute_wizard:
            if os.path.exists(installer_path):
                try:
                    subprocess.Popen([installer_path], shell=False)
                except Exception as ex:
                    messagebox.showerror("Execution Error", f"Failed to open the setup wizard:\n{ex}")
            else:
                messagebox.showerror("File Error", f"The installation file was not found at:\n{installer_path}")
    else:
        messagebox.showerror(
            "Toolchain Missing",
            "devkitPro was not detected. Install the NDS development group and "
            "set DEVKITPRO (normally /opt/devkitpro), then restart DS-Mangaman.",
        )

    root_win.destroy()
    sys.exit(0)


verify_toolchain_infrastructure()


def natural_sort_key(path):
    """Sorts 2.jpg before 10.jpg while remaining stable for text names."""
    parts = re.split(r'(\d+)', os.path.basename(path).casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def normalize_book_title(value):
    """Creates the printable ASCII title used by the DSi book picker."""
    collapsed = ' '.join(value.split())
    ascii_title = unicodedata.normalize('NFKD', collapsed).encode(
        'ascii', 'ignore'
    ).decode('ascii')
    ascii_title = ''.join(
        character for character in ascii_title if 0x20 <= ord(character) <= 0x7e
    ).strip()
    ascii_title = ascii_title[:BOOK_TITLE_CAPACITY - 1].rstrip()
    if not ascii_title:
        raise ValueError(
            "Book name must contain at least one Latin letter, number, or ASCII symbol "
            "so the DSi can display it."
        )
    return ascii_title


def make_book_id(book_title):
    """Creates the safe, stable SD-card folder name for one book."""
    book_id = re.sub(r'[^a-z0-9]+', '-', book_title.casefold()).strip('-')
    book_id = book_id[:BOOK_ID_CAPACITY - 1].rstrip('-')
    if not book_id:
        raise ValueError("Book name must contain at least one letter or number.")
    return book_id


def is_safe_book_folder(value):
    return (
        0 < len(value) < BOOK_ID_CAPACITY and
        re.fullmatch(r'[A-Za-z0-9_-]+', value) is not None
    )


def validate_preprocess_settings(settings):
    if settings.rotation not in ROTATIONS:
        raise ValueError(f"Unknown rotation option: {settings.rotation}")
    if settings.fit_mode not in ("Fit entire page", "Fill both screens"):
        raise ValueError(f"Unknown framing option: {settings.fit_mode}")
    if settings.background not in BACKGROUND_COLORS:
        raise ValueError(f"Unknown background option: {settings.background}")
    if settings.resampling not in RESAMPLERS:
        raise ValueError(f"Unknown resampling option: {settings.resampling}")
    if settings.enhancement not in ENHANCEMENT_MODES:
        raise ValueError(f"Unknown detail enhancement option: {settings.enhancement}")
    if settings.storage not in STORAGE_MODES:
        raise ValueError(f"Unknown storage option: {settings.storage}")


def enhance_page(canvas, enhancement, asset_role):
    """Improves small text and line art without discarding page colors."""
    if enhancement == "None":
        return canvas
    if enhancement == "Balanced":
        if asset_role == "detail":
            return canvas.filter(
                ImageFilter.UnsharpMask(radius=0.45, percent=55, threshold=3)
            )
        return canvas.filter(
            ImageFilter.UnsharpMask(radius=0.65, percent=80, threshold=3)
        )

    if asset_role == "detail":
        # The detailed texture is supersampled at 960x1440. Gentle sharpening
        # avoids the hard stair-step edges that the former direct 1:1 view
        # exposed at maximum zoom.
        contrasted = ImageEnhance.Contrast(canvas).enhance(1.03)
        return contrasted.filter(
            ImageFilter.UnsharpMask(radius=0.45, percent=70, threshold=2)
        )

    # The preview is reduced to the LCD's exact 256x384 resolution, so it needs
    # stronger local contrast to keep small lettering legible.
    contrasted = ImageEnhance.Contrast(canvas).enhance(1.08)
    return contrasted.filter(
        ImageFilter.UnsharpMask(radius=0.75, percent=140, threshold=2)
    )


def render_page_canvas(img, target_size, settings, asset_role="detail"):
    """Fits one source page to a target canvas using the selected quality options."""
    resampler = RESAMPLERS[settings.resampling]

    if settings.fit_mode == "Fill both screens":
        canvas = ImageOps.fit(
            img,
            target_size,
            method=resampler,
            centering=(0.5, 0.5),
        )
    else:
        resized = ImageOps.contain(img, target_size, method=resampler)
        canvas = Image.new(
            'RGB',
            target_size,
            BACKGROUND_COLORS[settings.background],
        )
        paste_x = (target_size[0] - resized.width) // 2
        paste_y = (target_size[1] - resized.height) // 2
        canvas.paste(resized, (paste_x, paste_y))

    return enhance_page(canvas, settings.enhancement, asset_role)


def validate_lz10_payload(payload, expected_size):
    """Structurally validates one Nintendo/GBA LZ10 stream without trusting grit."""
    if len(payload) < 4 or payload[0] != 0x10:
        raise RuntimeError("grit did not produce an LZ10 stream")

    declared_size = payload[1] | (payload[2] << 8) | (payload[3] << 16)
    if declared_size != expected_size:
        raise RuntimeError(
            f"LZ10 header declares {declared_size} bytes; expected {expected_size}"
        )

    input_offset = 4
    output_offset = 0
    while output_offset < expected_size:
        if input_offset >= len(payload):
            raise RuntimeError("LZ10 stream ends before its flag byte")
        flags = payload[input_offset]
        input_offset += 1

        for mask in (0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01):
            if output_offset >= expected_size:
                break

            if flags & mask:
                if input_offset + 2 > len(payload):
                    raise RuntimeError("LZ10 stream ends inside a back-reference")
                first = payload[input_offset]
                second = payload[input_offset + 1]
                input_offset += 2
                length = (first >> 4) + 3
                displacement = ((first & 0x0F) << 8 | second) + 1
                if displacement > output_offset:
                    raise RuntimeError("LZ10 stream references data before its output")
                if output_offset + length > expected_size:
                    raise RuntimeError("LZ10 stream expands beyond its declared size")
                output_offset += length
            else:
                if input_offset >= len(payload):
                    raise RuntimeError("LZ10 stream ends inside a literal")
                input_offset += 1
                output_offset += 1

    trailing = payload[input_offset:]
    if len(trailing) > 3:
        raise RuntimeError("LZ10 stream contains too much trailing padding")


def decompress_lz10_payload(payload, expected_size):
    """Decodes a previously validated LZ10 stream (used for its small palette)."""
    validate_lz10_payload(payload, expected_size)
    output = bytearray()
    input_offset = 4
    while len(output) < expected_size:
        flags = payload[input_offset]
        input_offset += 1
        for mask in (0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01):
            if len(output) >= expected_size:
                break
            if flags & mask:
                first = payload[input_offset]
                second = payload[input_offset + 1]
                input_offset += 2
                length = (first >> 4) + 3
                displacement = ((first & 0x0F) << 8 | second) + 1
                for _ in range(length):
                    output.append(output[-displacement])
            else:
                output.append(payload[input_offset])
                input_offset += 1
    return bytes(output)


def create_page_asset(img, out_path, settings, target_size, asset_role):
    """Creates one versioned palette or RGB555 texture with LZ10 pixel data."""
    validate_preprocess_settings(settings)
    canvas = render_page_canvas(img, target_size, settings, asset_role)
    compact_palette = settings.storage == STORAGE_MODES[0]
    pixel_count = target_size[0] * target_size[1]
    expected_image_size = pixel_count if compact_palette else pixel_count * 2

    output_directory = os.path.dirname(out_path)
    grit_work_dir = tempfile.mkdtemp(prefix='.grit-', dir=output_directory)
    source_path = os.path.join(grit_work_dir, 'page.png')
    output_base = os.path.join(grit_work_dir, 'page')
    grit_image_output = output_base + '.img.bin'
    grit_palette_output = output_base + '.pal.bin'
    temporary_path = out_path + '.tmp'
    try:
        canvas.save(source_path, format='PNG', compress_level=1)
        command = [
            GRIT_EXECUTABLE_PATH,
            source_path,
            '-gb',
            '-gB8' if compact_palette else '-gB16',
        ]
        if not compact_palette:
            command.append('-gT!')
        command.extend([
            '-gzl',
            '-p' if compact_palette else '-p!',
        ])
        if compact_palette:
            command.append('-pzl')
        command.extend([
            '-m!',
            '-ftb',
            '-fh!',
            f'-o{output_base}',
            '-W1',
        ])
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            check=False,
        )
        if result.returncode != 0 or not os.path.isfile(grit_image_output):
            detail = result.stdout.strip()[-2000:]
            raise RuntimeError(
                f"grit compression failed with status {result.returncode}: {detail}"
            )

        with open(grit_image_output, 'rb') as compressed:
            image_payload = compressed.read()
        validate_lz10_payload(image_payload, expected_image_size)

        if compact_palette:
            if not os.path.isfile(grit_palette_output):
                raise RuntimeError("grit did not produce the required page palette")
            with open(grit_palette_output, 'rb') as compressed:
                palette_payload = compressed.read()
            palette = bytearray(decompress_lz10_payload(
                palette_payload,
                ASSET_PALETTE_SIZE_BYTES,
            ))
            # Bitmap backgrounds require bit 15 on every displayed color.
            for offset in range(0, len(palette), 2):
                color = palette[offset] | (palette[offset + 1] << 8) | 0x8000
                palette[offset] = color & 0xFF
                palette[offset + 1] = color >> 8
            asset_payload = ASSET_MAGIC_PALETTE8 + bytes(palette) + image_payload
        else:
            asset_payload = ASSET_MAGIC_RGB555 + image_payload

        with open(temporary_path, 'wb') as output:
            output.write(asset_payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, out_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        shutil.rmtree(grit_work_dir, ignore_errors=True)

    actual_size = os.path.getsize(out_path)
    if actual_size <= len(ASSET_MAGIC_RGB555) + 4:
        raise RuntimeError(
            f"Invalid compressed output size for {out_path}: {actual_size} bytes"
        )
    return actual_size


def process_image(img_path, out_chap_dir, idx, settings):
    """Creates supersampled detail and filtered preview versioned assets."""
    with Image.open(img_path) as source:
        img = ImageOps.exif_transpose(source).convert('RGB')

    rotation = ROTATIONS[settings.rotation]
    if rotation is not None:
        img = img.transpose(rotation)

    full_path = os.path.join(out_chap_dir, f"page{idx:03d}_full.dsm")
    preview_path = os.path.join(out_chap_dir, f"page{idx:03d}_preview.dsm")
    full_size = create_page_asset(
        img,
        full_path,
        settings,
        (PAGE_WIDTH, PAGE_HEIGHT),
        "detail",
    )
    preview_size = create_page_asset(
        img,
        preview_path,
        settings,
        (PREVIEW_WIDTH, PREVIEW_HEIGHT),
        "preview",
    )
    return full_size, preview_size


def write_book_manifest(output_dir, book_title, chapter_counts):
    """Writes the external-book contract consumed by source/main.cpp."""
    if not chapter_counts:
        raise ValueError("A book manifest requires at least one chapter")

    manifest_path = os.path.join(output_dir, 'book.cfg')
    with open(manifest_path, 'w', encoding='ascii', newline='\n') as manifest:
        manifest.write(f"{MANIFEST_MAGIC}\n")
        manifest.write(
            f"{PAGE_WIDTH} {PAGE_HEIGHT} {PREVIEW_WIDTH} {PREVIEW_HEIGHT}\n"
        )
        manifest.write(f"{book_title}\n")
        manifest.write(
            f"{LAST_POSITION_PREFIX}{int(chapter_counts[0][0]):04d}:0000000001\n"
        )
        for chapter, page_count in chapter_counts:
            manifest.write(f"{int(chapter):04d} {page_count}\n")
        manifest.flush()
        os.fsync(manifest.fileno())


def validate_built_rom(rom_path):
    """Rejects missing, truncated, or non-DSi output before reporting success."""
    if not os.path.isfile(rom_path):
        raise RuntimeError(f"Compiler did not create the expected ROM: {rom_path}")

    rom_size = os.path.getsize(rom_path)
    if rom_size < NDS_HEADER_SIZE:
        raise RuntimeError(
            f"Compiler created a truncated ROM ({rom_size} bytes): {rom_path}"
        )
    if rom_size > MAX_READER_ROM_SIZE_BYTES:
        raise RuntimeError(
            f"Reader ROM is unexpectedly large ({rom_size} bytes). External book "
            "files may have been embedded by mistake."
        )

    with open(rom_path, 'rb') as rom:
        header = rom.read(NDS_UNIT_CODE_OFFSET + 1)

    if len(header) <= NDS_UNIT_CODE_OFFSET or \
            header[NDS_UNIT_CODE_OFFSET] != DSI_ENHANCED_UNIT_CODE:
        raise RuntimeError(
            "Compiler output is not marked as DSi-enhanced software"
        )


def promote_staging_directory(staging_dir, destination):
    """Installs a generated book and returns its recoverable previous directory."""
    parent = os.path.dirname(destination)
    os.makedirs(parent, exist_ok=True)
    backup_dir = None

    if os.path.exists(destination):
        backup_dir = tempfile.mkdtemp(prefix='.book-backup-', dir=parent)
        os.rmdir(backup_dir)
        os.replace(destination, backup_dir)

    try:
        os.replace(staging_dir, destination)
    except Exception:
        if backup_dir and os.path.exists(backup_dir):
            os.replace(backup_dir, destination)
        raise

    return backup_dir


def restore_previous_directory(destination, backup_dir):
    """Rolls back a promoted directory after a failed package update."""
    if backup_dir and os.path.exists(backup_dir):
        if os.path.exists(destination):
            shutil.rmtree(destination)
        os.replace(backup_dir, destination)


def atomic_copy_file(source, destination):
    """Copies one file and exposes it only after the complete write succeeds."""
    parent = os.path.dirname(destination)
    os.makedirs(parent, exist_ok=True)
    handle, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(destination)}.",
        suffix='.tmp',
        dir=parent,
    )
    os.close(handle)
    try:
        shutil.copy2(source, temporary_path)
        with open(temporary_path, 'rb') as copied_file:
            os.fsync(copied_file.fileno())
        os.replace(temporary_path, destination)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


class PdfImportDialog(tk.Toplevel):
    """Small modal window to configure manga PDF import."""

    def __init__(self, parent, pdf_path, out_base):
        super().__init__(parent)
        self.title("Import PDF")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.pdf_path = pdf_path
        self.out_base = out_base
        self.result = None  # param dict if user confirms, None if cancelled

        pad = {'padx': 8, 'pady': 4}

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"File: {os.path.basename(pdf_path)}", font=("Consolas", 9, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, **pad
        )

        ttk.Label(frm, text="Chapter mode:").grid(row=1, column=0, sticky=tk.W, **pad)
        self.mode_var = tk.StringVar(value="single")
        mode_box = ttk.Combobox(
            frm, textvariable=self.mode_var, state="readonly", width=28,
            values=["single (one single chapter)", "split (every N pages)", "toc (PDF bookmarks)"],
        )
        mode_box.current(0)
        mode_box.grid(row=1, column=1, sticky=tk.W, **pad)
        mode_box.bind("<<ComboboxSelected>>", self._on_mode_change)
        self._mode_box = mode_box

        ttk.Label(frm, text="Pages per chapter:").grid(row=2, column=0, sticky=tk.W, **pad)
        self.pages_var = tk.StringVar(value="20")
        self.pages_entry = ttk.Entry(frm, textvariable=self.pages_var, width=10, state='disabled')
        self.pages_entry.grid(row=2, column=1, sticky=tk.W, **pad)

        ttk.Label(frm, text="First chapter (id):").grid(row=3, column=0, sticky=tk.W, **pad)
        self.start_chap_var = tk.StringVar(value="0010")
        ttk.Entry(frm, textvariable=self.start_chap_var, width=10).grid(row=3, column=1, sticky=tk.W, **pad)

        ttk.Label(frm, text="Image format:").grid(row=4, column=0, sticky=tk.W, **pad)
        self.format_var = tk.StringVar(value="jpg")
        ttk.Combobox(
            frm, textvariable=self.format_var, state="readonly", width=10,
            values=["jpg", "png", "webp"],
        ).grid(row=4, column=1, sticky=tk.W, **pad)

        self.raw_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm, variable=self.raw_var,
            text="Prefer embedded original image (if present) instead of re-rendering"
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, **pad)

        btn_bar = ttk.Frame(frm)
        btn_bar.grid(row=6, column=0, columnspan=2, sticky=tk.E, pady=(10, 0))
        ttk.Button(btn_bar, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_bar, text="Import", command=self._on_confirm).pack(side=tk.RIGHT, padx=4)

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window(self)

    def _on_mode_change(self, _event=None):
        mode = self._mode_box.current()
        self.pages_entry.configure(state='normal' if mode == 1 else 'disabled')

    def _on_confirm(self):
        mode_idx = self._mode_box.current()
        mode = ["single", "split", "toc"][mode_idx]

        try:
            start_chapter = int(self.start_chap_var.get())
            pages_per_chapter = int(self.pages_var.get()) if mode == "split" else None
        except ValueError:
            messagebox.showerror("Invalid value", "Check that chapter id and pages/chapter are integer numbers.")
            return

        if mode == "split" and (not pages_per_chapter or pages_per_chapter < 1):
            messagebox.showerror("Invalid value", "'Pages per chapter' must be an integer >= 1 in split mode.")
            return

        self.result = dict(
            mode=mode,
            pages_per_chapter=pages_per_chapter,
            start_chapter=start_chapter,
            img_format=self.format_var.get(),
            raw=self.raw_var.get(),
        )
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class DSComicViewerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DS-Mangaman")
        self.root.geometry("780x700")
        self.root.minsize(720, 620)

        self.supported_extensions = [
            '*.jpg', '*.jpeg', '*.png', '*.bmp', '*.webp',
            '*.tiff', '*.tif', '*.gif', '*.avif', '*.heic'
        ]

        self.src_base = os.path.join(BASE_DIR, 'assets', 'jpg_comic')
        self.package_root = os.path.join(BASE_DIR, 'build', 'sd_card')
        self.books_output_root = os.path.join(
            self.package_root,
            'ds-mangaman',
            'books',
        )

        os.makedirs(self.src_base, exist_ok=True)

        self.ui_setup()
        self.refresh_chapter_list()

    def ui_setup(self):
        style = ttk.Style()
        style.configure("Large.TButton", font=("Consolas", 11))

        top_bar = ttk.Frame(self.root, padding=6)
        top_bar.pack(side=tk.TOP, fill=tk.X)

        btn_import = ttk.Button(top_bar, text="📁 Import Img", style="Large.TButton", command=self.open_manga_dir)
        btn_import.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)

        self.btn_import_pdf = ttk.Button(
            top_bar, text="📕 Import PDF", style="Large.TButton", command=self.import_pdf_dialog
        )
        self.btn_import_pdf.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)

        btn_refresh = ttk.Button(top_bar, text="↻ Refresh", style="Large.TButton", command=self.refresh_chapter_list)
        btn_refresh.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)

        self.btn_compile = ttk.Button(
            top_bar,
            text="Export Book + Reader",
            style="Large.TButton",
            command=self.trigger_compilation,
        )
        self.btn_compile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)

        main_body = ttk.Frame(self.root)
        main_body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        right_frame = ttk.Frame(main_body, padding=6)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, expand=False)

        lbl_info = ttk.Label(
            right_frame,
            text="Chapters:"
        )
        lbl_info.pack(anchor=tk.W, pady=(0, 4))

        cols = ('chap_id', 'page_num')
        self.tree = ttk.Treeview(right_frame, columns=cols, show='headings', selectmode="extended")
        self.tree.heading('chap_id', text='ID')
        self.tree.heading('page_num', text='Pages')
        self.tree.column('chap_id', anchor=tk.CENTER, width=85)
        self.tree.column('page_num', anchor=tk.CENTER, width=85)

        scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        left_frame = ttk.Frame(main_body, padding=6)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        book_frame = ttk.LabelFrame(left_frame, text="External SD book", padding=8)
        book_frame.pack(fill=tk.X, pady=(0, 8))
        self.book_name_var = tk.StringVar()

        ttk.Label(book_frame, text="Book name:").grid(
            row=0, column=0, sticky=tk.W, padx=4, pady=3
        )
        ttk.Entry(
            book_frame,
            textvariable=self.book_name_var,
            width=34,
        ).grid(row=0, column=1, sticky=tk.EW, padx=4, pady=3)
        ttk.Label(
            book_frame,
            text="Copy the contents of build/sd_card to the root of the DSi SD card.",
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=4, pady=(4, 2))
        book_frame.columnconfigure(1, weight=1)

        options_frame = ttk.LabelFrame(left_frame, text="Page preprocessing", padding=8)
        options_frame.pack(fill=tk.X, pady=(0, 8))

        self.rotation_var = tk.StringVar(value="No rotation")
        self.fit_mode_var = tk.StringVar(value="Fit entire page")
        self.background_var = tk.StringVar(value="White")
        self.resampling_var = tk.StringVar(value="Lanczos")
        self.enhancement_var = tk.StringVar(value="Crisp text / manga")
        self.storage_var = tk.StringVar(value=STORAGE_MODES[0])

        ttk.Label(options_frame, text="Orientation:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Combobox(
            options_frame,
            textvariable=self.rotation_var,
            values=list(ROTATIONS),
            state="readonly",
            width=28,
        ).grid(row=0, column=1, sticky=tk.EW, padx=4, pady=3)

        ttk.Label(options_frame, text="Framing:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Combobox(
            options_frame,
            textvariable=self.fit_mode_var,
            values=["Fit entire page", "Fill both screens"],
            state="readonly",
            width=28,
        ).grid(row=1, column=1, sticky=tk.EW, padx=4, pady=3)

        ttk.Label(options_frame, text="Padding:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Combobox(
            options_frame,
            textvariable=self.background_var,
            values=list(BACKGROUND_COLORS),
            state="readonly",
            width=28,
        ).grid(row=2, column=1, sticky=tk.EW, padx=4, pady=3)

        ttk.Label(options_frame, text="Resize filter:").grid(row=3, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Combobox(
            options_frame,
            textvariable=self.resampling_var,
            values=list(RESAMPLERS),
            state="readonly",
            width=28,
        ).grid(row=3, column=1, sticky=tk.EW, padx=4, pady=3)

        ttk.Label(options_frame, text="Detail enhancement:").grid(row=4, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Combobox(
            options_frame,
            textvariable=self.enhancement_var,
            values=ENHANCEMENT_MODES,
            state="readonly",
            width=28,
        ).grid(row=4, column=1, sticky=tk.EW, padx=4, pady=3)

        ttk.Label(options_frame, text="Storage quality:").grid(row=5, column=0, sticky=tk.W, padx=4, pady=3)
        ttk.Combobox(
            options_frame,
            textvariable=self.storage_var,
            values=STORAGE_MODES,
            state="readonly",
            width=28,
        ).grid(row=5, column=1, sticky=tk.EW, padx=4, pady=3)

        ttk.Label(
            options_frame,
            text="Output: compressed 960 x 1440 detail + filtered 256 x 384 view.",
        ).grid(row=6, column=0, columnspan=2, sticky=tk.W, padx=4, pady=(7, 3))
        options_frame.columnconfigure(1, weight=1)

        lbl_console = ttk.Label(left_frame, text="Console Log:")
        lbl_console.pack(anchor=tk.W, pady=(0, 4))

        self.console = scrolledtext.ScrolledText(left_frame, font=("Consolas", 9), wrap=tk.WORD)
        self.console.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self.print_log(">>> Workspace Initialized...")

    def print_log(self, text):
        self.console.configure(state='normal')
        self.console.insert(tk.END, text + "\n")
        self.console.see(tk.END)
        self.console.configure(state='disabled')

    def post_log(self, text):
        """Schedules a log update safely from a background thread."""
        self.root.after(0, self.print_log, text)

    def set_compile_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        self.root.after(0, lambda: self.btn_compile.configure(state=state))

    def collect_chapter_images(self, chapter_dir):
        images = []
        for extension in self.supported_extensions:
            images.extend(glob.glob(os.path.join(chapter_dir, extension)))
            images.extend(glob.glob(os.path.join(chapter_dir, extension.upper())))
        return sorted(set(images), key=natural_sort_key)

    def open_manga_dir(self):
        """Displays localized filesystem structures inside system file explorer profiles."""
        target_path = os.path.abspath(self.src_base)
        self.print_log(f"[GUI] Opening active manga repository directory: {target_path}")
        if platform.system() == "Windows":
            os.startfile(target_path)
        else:
            subprocess.Popen(["xdg-open", target_path])

    def import_pdf_dialog(self):
        """Prompts the user for a PDF, gathers import options and starts the
        background conversion using pdf_importer.py."""
        pdf_path = filedialog.askopenfilename(
            title="Select a manga PDF",
            filetypes=[("PDF Files", "*.pdf"), ("All files", "*.*")],
        )
        if not pdf_path:
            return

        dialog = PdfImportDialog(self.root, pdf_path, self.src_base)
        if not dialog.result:
            self.print_log("[GUI] PDF import cancelled by the user.")
            return

        opts = dialog.result
        self.print_log(f">>> Starting PDF import: {os.path.basename(pdf_path)}")
        self.print_log(f"[PDF] Mode={opts['mode']}  format={opts['img_format']}  "
                       f"first_chapter={opts['start_chapter']:04d}  raw={opts['raw']}")

        self.btn_import_pdf.configure(state='disabled')
        threading.Thread(
            target=self.pdf_import_worker,
            args=(pdf_path, opts),
            daemon=True,
        ).start()

    def pdf_import_worker(self, pdf_path, opts):
        """Executes pdf_importer.import_pdf() outside the UI thread."""

        def progress_cb(done, total, message):
            # infrequent progress updates to avoid clogging the console
            if done == 1 or done == total or done % 5 == 0:
                self.root.after(0, self.print_log, f"[PDF] {message} ({done}/{total})")

        try:
            result = pdf_importer.import_pdf(
                pdf_path=pdf_path,
                out_base=self.src_base,
                mode=opts['mode'],
                pages_per_chapter=opts['pages_per_chapter'],
                start_chapter=opts['start_chapter'],
                img_format=opts['img_format'],
                raw=opts['raw'],
                progress_cb=progress_cb,
            )
        except pdf_importer.PdfImportError as exc:
            self.root.after(0, self.print_log, f"[ERROR] PDF import failed: {exc}")
            self.root.after(0, messagebox.showerror, "Import failed", str(exc))
        except Exception as exc:
            self.root.after(0, self.print_log, f"[ERROR] Unexpected error during PDF import: {exc}")
        else:
            for warn in result.warnings:
                self.root.after(0, self.print_log, f"[WARN] {warn}")
            for folder, n_pages in result.chapters:
                self.root.after(0, self.print_log, f"[PDF] Chapter generated: {folder}/ ({n_pages} pages)")
            self.root.after(0, self.print_log, "[PDF] Import completed.")
            self.root.after(0, self.refresh_chapter_list)
        finally:
            self.root.after(0, lambda: self.btn_import_pdf.configure(state='normal'))

    def refresh_chapter_list(self):
        """Parses active working paths and updates selection objects using 3-digit metrics."""
        for row in self.tree.get_children():
            self.tree.delete(row)

        if not os.path.exists(self.src_base):
            return

        folders = os.listdir(self.src_base)
        detected_valid = []

        for folder in folders:
            f_path = os.path.join(self.src_base, folder)
            if os.path.isdir(f_path) and len(folder) == 4 and folder.isdigit():
                total_pages = len(self.collect_chapter_images(f_path))
                detected_valid.append((folder, total_pages))

        detected_valid.sort(key=lambda x: x[0])

        for folder_id, pages_count in detected_valid:
            formatted_pages = f"{pages_count:03d} pages"
            self.tree.insert("", "end", values=(folder_id, formatted_pages))

        self.print_log(f"[GUI] Rendered inventory. Chapters: {len(detected_valid)}")

    def trigger_compilation(self):
        """Captures GUI state and starts an atomic external-book export."""
        all_items = self.tree.get_children()
        if not all_items:
            messagebox.showwarning("Execution Aborted", "No eligible manga folders detected inside your directory path source.")
            return

        try:
            book_title = normalize_book_title(self.book_name_var.get())
            book_id = make_book_id(book_title)
        except ValueError as exc:
            messagebox.showerror("Invalid book name", str(exc))
            return

        selected_rows = self.tree.selection()
        if selected_rows:
            target_chapters = sorted([str(self.tree.item(r)['values'][0]).zfill(4) for r in selected_rows])
            self.print_log(f"[GUI] User selected a custom subset of {len(target_chapters)} chapters.")
        else:
            msg = "No specific items selected. Export ALL discovered chapters into this book?"
            if messagebox.askyesno("Export Selection", msg):
                target_chapters = sorted([str(self.tree.item(r)['values'][0]).zfill(4) for r in all_items])
            else:
                return

        if len(target_chapters) > MAX_CHAPTERS:
            messagebox.showerror(
                "Too many chapters",
                f"A book can contain at most {MAX_CHAPTERS} selected chapters.",
            )
            return

        settings = PreprocessSettings(
            rotation=self.rotation_var.get(),
            fit_mode=self.fit_mode_var.get(),
            background=self.background_var.get(),
            resampling=self.resampling_var.get(),
            enhancement=self.enhancement_var.get(),
            storage=self.storage_var.get(),
        )

        try:
            validate_preprocess_settings(settings)
        except ValueError as exc:
            messagebox.showerror("Invalid preprocessing option", str(exc))
            return

        self.btn_compile.configure(state='disabled')
        threading.Thread(
            target=self.core_pipeline_worker,
            args=(target_chapters, settings, book_title, book_id),
            daemon=True
        ).start()

    def core_pipeline_worker(self, target_chapters, settings, book_title, book_id):
        """Builds a small reader and atomically exports one external SD book."""
        build_root = os.path.join(BASE_DIR, 'build')
        staging_parent = os.path.join(build_root, '.book-staging')
        os.makedirs(staging_parent, exist_ok=True)
        staging_dir = tempfile.mkdtemp(prefix=f'{book_id}-', dir=staging_parent)

        destination_book = os.path.join(self.books_output_root, book_id)
        had_previous_book = os.path.exists(destination_book)
        book_backup_dir = None
        book_promoted = False

        target_rom_path = 'build/DS-Mangaman'
        target_rom_file = os.path.join(BASE_DIR, f'{target_rom_path}.nds')
        package_rom_file = os.path.join(self.package_root, 'DS-Mangaman.nds')
        package_rom_backup = None
        package_rom_replaced = False

        self.post_log(">>> Initializing external-book export...")
        self.post_log(f"[BOOK] {book_title} -> {book_id}")
        self.post_log(f"[PIPELINE] Targeting chapters: {', '.join(target_chapters)}")
        self.post_log(
            f"[PREPROCESS] {PAGE_WIDTH}x{PAGE_HEIGHT}, rotation={settings.rotation}, "
            f"framing={settings.fit_mode}, padding={settings.background}, "
            f"filter={settings.resampling}, detail={settings.enhancement}, "
            f"storage={settings.storage} + LZ10"
        )

        try:
            os.makedirs(self.books_output_root, exist_ok=True)
            existing_books = [
                entry.name
                for entry in os.scandir(self.books_output_root)
                if is_safe_book_folder(entry.name) and entry.is_dir() and os.path.isfile(
                    os.path.join(entry.path, 'book.cfg')
                )
            ]
            if book_id not in existing_books and len(existing_books) >= MAX_BOOKS:
                raise RuntimeError(
                    f"The package already contains {MAX_BOOKS} books, which is the "
                    "reader's maximum. Remove one book before exporting another."
                )

            chapter_counts = []
            total_pages = 0
            total_asset_bytes = 0
            for chapter in target_chapters:
                src_dir = os.path.join(self.src_base, chapter)
                if not os.path.isdir(src_dir):
                    raise RuntimeError(f"Chapter directory disappeared: {src_dir}")

                image_list = self.collect_chapter_images(src_dir)
                if not image_list:
                    raise RuntimeError(f"Chapter {chapter} contains no supported page images")

                dst_dir = os.path.join(staging_dir, chapter)
                os.makedirs(dst_dir, exist_ok=False)
                self.post_log(
                    f">>> Transcoding chapter {chapter} [{len(image_list):03d} pages]..."
                )

                for page_number, image_path in enumerate(image_list, start=1):
                    try:
                        full_bytes, preview_bytes = process_image(
                            image_path,
                            dst_dir,
                            page_number,
                            settings,
                        )
                        total_asset_bytes += full_bytes + preview_bytes
                    except Exception as exc:
                        raise RuntimeError(
                            f"Chapter {chapter}, page {page_number} "
                            f"({os.path.basename(image_path)}): {exc}"
                        ) from exc

                chapter_counts.append((chapter, len(image_list)))
                total_pages += len(image_list)

            write_book_manifest(staging_dir, book_title, chapter_counts)
            raw_equivalent_bytes = total_pages * RAW_PAGE_ASSET_SIZE_BYTES
            saving_percent = 100.0 * (
                1.0 - total_asset_bytes / raw_equivalent_bytes
            )
            self.post_log(
                f"[PREPROCESS] Completed {total_pages} pages "
                f"({total_asset_bytes / (1024 * 1024):.2f} MiB, "
                f"{saving_percent:.1f}% smaller than raw RGB555)."
            )

            # Repackage the tiny reader every time so a stale, formerly embedded
            # ROM can never be mistaken for the external-book build.
            make_command = [
                MAKE_EXECUTABLE_PATH,
                '--always-make',
                f"TARGET={target_rom_path}",
            ]
            self.post_log(f"[PIPELINE] Building reusable reader: {' '.join(make_command)}")
            proc = subprocess.Popen(
                make_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                cwd=BASE_DIR,
                env=os.environ.copy(),
            )

            if proc.stdout is not None:
                for line in proc.stdout:
                    self.post_log(line.rstrip())

            return_code = proc.wait()
            if return_code != 0:
                raise RuntimeError(f"Compiler exited with status {return_code}")
            validate_built_rom(target_rom_file)

            book_backup_dir = promote_staging_directory(
                staging_dir,
                destination_book,
            )
            book_promoted = True
            staging_dir = None

            os.makedirs(self.package_root, exist_ok=True)
            if os.path.exists(package_rom_file):
                backup_handle, package_rom_backup = tempfile.mkstemp(
                    prefix='.DS-Mangaman.nds.',
                    suffix='.backup',
                    dir=self.package_root,
                )
                os.close(backup_handle)
                os.unlink(package_rom_backup)
                os.replace(package_rom_file, package_rom_backup)

            atomic_copy_file(target_rom_file, package_rom_file)
            package_rom_replaced = True
            validate_built_rom(package_rom_file)

            if package_rom_backup and os.path.exists(package_rom_backup):
                try:
                    os.unlink(package_rom_backup)
                except OSError as cleanup_error:
                    self.post_log(
                        f"[WARN] Could not remove the old reader backup: {cleanup_error}"
                    )
                package_rom_backup = None

            if book_backup_dir and os.path.exists(book_backup_dir):
                try:
                    shutil.rmtree(book_backup_dir)
                except OSError as cleanup_error:
                    self.post_log(
                        f"[WARN] Could not remove the old book backup: {cleanup_error}"
                    )
                book_backup_dir = None

            self.post_log(f"[PIPELINE] SD package ready: {self.package_root}")
            self.post_log(
                "[COPY] Copy DS-Mangaman.nds and the ds-mangaman folder to "
                "the root of the DSi SD card."
            )

            try:
                if platform.system() == "Windows":
                    os.startfile(self.package_root)
                elif shutil.which('xdg-open'):
                    subprocess.Popen(['xdg-open', self.package_root])
            except OSError as exc:
                self.post_log(f"[WARN] Could not open the SD package directory: {exc}")

        except Exception as err:
            try:
                if package_rom_backup and os.path.exists(package_rom_backup):
                    if os.path.exists(package_rom_file):
                        os.unlink(package_rom_file)
                    os.replace(package_rom_backup, package_rom_file)
                    package_rom_backup = None
                    self.post_log("[PIPELINE] Restored the previous packaged reader.")
                elif package_rom_replaced and os.path.exists(package_rom_file):
                    os.unlink(package_rom_file)
                    self.post_log("[PIPELINE] Removed the incomplete packaged reader.")
            except OSError as rollback_error:
                self.post_log(f"[ERROR] Reader rollback failed: {rollback_error}")

            if book_promoted:
                if book_backup_dir and os.path.exists(book_backup_dir):
                    try:
                        restore_previous_directory(destination_book, book_backup_dir)
                        book_backup_dir = None
                        self.post_log("[PIPELINE] Restored the previous exported book.")
                    except Exception as rollback_error:
                        self.post_log(f"[ERROR] Book rollback failed: {rollback_error}")
                elif not had_previous_book and os.path.exists(destination_book):
                    shutil.rmtree(destination_book)
                    self.post_log("[PIPELINE] Removed the incomplete exported book.")

            self.post_log(f"[ERROR] Export aborted: {err}")
            self.root.after(0, messagebox.showerror, "Export failed", str(err))

        finally:
            if staging_dir and os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)
            self.set_compile_enabled(True)


if __name__ == '__main__':
    ui_handle = tk.Tk()
    app_runner = DSComicViewerGUI(ui_handle)
    ui_handle.mainloop()
