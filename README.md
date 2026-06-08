# ds-mangaman

A high-performance, hardware-accelerated 3x Ultra-HD comic book reader for the Nintendo DS. It includes a vectorized Python preprocessing pipeline supporting a dedicated Chrome Extension to bypass modern anti-scraping walls.

---

## Features

* **3x Ultra-HD Rendering Matrix**: Converts source images into high-fidelity 768x576 assets, providing raw details that far exceed standard NDS screen limitations.
* **Dual-Screen Layout**: The bottom touch screen shows the current page in thumbnail view, while the top screen displays a preview of the upcoming page for a seamless transition.
* **Touch Radar Magnifier**: Pressing the stylus on the bottom screen instantly shifts the top screen into an ultra-high-resolution magnifying glass focused on the exact pixel coordinates, paired with a dynamic tracking bounding box.
* **Hardware-Accelerated Scaling**: Implements 16-bit fixed-point nearest-neighbor interpolations directly inside the ARM9 main loop, providing clean, high-performance image scaling without floating-point overhead.
* **Dynamic Chapter indexing**: The NDS runtime automatically scans, processes, and sorts 4-digit non-sequential chapter folders on boot via NitroFS, allowing irregular chapter progression (e.g., jumping from `0030` to `0032`).
* **Anti-Scraping Chrome Extension**: Extracts rendered DOM images directly inside the browser instance, completely bypassing Cloudflare, Captcha, or JavaScript obfuscation barriers.

---

## Project Structure

```text
ds-mangaman/
├── build/             # Temporary compiler object files
│   ├── jpg_comic/         # Local workspace for raw images downloaded via extension
│   │   ├── 0010/          # Chapter 1 (Strictly named 001.jpg, 002.jpg...)
│   │   └── 0032/          # Chapter 3b
│   └── nitrofiles/        # Compiled little-endian 16-bit binary matrix outputs
├── source/            # NDS C++ source files
│   └── main.cpp       # Main application execution, touch HUD & NitroFS control
├── Makefile           # Native devkitARM compilation configuration rules
└── pack.py            # NumPy-accelerated image converter & pipeline script\