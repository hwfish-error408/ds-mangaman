import os
import sys
import shutil
import glob
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext, filedialog
from PIL import Image
import numpy as np

import pdf_importer

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEVKITPRO_ROOT = None
MAKE_EXECUTABLE_PATH = "make"


def verify_toolchain_infrastructure():
    """Validates the hardware toolchain footprint and injects standard paths into environment."""
    global DEVKITPRO_ROOT, MAKE_EXECUTABLE_PATH
    possible_roots = [ 'C:\\devkitPro', 'C:\\devkitpro', 'D:\\devkitPro', 'd:\\devkitpro' ]
    for root in possible_roots:
        if os.path.exists(os.path.join(root, 'devkitARM')) and os.path.exists(os.path.join(root, 'libnds')):
            DEVKITPRO_ROOT = root
            break

    if DEVKITPRO_ROOT:
        clean_root = DEVKITPRO_ROOT.replace('\\', '/')
        os.environ['DEVKITPRO'] = clean_root
        os.environ['DEVKITARM'] = f"{clean_root}/devkitARM"

        devkitarm_bin = f"{clean_root}/devkitARM/bin"
        msys2_bin = f"{clean_root}/msys2/usr/bin"
        tools_bin = f"{clean_root}/tools/bin"

        current_path = os.environ.get('PATH', '')
        toolchain_paths = [devkitarm_bin, msys2_bin, tools_bin]
        new_paths = [p for p in toolchain_paths if p not in current_path]
        if new_paths:
            os.environ['PATH'] = ";".join(new_paths) + ";" + current_path
            print(f"[GUI] Environment variable injected")

        msys_make = os.path.join(DEVKITPRO_ROOT, 'msys2', 'usr', 'bin', 'make.exe')
        if os.path.exists(msys_make):
            MAKE_EXECUTABLE_PATH = msys_make
            print(f"[GUI] Found internal build utility: {MAKE_EXECUTABLE_PATH}")

        return True

    root_win = tk.Tk()
    root_win.withdraw()
    installer_path = os.path.join(BASE_DIR, 'assets', 'devkitProUpdater-3.0.3.exe')
    alert_msg = (
        "devkitPro toolchain directory not detected on your local storage drives!\n\n"
        "Would you like to execute the bundled setup utility now?\n\n"
        f"Target Utility Path: {installer_path}"
    )
    execute_wizard = messagebox.askyesno("Toolchain Missing", alert_msg, icon='warning')
    if execute_wizard:
        if os.path.exists(installer_path):
            try:
                subprocess.Popen([installer_path], shell=True)
            except Exception as ex:
                messagebox.showerror("Execution Error", f"Failed to open the setup wizard:\n{ex}")
        else:
            messagebox.showerror("File Error", f"The installation file was not found at:\n{installer_path}")
    root_win.destroy()
    sys.exit(0)


verify_toolchain_infrastructure()


def clear_output_directory(dir_path):
    """Purges past matrix caches from the output workspace directory."""
    if os.path.exists(dir_path):
        for filename in os.listdir(dir_path):
            file_path = os.path.join(dir_path, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f"[ERROR] Clean task failure: {e}")
    else:
        os.makedirs(dir_path)


def create_padded_bin_vectorized(img, target_w, target_h, out_path):
    """Resizes coordinates using proportional layout anchors and outputs RGB555 structural arrays via NumPy."""
    orig_w, orig_h = img.size
    ratio = min(target_w / orig_w, target_h / orig_h)
    new_w = int(orig_w * ratio)
    new_h = int(orig_h * ratio)

    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new('RGB', (target_w, target_h), (0, 0, 0))
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    canvas.paste(img_resized, (paste_x, paste_y))

    img_array = np.array(canvas, dtype=np.uint32)
    r = img_array[:, :, 0] * 31 // 255
    g = img_array[:, :, 1] * 31 // 255
    b = img_array[:, :, 2] * 31 // 255
    rgb555 = r | (g << 5) | (b << 10) | (1 << 15)

    bin_data = rgb555.astype('<u2').tobytes()
    with open(out_path, 'wb') as f:
        f.write(bin_data)


