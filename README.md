# ds-mangaman

A high-performance, hardware-accelerated 3x Ultra-HD comic book reader for the Nintendo DS. It includes a Python preprocessing pipeline supporting a dedicated Chrome Extension to bypass modern anti-scraping walls.

---

通过完美的端到端架构，`ds-mangaman` 彻底打破了复古硬件的物理制约。在任天堂 DS 掌机上通过底层 C++ 定点数硬件加速，实现了前所未有的 3 倍超清双屏动态无损漫画阅读体验。这不仅是一次卓越的逆向性能调优，更是一套将现代高精数字化资产无缝注入老旧硬件的终极闭环解决方案。

---

`ds-mangaman` は、完璧なエンドツーエンドのアーキテクチャにより、レトロハードウェアの物理的制約を完全に打ち破ります。さらにニンテンドーDS実機上では、低層の C++ 固定小数点数によるハードウェア加速を通じて、かつてない 3倍超高画質のデュアルスクリーン動的マンガ閲覧体験を実現しました。これは単なるパフォーマンスの最適化に留まらず、旧世代のハードウェアに現代のデジタル資産の生命力を吹き込む、究極のソリューションです。

<div align="center">
  <img src="./demo.jpg" width="50%" alt="demo演示デモ">
</div>

## Features

* **3x Ultra-HD Rendering Matrix**: Converts source images into native 768x576 assets, providing raw details that far exceed standard NDS screen limitations.
* **Dual-Screen Layout**: The bottom touch screen shows the current page in thumbnail view, while the top screen displays a preview of the upcoming page for a seamless transition.
* **Touch Radar Magnifier**: Pressing the stylus on the bottom screen instantly shifts the top screen into an ultra-high-resolution magnifying glass focused on the exact pixel coordinates, paired with a dynamic tracking bounding box.
* **Hardware-Accelerated Scaling**: Implements 16-bit fixed-point nearest-neighbor interpolations directly inside the ARM9 main loop, providing clean, high-performance image scaling without floating-point overhead.
* **Dynamic Chapter indexing**: The NDS runtime automatically scans, processes, and sorts 4-digit non-sequential chapter folders on boot via NitroFS, allowing irregular chapter progression (e.g., jumping from `0030` to `0032`).

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
