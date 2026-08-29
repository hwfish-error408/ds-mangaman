# DS-Mangaman

DS-Mangaman is a DSi-only dual-screen comic and document reader. One page is
displayed across a logical 256 x 384 viewport, with the upper half on
the top LCD and the lower half on the touchscreen LCD.

Books are stored as external files on the SD card. The reusable reader ROM is
about 250 KiB, so launching a 300 MiB book no longer requires the loader to
scan a 300 MiB NitroFS ROM. Page textures use Nintendo LZ10 compression and are
expanded only after the selected page is read. At minimum zoom, the reader
loads only the small filtered preview; the detailed texture is loaded when you
zoom in.

## Controls

| Control | Action |
|---|---|
| Touch drag | Move the page directly |
| D-pad | Pan the view; the page moves opposite the pressed direction |
| X / Y | Next / previous page, crossing chapter boundaries |
| L | Rotate the image view 90 degrees clockwise |
| R | Rotate the image view 90 degrees counterclockwise |
| SELECT | Zoom in |
| START | Zoom out |
| START + SELECT | Open the book picker; B cancels |
| A | Reset to minimum zoom and center the page |
| B | Open the chapter-local page-number dialog |

The page can move outside the viewport. Areas beyond it are white, and motion
stops only when the page reaches the opposite viewport boundary.
The D-pad axes rotate with the image view, while touch dragging continues to
move the page directly under the finger.

## Export a book

Run `main_gui.py`, enter a **Book name**, choose the chapters and preprocessing
options, then click **Export Book + Reader**. The result is:

```text
build/sd_card/
├── DS-Mangaman.nds
└── ds-mangaman/
    └── books/
        └── example-book/
            ├── book.cfg
            ├── 0010/
            │   ├── page001_full.dsm
            │   └── page001_preview.dsm
            └── ...
```

Copy both `DS-Mangaman.nds` and the `ds-mangaman` folder to the root of the DSi
SD card. Launch `DS-Mangaman.nds` in DSi mode. If one valid book is present it
opens automatically; if several are present, choose one with the D-pad and A.

Exporting another name adds another book. Exporting the same normalized name
replaces only that book. The GUI stages and validates all pages before exposing
the replacement, and preserves the previous book and reader if export fails.

The compressed format uses the `DS_MANGAMAN_BOOK_V3_ASSET` manifest. Books made
with an older raw or LZ-only format must be exported again with the current
GUI; the reader rejects them instead of interpreting incompatible pixels.

Each `book.cfg` also contains a fixed-width resume entry such as:

```text
last_position=0010:0000000025
```

The first number is the chapter folder and the second is its page. After a page
loads successfully, the reader updates this entry on the SD card. Opening the
book again therefore resumes on that page. Older V3 books without the entry
still open at their first page and receive the entry after page navigation.

Do not launch an older, hundreds-of-megabytes per-book ROM: those files still
contain embedded page data and retain the old long startup behavior.

## Image sources

Source chapters live below `assets/jpg_comic/` in four-digit folders:

```text
assets/jpg_comic/
├── 0010/
│   ├── 1.jpg
│   ├── 2.jpg
│   └── 10.jpg
└── 0020/
    └── 1.png
```

Filenames are naturally sorted, so `2.jpg` precedes `10.jpg`. The PDF importer
can create these chapter folders from a PDF.

The recommended settings for text and line art are **Fit entire page**,
**White**, **Lanczos**, **Crisp text / manga**, and **Compact 256 colors**.
Every page produces:

- a gently sharpened 960 x 1440 detailed texture;
- a separately filtered 256 x 384 minimum-zoom preview;
- an LZ10-compressed stream for each texture.

Compact mode quantizes each page to its own 256-color palette before lossless
LZ10 compression. Full-color mode instead stores every texture as RGB555.

The former raw format always consumed 1.875 MiB per page. Compressed size now
depends on page contents. **Compact 256 colors**, the recommended default for
books and manga, uses a page-specific palette and occupied about 0.34 MiB for
the representative mathematical page used during validation—roughly 82% less
than the old raw assets. **Full color RGB555** retained all 15-bit colors and
occupied about 0.58 MiB for that page, roughly 69% less. A 426-page book with
similar content is therefore approximately 146 MiB in Compact mode or 247 MiB
in Full-color mode instead of about 799 MiB raw; exact results vary by page.

Maximum zoom bilinearly reduces a 320 x 480 crop to the 256 x 384 LCD viewport,
which smooths text edges while keeping the same apparent 3x magnification.

## Requirements and building

- A Nintendo DSi running the application in DSi mode.
- devkitPro with the NDS development group (devkitARM, libnds, Calico, libfat,
  ndstool, and grit).
- Python 3 with the packages in `requirements.txt`; Linux also needs its Tk
  package, commonly `python3-tk`.

The GUI discovers `DEVKITPRO`, `/opt/devkitpro`, and standard Windows devkitPro
locations. Repository paths containing spaces or parentheses are supported.

To build only the reusable reader from a shell:

```sh
make TARGET=build/DS-Mangaman
```

The reader mounts the SD card with libfat and looks only below
`/ds-mangaman/books`. It does not embed or scan the generated books during ROM
startup.
