#include <nds.h>
#include <stdio.h>
#include <string.h>
#include <filesystem.h>
#include <dirent.h>
#include <stdlib.h>
#include <ctype.h>

#define THUMB_W          256
#define THUMB_H          192
#define FULL_W           768
#define FULL_H           576
#define CROP_W           256
#define CROP_H           192

#define THUMB_SIZE       (THUMB_W * THUMB_H * 2)
#define FULL_SIZE        (FULL_W * FULL_H * 2)
#define DEBOUNCE_FRAMES  12 

static u16 thumb_buffer[THUMB_W * THUMB_H] __attribute__((aligned(4)));      
static u16 next_thumb_buffer[THUMB_W * THUMB_H] __attribute__((aligned(4))); 
static u16 high_res_buffer[FULL_W * FULL_H] __attribute__((aligned(4)));      
static u16 zoom_buffer[CROP_W * CROP_H] __attribute__((aligned(4)));          

static const float ZOOM_LEVELS[] = {1.5f, 2.0f, 2.5f, 3.0f};
static int current_zoom_idx = 3; 

// ================= [Automated Dynamic Chapter Search Pool] =================
static int valid_chapters[256]; // Supports up to 256 chapters per ROM theoretically
static int total_chapters = 0;
static int current_chapter_idx = 0; // Index of the currently read chapter in the search pool

// Auto-scans the NitroFS root directory, filters and sorts all 4-digit chapter folders
void scan_and_sort_chapters() {
    DIR* dir = opendir("nitro:/");
    if (!dir) return;
    
    struct dirent* entry;
    total_chapters = 0;
    
    while ((entry = readdir(dir))) {
        // Filter folders whose names are strictly 4 characters long and completely numeric
        if (entry->d_type == DT_DIR) {
            char* name = entry->d_name;
            if (strlen(name) == 4 && isdigit(name[0]) && isdigit(name[1]) && isdigit(name[2]) && isdigit(name[3])) {
                valid_chapters[total_chapters++] = atoi(name);
                if (total_chapters >= 256) break;
            }
        }
    }
    closedir(dir);

    // Bubble sort to ensure chapter numbers are sorted in ascending order
    for (int i = 0; i < total_chapters - 1; i++) {
        for (int j = 0; j < total_chapters - i - 1; j++) {
            if (valid_chapters[j] > valid_chapters[j + 1]) {
                int temp = valid_chapters[j];
                valid_chapters[j] = valid_chapters[j + 1];
                valid_chapters[j + 1] = temp;
            }
        }
    }
}
// =========================================================================

bool is_file_exist(int chapter, int page, const char* suffix) {
    char file_path[64];
    // Core Fix: Dynamically match the 4-digit chapter folder path using %04d
    sprintf(file_path, "nitro:/%04d/page%03d_%s.bin", chapter, page, suffix);
    FILE* file = fopen(file_path, "rb");
    if (file) {
        fclose(file);
        return true;
    }
    return false;
}

void update_hd_crop_scaled(int center_x, int center_y, int bg_top) {
    float multiplier = ZOOM_LEVELS[current_zoom_idx];
    int crop_w = (int)(768.0f / multiplier);
    int crop_h = (crop_w * 3) / 4; 

    int hd_cx = center_x * 3;
    int hd_cy = center_y * 3;

    int start_x = hd_cx - (crop_w / 2);
    int start_y = hd_cy - (crop_h / 2);

    if (start_x < 0) start_x = 0;
    if (start_x > (FULL_W - crop_w)) start_x = FULL_W - crop_w;
    if (start_y < 0) start_y = 0;
    if (start_y > (FULL_H - crop_h)) start_y = FULL_H - crop_h;

    uint32_t step_x = (crop_w << 16) / 256;
    uint32_t step_y = (crop_h << 16) / 192; 

    uint32_t curr_y_fp = 0;
    for (int y = 0; y < 192; y++) {
        int tex_y = start_y + (curr_y_fp >> 16);
        u16* src_row = &high_res_buffer[tex_y * FULL_W];
        u16* dst_row = &zoom_buffer[y * CROP_W];

        uint32_t curr_x_fp = 0;
        for (int x = 0; x < 256; x++) {
            dst_row[x] = src_row[start_x + (curr_x_fp >> 16)];
            curr_x_fp += step_x;
        }
        curr_y_fp += step_y;
    }

    DC_FlushRange(zoom_buffer, THUMB_SIZE);
    dmaCopy(zoom_buffer, bgGetGfxPtr(bg_top), THUMB_SIZE);
}

void load_comic_page_double_view(int chapter, int page, int bg_top, int bg_bottom) {
    char path[64];

    sprintf(path, "nitro:/%04d/page%03d_full.bin", chapter, page);
    FILE* f_full = fopen(path, "rb");
    if (f_full) {
        fread(high_res_buffer, 1, FULL_SIZE, f_full);
        fclose(f_full);
    } else {
        dmaFillWords(0, high_res_buffer, FULL_SIZE);
    }

    sprintf(path, "nitro:/%04d/page%03d_thumb.bin", chapter, page);
    FILE* f_thumb = fopen(path, "rb");
    if (f_thumb) {
        fread(thumb_buffer, 1, THUMB_SIZE, f_thumb);
        fclose(f_thumb);
    } else {
        dmaFillWords(0, thumb_buffer, THUMB_SIZE);
    }

    if (is_file_exist(chapter, page + 1, "thumb")) {
        sprintf(path, "nitro:/%04d/page%03d_thumb.bin", chapter, page + 1);
        FILE* f_next = fopen(path, "rb");
        if (f_next) {
            fread(next_thumb_buffer, 1, THUMB_SIZE, f_next);
            fclose(f_next);
        }
    } else {
        dmaFillWords(0, next_thumb_buffer, THUMB_SIZE); 
    }

    DC_FlushRange(next_thumb_buffer, THUMB_SIZE);
    dmaCopy(next_thumb_buffer, bgGetGfxPtr(bg_top), THUMB_SIZE);      // Top screen = Next page preview

    DC_FlushRange(thumb_buffer, THUMB_SIZE);
    dmaCopy(thumb_buffer, bgGetGfxPtr(bg_bottom), THUMB_SIZE);     // Bottom screen = Current page main view
}

