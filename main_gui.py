import os
import sys
import glob
import shutil
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
from PIL import Image
import numpy as np

# Global storage for the verified paths
DEVKITPRO_ROOT = None
MAKE_EXECUTABLE_PATH = "make"  # Fallback to system path default

# ==========================================
# 1. PHYSICAL TOOLCHAIN & BINARY VALIDATION
# ==========================================
def verify_toolchain_infrastructure():
    """Validates the hardware toolchain footprint without overriding global environment profiles."""
    global DEVKITPRO_ROOT, MAKE_EXECUTABLE_PATH
    
    possible_roots = [
        'C:\\devkitPro',
        'C:\\devkitpro',
        'D:\\devkitPro',
        'd:\\devkitpro'
    ]
    
    for root in possible_roots:
        if os.path.exists(os.path.join(root, 'devkitARM')) and os.path.exists(os.path.join(root, 'libnds')):
            DEVKITPRO_ROOT = root
            break
            
    if DEVKITPRO_ROOT:
        # Resolve absolute location of the bundled MSYS2 make binary to prevent Windows PATH execution failures
        msys_make = os.path.join(DEVKITPRO_ROOT, 'msys2', 'usr', 'bin', 'make.exe')
        if os.path.exists(msys_make):
            MAKE_EXECUTABLE_PATH = msys_make
            print(f"[TOOLCHAIN] Found internal build utility: {MAKE_EXECUTABLE_PATH}")
        return True

    # Alert handling if the installation path cannot be verified
    root_win = tk.Tk()
    root_win.withdraw()
    
    installer_path = os.path.join('assets', 'devkitProUpdater-3.0.3.exe')
    
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

# Run verification checks prior to UI window allocation
verify_toolchain_infrastructure()


# ==========================================
# 2. IMAGE COMPRESSION PIPELINE (PACK.PY)
# ==========================================
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


# ==========================================
# 3. INTERACTIVE MANGA CONTEXT MANAGER GUI
# ==========================================
class DSComicViewerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DS-Mangaman")
        self.root.geometry("600x500")  # 锁定 500 * 500 分辨率
        self.root.minsize(600, 500)
        
        # Extended multi-format compatibility matrix
        self.supported_extensions = [
            '*.jpg', '*.jpeg', '*.png', '*.bmp', '*.webp', 
            '*.tiff', '*.tif', '*.gif', '*.avif', '*.heic'
        ]
        
        self.src_base = 'assets/jpg_comic'
        self.out_base = 'assets/nitrofiles'
        os.makedirs(self.src_base, exist_ok=True)
        os.makedirs(self.out_base, exist_ok=True)
        
        self.ui_setup()
        self.refresh_chapter_list()

    def ui_setup(self):
        # 创建统一样式，使顶部按钮文字加粗显得更大
        style = ttk.Style()
        style.configure("Large.TButton", font=("Consolas", 11))

        # ----------------------------------------------------
        # 【新架构】顶部大按钮区域：独占一整行，平分拉满宽度
        # ----------------------------------------------------
        top_bar = ttk.Frame(self.root, padding=6)
        top_bar.pack(side=tk.TOP, fill=tk.X)
        
        # 仅保留 📁 和 ↻ 表情，统一增加 ipady 让按钮体积显著变大
        btn_import = ttk.Button(top_bar, text="📁 Import", style="Large.TButton", command=self.open_manga_dir)
        btn_import.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)
        
        btn_refresh = ttk.Button(top_bar, text="↻ Refresh", style="Large.TButton", command=self.refresh_chapter_list)
        btn_refresh.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)
        
        # 去掉了原先的齿轮 ⚙️ 表情
        self.btn_compile = ttk.Button(top_bar, text="Gen ROM", style="Large.TButton", command=self.trigger_compilation)
        self.btn_compile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, ipady=8)

        # ----------------------------------------------------
        # 下半部分主容器：承载中下部的双栏分列
        # ----------------------------------------------------
        main_body = ttk.Frame(self.root)
        main_body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 右侧分列：章节管理数据表格 (固定紧凑宽度 170px)
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

        # 左侧分列：控制台终端日志输出 (横向动态填满剩余空间)
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
                text=True, shell=True
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
            else:
                self.print_log(f"\n[PIPELINE] Compiler chain broken. Exit code: {return_code}")
                
        except Exception as err:
            self.print_log(f"\n[ERROR] Failed to execute build process: {err}")
            
        self.btn_compile.configure(state='normal')


if __name__ == '__main__':
    ui_handle = tk.Tk()
    app_runner = DSComicViewerGUI(ui_handle)
    ui_handle.mainloop()