def process_image(img_path, out_chap_dir, idx):
    """Transforms layout dimensions via clockwise rotations and writes targeted binary matrices."""
    img = Image.open(img_path).convert('RGB')
    img_cw = img.transpose(Image.Transpose.ROTATE_270)

    thumb_path = os.path.join(out_chap_dir, f"page{idx:03d}_thumb.bin")
    create_padded_bin_vectorized(img_cw, 256, 192, thumb_path)

    full_path = os.path.join(out_chap_dir, f"page{idx:03d}_full.bin")
    create_padded_bin_vectorized(img_cw, 768, 576, full_path)


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
        self.root.geometry("600x500")
        self.root.minsize(600, 500)

        self.supported_extensions = [
            '*.jpg', '*.jpeg', '*.png', '*.bmp', '*.webp',
            '*.tiff', '*.tif', '*.gif', '*.avif', '*.heic'
        ]

        self.src_base = os.path.join(BASE_DIR, 'assets', 'jpg_comic')
        self.out_base = os.path.join(BASE_DIR, 'assets', 'nitrofiles')

        os.makedirs(self.src_base, exist_ok=True)
        os.makedirs(self.out_base, exist_ok=True)

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

        self.btn_compile = ttk.Button(top_bar, text="Gen ROM", style="Large.TButton", command=self.trigger_compilation)
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
                matched_pics = []
                for extension in self.supported_extensions:
                    matched_pics.extend(glob.glob(os.path.join(f_path, extension)))
                    matched_pics.extend(glob.glob(os.path.join(f_path, extension.upper())))
                total_pages = len(sorted(list(set(matched_pics))))
                detected_valid.append((folder, total_pages))

        detected_valid.sort(key=lambda x: x[0])

        for folder_id, pages_count in detected_valid:
            formatted_pages = f"{pages_count:03d} pages"
            self.tree.insert("", "end", values=(folder_id, formatted_pages))

        self.print_log(f"[GUI] Rendered inventory. Chapters: {len(detected_valid)}")

    def trigger_compilation(self):
        """Dispatches operational data matrices inside background threads to prevent UI lockup."""
        all_items = self.tree.get_children()
        if not all_items:
            messagebox.showwarning("Execution Aborted", "No eligible manga folders detected inside your directory path source.")
            return

        selected_rows = self.tree.selection()
        if selected_rows:
            target_chapters = sorted([str(self.tree.item(r)['values'][0]).zfill(4) for r in selected_rows])
            self.print_log(f"[GUI] User selected a custom subset of {len(target_chapters)} chapters.")
        else:
            msg = "No specific items selected! Do you want to package ALL discovered chapters into the ROM?"
            if messagebox.askyesno("Compile Selection", msg):
                target_chapters = sorted([str(self.tree.item(r)['values'][0]).zfill(4) for r in all_items])
            else:
                return

        self.btn_compile.configure(state='disabled')
        threading.Thread(
            target=self.core_pipeline_worker,
            args=(target_chapters,),
            daemon=True
        ).start()

    def core_pipeline_worker(self, target_chapters):
        """Processes exactly the specified subset of folders and builds the ROM binary."""
        rom_identifier = f"{target_chapters[0]}_{target_chapters[-1]}"
        target_rom_path = f"build/manga_{rom_identifier}"

        self.print_log(f">>> Initializing Compilation Task...")
        self.print_log(f"[PIPELINE] Targeting Chapters: {', '.join(target_chapters)}")

        clear_output_directory(self.out_base)

        for chap in target_chapters:
            src_dir = os.path.join(self.src_base, chap)
            dst_dir = os.path.join(self.out_base, chap)

            if not os.path.exists(src_dir):
                continue

            os.makedirs(dst_dir, exist_ok=True)

            img_list = []
            for ext in self.supported_extensions:
                img_list.extend(glob.glob(os.path.join(src_dir, ext)))
                img_list.extend(glob.glob(os.path.join(src_dir, ext.upper())))
            img_list = sorted(list(set(img_list)))

            if not img_list:
                self.print_log(f"[WARN] No valid assets inside chapter folder: {chap}")
                continue

            self.print_log(f">>> Transcoding Chapter {chap} [{len(img_list):03d} pages found]...")

            for idx, img_path in enumerate(img_list, start=1):
                try:
                    process_image(img_path, dst_dir, idx)
                except Exception as ex:
                    self.print_log(f"[ERROR] Corrupted file skipped: {os.path.basename(img_path)}: {ex}")

        self.print_log("[PIPELINE] Asset matrices synchronized. Starting build system pipeline...")

        try:
            make_command = f'"{MAKE_EXECUTABLE_PATH}" TARGET={target_rom_path}'
            self.print_log(f"[PIPELINE] Dispatching command: {make_command}\n")

            proc = subprocess.Popen(
                make_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, shell=True, cwd=BASE_DIR
            )

            while True:
                line = proc.stdout.readline()
                if line == '' and proc.poll() is not None:
                    break
                if line:
                    self.print_log(line.strip())

            return_code = proc.poll()
            if return_code == 0:
                self.print_log(f"\n[PIPELINE] Target ROM binary built successfully: {target_rom_path}.nds")
                build_dir = os.path.abspath(os.path.join(BASE_DIR, 'build'))
                if platform.system() == "Windows":
                    os.startfile(build_dir)
                else:
                    subprocess.Popen(["xdg-open", build_dir])
            else:
                self.print_log(f"\n[PIPELINE] Compiler chain broken. Exit code: {return_code}")

        except Exception as err:
            self.print_log(f"\n[ERROR] Failed to execute build process: {err}")

        self.btn_compile.configure(state='normal')


if __name__ == '__main__':
    ui_handle = tk.Tk()
    app_runner = DSComicViewerGUI(ui_handle)
    ui_handle.mainloop()