void draw_radar_rect_dynamic(u16* vram, int tx, int ty) {
    float multiplier = ZOOM_LEVELS[current_zoom_idx];
    int rw = (int)(256.0f / multiplier);
    int rh = (rw * 3) / 4; 
    
    int rx = tx - (rw / 2);
    int ry = ty - (rh / 2);

    if (rx < 0) rx = 0;
    if (rx > (THUMB_W - rw)) rx = THUMB_W - rw;
    if (ry < 0) ry = 0;
    if (ry > (THUMB_H - rh)) ry = THUMB_H - rh;

    u16 black = RGB15(0, 0, 0) | BIT(15);

    for (int x = rx; x < rx + rw; x++) {
        vram[ry * THUMB_W + x] = black;
        vram[(ry + rh - 1) * THUMB_W + x] = black;
    }
    for (int y = ry; y < ry + rh; y++) {
        vram[y * THUMB_W + rx] = black;
        vram[y * THUMB_W + (rx + rw - 1)] = black;
    }
}

int main(void) {
    if (!nitroFSInit(NULL)) {
        consoleDemoInit();
        perror("NitroFS initialization failed!");
        while(1) swiWaitForVBlank();
    }

    // Scan on boot to retrieve all valid chapter numbers
    scan_and_sort_chapters();
    
    if (total_chapters == 0) {
        consoleDemoInit();
        printf("Error: No valid 4-digit comic folders detected in NitroFS!");
        while(1) swiWaitForVBlank();
    }

    lcdMainOnBottom(); // Ensure bottom screen touch is accurately aligned

    videoSetMode(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG);
    int bg_touch_bottom = bgInit(3, BgType_Bmp16, BgSize_B16_256x256, 0, 0); // Bottom Screen (Main)

    videoSetModeSub(MODE_5_2D);
    vramSetBankC(VRAM_C_SUB_BG);
    int bg_view_top = bgInitSub(3, BgType_Bmp16, BgSize_B16_256x256, 0, 0);   // Top Screen (Sub)

    // Start automatically from the first valid chapter number retrieved
    current_chapter_idx = 0;
    int current_page = 1;

    load_comic_page_double_view(valid_chapters[current_chapter_idx], current_page, bg_view_top, bg_touch_bottom);

    touchPosition touch_pos;
    touch_pos.px = THUMB_W / 2;
    touch_pos.py = THUMB_H / 2;

    bool in_zoom_mode = false;
    int debounce_counter = 0;

    while(1) {
        scanKeys();
        u32 keys_down = keysDown();
        u32 keys_held = keysHeld();
        bool page_changed = false;

        if (keys_down & KEY_SELECT) { 
            if (current_zoom_idx < 3) current_zoom_idx++;
        }
        else if (keys_down & KEY_START) { 
            if (current_zoom_idx > 0) current_zoom_idx--;
        }

        // Core Control Flow Upgrade: Seamless non-sequential chapter switching based on pooled indexing
        if (keys_down & KEY_L) { // Next chapter
            if (current_chapter_idx < total_chapters - 1) {
                current_chapter_idx++; 
                current_page = 1; 
                page_changed = true;
            }
        }
        else if (keys_down & KEY_R) { // Previous chapter
            if (current_chapter_idx > 0) {
                current_chapter_idx--; 
                current_page = 1; 
                page_changed = true;
            }
        }
        else if (keys_down & KEY_UP) { // Next page
            if (is_file_exist(valid_chapters[current_chapter_idx], current_page + 1, "thumb")) {
                current_page++; page_changed = true;
            }
        }
        else if (keys_down & KEY_DOWN) { // Previous page
            if (current_page > 1) {
                current_page--; page_changed = true;
            }
        }

        if (page_changed) {
            in_zoom_mode = false;
            debounce_counter = 0;
            load_comic_page_double_view(valid_chapters[current_chapter_idx], current_page, bg_view_top, bg_touch_bottom);
        }
        else {
            u16* bottom_vram = bgGetGfxPtr(bg_touch_bottom);
            
            if (keys_held & KEY_TOUCH) {
                touchRead(&touch_pos);
                in_zoom_mode = true;
                debounce_counter = DEBOUNCE_FRAMES; 

                DC_FlushRange(thumb_buffer, THUMB_SIZE);
                dmaCopy(thumb_buffer, bottom_vram, THUMB_SIZE);
                
                update_hd_crop_scaled(touch_pos.px, touch_pos.py, bg_view_top);
                draw_radar_rect_dynamic(bottom_vram, touch_pos.px, touch_pos.py);
            }
            else {
                if (in_zoom_mode) {
                    debounce_counter--; 
                    
                    if (debounce_counter <= 0) {
                        in_zoom_mode = false;
                        DC_FlushRange(next_thumb_buffer, THUMB_SIZE);
                        dmaCopy(next_thumb_buffer, bgGetGfxPtr(bg_view_top), THUMB_SIZE);

                        DC_FlushRange(thumb_buffer, THUMB_SIZE);
                        dmaCopy(thumb_buffer, bottom_vram, THUMB_SIZE);
                    } else {
                        draw_radar_rect_dynamic(bottom_vram, touch_pos.px, touch_pos.py);
                    }
                }
            }
        }

        swiWaitForVBlank();
    }
    return 0;
}