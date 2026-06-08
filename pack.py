import os
import glob
import shutil
import subprocess
import struct
from PIL import Image
import numpy as np

def clear_output_directory(dir_path):
    """Removes all files and subdirectories inside the output folder to clear stale assets."""
    if os.path.exists(dir_path):
        print(f"[INFO] Clearing output directory: {dir_path}")
        for filename in os.listdir(dir_path):
            file_path = os.path.join(dir_path, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f"[ERROR] Failed to delete {file_path}. Reason: {e}")
    else:
        os.makedirs(dir_path)

def create_padded_bin_vectorized(img, target_w, target_h, out_path):
    """Resizes an image proportionally and converts it via vectorized NumPy arrays for speed."""
    orig_w, orig_h = img.size
    ratio = min(target_w / orig_w, target_h / orig_h)
    new_w = int(orig_w * ratio)
    new_h = int(orig_h * ratio)
    
    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (target_w, target_h), (0, 0, 0))
    
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    canvas.paste(img_resized, (paste_x, paste_y))
    
    # Cast canvas straight to a NumPy array to bypass interpreted Python loop logic
    img_array = np.array(canvas, dtype=np.uint32)
    
    # Compute channel values across the matrix using native C implementations
    r = img_array[:, :, 0] * 31 // 255
    g = img_array[:, :, 1] * 31 // 255
    b = img_array[:, :, 2] * 31 // 255
    
    # Pack channels into standard 16-bit RGB555 format with alpha bit enabled
    rgb555 = r | (g << 5) | (b << 10) | (1 << 15)
    
    # Fast convert to contiguous bytes slice
    bin_data = rgb555.astype('<u2').tobytes()
    
    with open(out_path, 'wb') as f:
        f.write(bin_data)

def process_image(img_path, out_chap_dir, idx):
    """Rotates raw images 90 degrees clockwise and executes fast asset creation."""
    img = Image.open(img_path).convert('RGB')
    img_cw = img.transpose(Image.Transpose.ROTATE_270)
    
    thumb_path = os.path.join(out_chap_dir, f"page{idx:03d}_thumb.bin")
    create_padded_bin_vectorized(img_cw, 256, 192, thumb_path)
    
    full_path = os.path.join(out_chap_dir, f"page{idx:03d}_full.bin")
    create_padded_bin_vectorized(img_cw, 768, 576, full_path)

def build_nds_project(start_code, end_code):
    src_base = 'build/jpg_comic'
    out_base = 'build/nitrofiles'
    build_target = f"build/{start_code}_{end_code}"
    
    if not os.path.exists(src_base):
        print(f"[ERROR] Source directory '{src_base}' does not exist.")
        return

    # Flush output workspace directories cleanly
    clear_output_directory(out_base)

    try:
        start_val = int(start_code)
        end_val = int(end_code)
    except ValueError:
        print("[ERROR] Chapter codes must be valid numeric sequences.")
        return

    if start_val > end_val:
        print("[ERROR] Starting chapter code cannot be greater than ending chapter code.")
        return

    all_dirs = os.listdir(src_base)
    chapters = []
    for d in all_dirs:
        dir_path = os.path.join(src_base, d)
        if os.path.isdir(dir_path) and len(d) == 4 and d.isdigit():
            val = int(d)
            if start_val <= val <= end_val:
                chapters.append(d)
    
    chapters.sort()

    if not chapters:
        print(f"[INFO] No valid 4-digit chapter directories found matching range {start_code} to {end_code}.")
        return

    print("\n" + "-"*50)
    print(f"[INFO] Transcoding selected 3x Ultra HD assets from chapter {start_code} to {end_code}...")
    
    for chap in chapters:
        src_chap_dir = os.path.join(src_base, chap)
        out_chap_dir = os.path.join(out_base, chap)
        
        if not os.path.exists(out_chap_dir):
            os.makedirs(out_chap_dir)
            
        files = []
        for ext in ['*.jpg', '*.jpeg', '*.png', '*.bmp']:
            files.extend(glob.glob(os.path.join(src_chap_dir, ext)))
            files.extend(glob.glob(os.path.join(src_chap_dir, ext.upper())))
        files = sorted(list(set(files)))
        
        if not files:
            print(f"[WARN] No images found inside source directory: {src_chap_dir}")
            continue
            
        print(f" Processing chapter {chap}, matching {len(files)} raw source images...")
        for idx, fpath in enumerate(files, start=1):
            process_image(fpath, out_chap_dir, idx)
            
    print("\n[INFO] Asset conversion complete. Initializing devkitARM system build toolchain...")
    try:
        # Override the TARGET variable dynamically via command line arguments
        subprocess.run(['make', f'TARGET={build_target}'], check=True)
        print(f"\n[SUCCESS] NDS project compiled natively under build name: {build_target}.nds")
    except subprocess.CalledProcessError:
        print("\n[ERROR] Compilation failed. Please check compiler output log information.")

def main():
    print("=================== NDS MANGA BUILD PIPELINE ===================")
    start_input = input("Enter starting chapter code (4 digits, e.g., 0010): ").strip()
    end_input = input("Enter ending chapter code (4 digits, e.g., 0100): ").strip()
    
    if len(start_input) != 4 or len(end_input) != 4 or not start_input.isdigit() or not end_input.isdigit():
        print("[ERROR] Invalid configuration. Both chapter codes must be strictly 4-digit numbers.")
        return

    build_nds_project(start_input, end_input)

if __name__ == '__main__':
    main()