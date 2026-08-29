#include <nds.h>

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <fat.h>

namespace {

constexpr int LOGICAL_HEIGHT = SCREEN_HEIGHT * 2;

constexpr int PAGE_WIDTH = 960;
constexpr int PAGE_HEIGHT = 1440;
constexpr int PAGE_PIXELS = PAGE_WIDTH * PAGE_HEIGHT;
constexpr size_t PAGE_BYTES = static_cast<size_t>(PAGE_PIXELS) * sizeof(u16);
constexpr size_t MAX_COMPRESSED_ASSET_BYTES =
    8 + PAGE_BYTES + (PAGE_BYTES + 7) / 8 + 4;

constexpr int PREVIEW_WIDTH = SCREEN_WIDTH;
constexpr int PREVIEW_HEIGHT = LOGICAL_HEIGHT;
constexpr int PREVIEW_PIXELS = PREVIEW_WIDTH * PREVIEW_HEIGHT;
constexpr size_t PREVIEW_BYTES = static_cast<size_t>(PREVIEW_PIXELS) * sizeof(u16);

constexpr int MAX_CHAPTERS = 256;
constexpr int MAX_BOOKS = 128;
constexpr int BOOK_FOLDER_CAPACITY = 49;
constexpr int BOOK_TITLE_CAPACITY = 49;
constexpr int PATH_CAPACITY = 192;
constexpr int PAN_STEP_PIXELS = 8;
constexpr int PAGE_INPUT_CAPACITY = 11;
constexpr int BOOK_PICKER_ROWS = 16;
constexpr int BOOK_SHORTCUT_GRACE_FRAMES = 4;
constexpr u32 BOOK_PICKER_CHORD = KEY_START | KEY_SELECT;
constexpr const char* MANIFEST_MAGIC = "DS_MANGAMAN_BOOK_V3_ASSET";
constexpr char LAST_POSITION_PREFIX[] = "last_position=";
constexpr size_t LAST_POSITION_PREFIX_LENGTH = sizeof(LAST_POSITION_PREFIX) - 1;
constexpr size_t LAST_POSITION_LINE_LENGTH =
    LAST_POSITION_PREFIX_LENGTH + 4 + 1 + 10;
constexpr size_t LAST_POSITION_RECORD_BYTES = LAST_POSITION_LINE_LENGTH + 1;
constexpr const char* BOOKS_ROOT = "/ds-mangaman/books";
constexpr size_t ASSET_MAGIC_SIZE = 8;
constexpr size_t ASSET_PALETTE_BYTES = 256 * sizeof(u16);
constexpr char ASSET_MAGIC_PALETTE8[] = "DSMP8L10";
constexpr char ASSET_MAGIC_RGB555[] = "DSM16L10";

struct Chapter {
    int number;
    int page_count;
};

struct Book {
    char folder[BOOK_FOLDER_CAPACITY];
    char title[BOOK_TITLE_CAPACITY];
};

struct CropSize {
    int width;
    int height;
};

struct ImageSize {
    int width;
    int height;
};

struct DragState {
    bool active;
    int touch_x;
    int touch_y;
    int starting_view_x;
    int starting_view_y;
};

// Every crop keeps the source's 2:3 aspect ratio, matching the combined 256x384 LCD area.
constexpr CropSize ZOOM_CROPS[] = {
    {960, 1440},
    {768, 1152},
    {640, 960},
    {480, 720},
    {384, 576},
    {320, 480},
};
constexpr int ZOOM_LEVEL_COUNT = sizeof(ZOOM_CROPS) / sizeof(ZOOM_CROPS[0]);

// These two full-page buffers require DSi RAM. Loading into the inactive buffer keeps
// the current page intact if the SD card contains a missing, truncated, or oversized asset.
static u16 page_buffers[2][PAGE_PIXELS] TWL_BSS __attribute__((aligned(32)));
static u16 preview_buffers[2][PREVIEW_PIXELS] TWL_BSS __attribute__((aligned(32)));
static u8 compressed_asset_buffer[MAX_COMPRESSED_ASSET_BYTES]
    TWL_BSS __attribute__((aligned(32)));
static u16 top_screen_buffer[SCREEN_WIDTH * SCREEN_HEIGHT] __attribute__((aligned(32)));
static u16 bottom_screen_buffer[SCREEN_WIDTH * SCREEN_HEIGHT] __attribute__((aligned(32)));

static Chapter chapters[MAX_CHAPTERS];
static Chapter manifest_scratch[MAX_CHAPTERS];
static Book books[MAX_BOOKS];
static int chapter_count = 0;
static int book_count = 0;
static int current_book_index = -1;
static int current_chapter_index = 0;
static int current_page = 1;
static int active_page_buffer = 0;
static bool full_page_loaded[2] = {};
static char current_book_path[PATH_CAPACITY];

static int zoom_index = 0;
static int view_x = 0;
static int view_y = 0;
// 0, 1, 2, and 3 represent 0, 90, 180, and 270 degrees clockwise.
static int rotation_quarters_clockwise = 0;

static int top_background = -1;
static int bottom_background = -1;

constexpr u16 opaque_rgb(int red, int green, int blue) {
    return static_cast<u16>(RGB15(red, green, blue) | BIT(15));
}

constexpr u16 COLOR_BLACK = opaque_rgb(0, 0, 0);
constexpr u16 COLOR_WHITE = opaque_rgb(31, 31, 31);
constexpr u16 COLOR_PANEL = opaque_rgb(4, 4, 5);
constexpr u16 COLOR_BUTTON = opaque_rgb(10, 10, 12);
constexpr u16 COLOR_ACCENT = opaque_rgb(8, 21, 31);
constexpr u16 COLOR_ERROR = opaque_rgb(31, 5, 5);

void fatal_error(const char* title, const char* detail = nullptr) {
    consoleDemoInit();
    iprintf("DS-Mangaman\n\n%s\n", title);
    if (detail != nullptr) {
        iprintf("\n%s\n", detail);
    }
    iprintf("\nPower off to exit.");

    while (true) {
        swiWaitForVBlank();
    }
}

int read_manifest_line(FILE* file, char* buffer, size_t capacity) {
    if (fgets(buffer, static_cast<int>(capacity), file) == nullptr) {
        return feof(file) ? 0 : -1;
    }

    size_t length = strlen(buffer);
    if (length > 0 && buffer[length - 1] == '\n') {
        buffer[--length] = '\0';
        if (length > 0 && buffer[length - 1] == '\r') {
            buffer[length - 1] = '\0';
        }
    } else if (!feof(file)) {
        // The line did not fit. Consume it so a caller cannot parse a partial record.
        int character = 0;
        do {
            character = fgetc(file);
        } while (character != '\n' && character != EOF);
        return -1;
    }

    return 1;
}

bool parse_integer_pair(const char* line, long* first, long* second) {
    const char* cursor = line;
    while (isspace(static_cast<unsigned char>(*cursor))) {
        ++cursor;
    }

    errno = 0;
    char* end = nullptr;
    const long parsed_first = strtol(cursor, &end, 10);
    if (end == cursor || errno == ERANGE) {
        return false;
    }

    cursor = end;
    while (isspace(static_cast<unsigned char>(*cursor))) {
        ++cursor;
    }

    errno = 0;
    const long parsed_second = strtol(cursor, &end, 10);
    if (end == cursor || errno == ERANGE) {
        return false;
    }

    cursor = end;
    while (isspace(static_cast<unsigned char>(*cursor))) {
        ++cursor;
    }
    if (*cursor != '\0') {
        return false;
    }

    *first = parsed_first;
    *second = parsed_second;
    return true;
}

bool parse_integer_quad(
    const char* line,
    long* first,
    long* second,
    long* third,
    long* fourth
) {
    long* outputs[] = {first, second, third, fourth};
    const char* cursor = line;

    for (long* output : outputs) {
        while (isspace(static_cast<unsigned char>(*cursor))) {
            ++cursor;
        }

        errno = 0;
        char* end = nullptr;
        const long parsed = strtol(cursor, &end, 10);
        if (end == cursor || errno == ERANGE) {
            return false;
        }

        *output = parsed;
        cursor = end;
    }

    while (isspace(static_cast<unsigned char>(*cursor))) {
        ++cursor;
    }
    return *cursor == '\0';
}

bool valid_book_folder(const char* folder) {
    const size_t length = strlen(folder);
    if (length == 0 || length >= BOOK_FOLDER_CAPACITY) {
        return false;
    }

    for (size_t index = 0; index < length; ++index) {
        const unsigned char character = static_cast<unsigned char>(folder[index]);
        if (!isalnum(character) && character != '-' && character != '_') {
            return false;
        }
    }
    return true;
}

bool valid_book_title(const char* title) {
    const size_t length = strlen(title);
    if (length == 0 || length >= BOOK_TITLE_CAPACITY ||
        title[0] == ' ' || title[length - 1] == ' ') {
        return false;
    }

    bool contains_visible_character = false;
    for (size_t index = 0; index < length; ++index) {
        const unsigned char character = static_cast<unsigned char>(title[index]);
        if (character < 0x20 || character > 0x7e) {
            return false;
        }
        if (character != ' ') {
            contains_visible_character = true;
        }
    }
    return contains_visible_character;
}

bool parse_last_position_line(
    const char* line,
    int* chapter_number,
    int* page_number
) {
    if (line == nullptr || chapter_number == nullptr || page_number == nullptr ||
        strlen(line) != LAST_POSITION_LINE_LENGTH ||
        memcmp(line, LAST_POSITION_PREFIX, LAST_POSITION_PREFIX_LENGTH) != 0 ||
        line[LAST_POSITION_PREFIX_LENGTH + 4] != ':') {
        return false;
    }

    int parsed_chapter = 0;
    for (size_t index = 0; index < 4; ++index) {
        const char character = line[LAST_POSITION_PREFIX_LENGTH + index];
        if (character < '0' || character > '9') {
            return false;
        }
        parsed_chapter = parsed_chapter * 10 + character - '0';
    }

    int parsed_page = 0;
    const size_t page_offset = LAST_POSITION_PREFIX_LENGTH + 5;
    for (size_t index = 0; index < 10; ++index) {
        const char character = line[page_offset + index];
        if (character < '0' || character > '9') {
            return false;
        }
        const int digit = character - '0';
        if (parsed_page > (INT_MAX - digit) / 10) {
            return false;
        }
        parsed_page = parsed_page * 10 + digit;
    }
    if (parsed_page < 1) {
        return false;
    }

    *chapter_number = parsed_chapter;
    *page_number = parsed_page;
    return true;
}

bool parse_book_manifest(
    const char* book_path,
    char* title_output,
    size_t title_capacity,
    Chapter* chapter_output,
    int* parsed_chapter_count,
    int* resume_chapter_index_output = nullptr,
    int* resume_page_output = nullptr
) {
    if (book_path == nullptr || title_output == nullptr || title_capacity == 0 ||
        chapter_output == nullptr || parsed_chapter_count == nullptr) {
        return false;
    }

    *parsed_chapter_count = 0;
    title_output[0] = '\0';
    if (resume_chapter_index_output != nullptr) {
        *resume_chapter_index_output = 0;
    }
    if (resume_page_output != nullptr) {
        *resume_page_output = 1;
    }

    char manifest_path[PATH_CAPACITY];
    const int path_length = snprintf(
        manifest_path,
        sizeof(manifest_path),
        "%s/book.cfg",
        book_path
    );
    if (path_length < 0 || path_length >= static_cast<int>(sizeof(manifest_path))) {
        return false;
    }

    FILE* file = fopen(manifest_path, "rb");
    if (file == nullptr) {
        return false;
    }

    char line[128];
    bool valid = true;
    long width = 0;
    long height = 0;
    long preview_width = 0;
    long preview_height = 0;

    if (read_manifest_line(file, line, sizeof(line)) != 1 ||
        strcmp(line, MANIFEST_MAGIC) != 0) {
        valid = false;
    }

    if (valid && read_manifest_line(file, line, sizeof(line)) != 1) {
        valid = false;
    }
    if (valid && (!parse_integer_quad(
            line,
            &width,
            &height,
            &preview_width,
            &preview_height
        ) ||
        width != PAGE_WIDTH || height != PAGE_HEIGHT ||
        preview_width != PREVIEW_WIDTH || preview_height != PREVIEW_HEIGHT)) {
        valid = false;
    }

    if (valid && read_manifest_line(file, line, sizeof(line)) != 1) {
        valid = false;
    }
    if (valid && (!valid_book_title(line) || strlen(line) >= title_capacity)) {
        valid = false;
    }
    if (valid) {
        strcpy(title_output, line);
    }

    int count = 0;
    int resume_chapter_number = -1;
    int resume_page_number = -1;
    while (valid) {
        const int line_result = read_manifest_line(file, line, sizeof(line));
        if (line_result == 0) {
            break;
        }
        if (line_result < 0) {
            valid = false;
            break;
        }

        const char* cursor = line;
        while (isspace(static_cast<unsigned char>(*cursor))) {
            ++cursor;
        }
        if (*cursor == '\0') {
            continue;
        }

        if (strncmp(
                cursor,
                LAST_POSITION_PREFIX,
                LAST_POSITION_PREFIX_LENGTH
            ) == 0) {
            int parsed_resume_chapter = 0;
            int parsed_resume_page = 0;
            if (parse_last_position_line(
                    cursor,
                    &parsed_resume_chapter,
                    &parsed_resume_page
                )) {
                // If a recoverable interrupted update left more than one resume
                // record, the final valid record is the newest one.
                resume_chapter_number = parsed_resume_chapter;
                resume_page_number = parsed_resume_page;
            }
            // A damaged resume record never makes the book itself unreadable.
            continue;
        }

        long chapter_number = 0;
        long pages = 0;
        if (!parse_integer_pair(line, &chapter_number, &pages) ||
            chapter_number < 0 || chapter_number > 9999 ||
            pages < 1 || pages > INT_MAX || count >= MAX_CHAPTERS ||
            (count > 0 && chapter_number <= chapter_output[count - 1].number)) {
            valid = false;
            break;
        }

        chapter_output[count].number = static_cast<int>(chapter_number);
        chapter_output[count].page_count = static_cast<int>(pages);
        ++count;
    }

    if (ferror(file)) {
        valid = false;
    }
    fclose(file);

    if (!valid || count == 0) {
        title_output[0] = '\0';
        return false;
    }

    *parsed_chapter_count = count;
    if (resume_chapter_index_output != nullptr || resume_page_output != nullptr) {
        for (int index = 0; index < count; ++index) {
            if (chapter_output[index].number == resume_chapter_number &&
                resume_page_number >= 1 &&
                resume_page_number <= chapter_output[index].page_count) {
                if (resume_chapter_index_output != nullptr) {
                    *resume_chapter_index_output = index;
                }
                if (resume_page_output != nullptr) {
                    *resume_page_output = resume_page_number;
                }
                break;
            }
        }
    }
    return true;
}

int compare_ascii_case_insensitive(const char* first, const char* second) {
    while (*first != '\0' && *second != '\0') {
        const int first_lower = tolower(static_cast<unsigned char>(*first));
        const int second_lower = tolower(static_cast<unsigned char>(*second));
        if (first_lower != second_lower) {
            return first_lower - second_lower;
        }
        ++first;
        ++second;
    }
    return static_cast<unsigned char>(*first) - static_cast<unsigned char>(*second);
}

int compare_books(const void* first_pointer, const void* second_pointer) {
    const Book* first = static_cast<const Book*>(first_pointer);
    const Book* second = static_cast<const Book*>(second_pointer);
    const int title_comparison = compare_ascii_case_insensitive(first->title, second->title);
    return title_comparison != 0 ? title_comparison : strcmp(first->folder, second->folder);
}

bool scan_books() {
    book_count = 0;
    DIR* directory = opendir(BOOKS_ROOT);
    if (directory == nullptr) {
        return false;
    }

    while (book_count < MAX_BOOKS) {
        dirent* entry = readdir(directory);
        if (entry == nullptr) {
            break;
        }
        if (!valid_book_folder(entry->d_name)) {
            continue;
        }

        char book_path[PATH_CAPACITY];
        const int path_length = snprintf(
            book_path,
            sizeof(book_path),
            "%s/%s",
            BOOKS_ROOT,
            entry->d_name
        );
        if (path_length < 0 || path_length >= static_cast<int>(sizeof(book_path))) {
            continue;
        }

        int parsed_count = 0;
        char title[BOOK_TITLE_CAPACITY];
        if (!parse_book_manifest(
                book_path,
                title,
                sizeof(title),
                manifest_scratch,
                &parsed_count
            )) {
            continue;
        }

        strcpy(books[book_count].folder, entry->d_name);
        strcpy(books[book_count].title, title);
        ++book_count;
    }

    closedir(directory);
    if (book_count > 1) {
        qsort(books, book_count, sizeof(books[0]), compare_books);
    }
    return book_count > 0;
}

void draw_book_picker(int selected_book, bool allow_cancel) {
    const int first_book = (selected_book / BOOK_PICKER_ROWS) * BOOK_PICKER_ROWS;
    int last_book = first_book + BOOK_PICKER_ROWS;
    if (last_book > book_count) {
        last_book = book_count;
    }

    iprintf("\x1b[2J\x1b[1;1H");
    iprintf("DS-Mangaman\n");
    iprintf("Books %d-%d of %d\n", first_book + 1, last_book, book_count);
    if (allow_cancel) {
        iprintf("D-pad: choose  A: open  B: back\n\n");
    } else {
        iprintf("D-pad: choose       A: open\n\n");
    }

    for (int index = first_book; index < last_book; ++index) {
        iprintf("%c %.29s\n", index == selected_book ? '>' : ' ', books[index].title);
    }
}

int choose_book(int initial_book = 0, bool allow_cancel = false) {
    if (book_count == 1 && !allow_cancel) {
        return 0;
    }

    int selected_book = initial_book;
    if (selected_book < 0 || selected_book >= book_count) {
        selected_book = 0;
    }
    keysSetRepeat(12, 3);
    draw_book_picker(selected_book, allow_cancel);

    while (true) {
        scanKeys();
        const u32 keys_down = keysDown();
        const u32 repeated_keys = keysDownRepeat();
        int next_book = selected_book;

        if (repeated_keys & KEY_UP) --next_book;
        if (repeated_keys & KEY_DOWN) ++next_book;
        if (repeated_keys & KEY_LEFT) next_book -= BOOK_PICKER_ROWS;
        if (repeated_keys & KEY_RIGHT) next_book += BOOK_PICKER_ROWS;

        if (next_book < 0) next_book = 0;
        if (next_book >= book_count) next_book = book_count - 1;
        if (next_book != selected_book) {
            selected_book = next_book;
            draw_book_picker(selected_book, allow_cancel);
        }

        if (keys_down & KEY_A) {
            return selected_book;
        }
        if (allow_cancel && (keys_down & KEY_B)) {
            return -1;
        }
        swiWaitForVBlank();
    }
}

bool activate_book(int book_index) {
    if (book_index < 0 || book_index >= book_count) {
        return false;
    }

    const int path_length = snprintf(
        current_book_path,
        sizeof(current_book_path),
        "%s/%s",
        BOOKS_ROOT,
        books[book_index].folder
    );
    if (path_length < 0 || path_length >= static_cast<int>(sizeof(current_book_path))) {
        return false;
    }

    char verified_title[BOOK_TITLE_CAPACITY];
    int verified_chapter_count = 0;
    int resume_chapter_index = 0;
    int resume_page = 1;
    if (!parse_book_manifest(
            current_book_path,
            verified_title,
            sizeof(verified_title),
            chapters,
            &verified_chapter_count,
            &resume_chapter_index,
            &resume_page
        )) {
        return false;
    }

    chapter_count = verified_chapter_count;
    current_book_index = book_index;
    current_chapter_index = resume_chapter_index;
    current_page = resume_page;
    return true;
}

bool persist_current_position() {
    if (current_chapter_index < 0 || current_chapter_index >= chapter_count ||
        current_page < 1 ||
        current_page > chapters[current_chapter_index].page_count) {
        return false;
    }

    char manifest_path[PATH_CAPACITY];
    const int path_length = snprintf(
        manifest_path,
        sizeof(manifest_path),
        "%s/book.cfg",
        current_book_path
    );
    if (path_length < 0 || path_length >= static_cast<int>(sizeof(manifest_path))) {
        return false;
    }

    char record[LAST_POSITION_RECORD_BYTES + 1];
    const int record_length = snprintf(
        record,
        sizeof(record),
        "%s%04d:%010d\n",
        LAST_POSITION_PREFIX,
        chapters[current_chapter_index].number,
        current_page
    );
    if (record_length != static_cast<int>(LAST_POSITION_RECORD_BYTES)) {
        return false;
    }

    FILE* file = fopen(manifest_path, "r+b");
    if (file == nullptr) {
        return false;
    }

    long record_offset = -1;
    char line[128];
    while (true) {
        const long line_offset = ftell(file);
        if (line_offset < 0 || fgets(line, sizeof(line), file) == nullptr) {
            break;
        }
        if (strncmp(line, LAST_POSITION_PREFIX, LAST_POSITION_PREFIX_LENGTH) == 0 &&
            strlen(line) == LAST_POSITION_RECORD_BYTES &&
            line[LAST_POSITION_RECORD_BYTES - 1] == '\n') {
            // Keep scanning so an earlier interrupted migration cannot cause a
            // stale duplicate after this record to override the new value.
            record_offset = line_offset;
        }
    }

    clearerr(file);
    bool valid = true;
    if (record_offset >= 0) {
        valid = fseek(file, record_offset, SEEK_SET) == 0;
    } else {
        valid = fseek(file, 0, SEEK_END) == 0;
        const long file_size = valid ? ftell(file) : -1;
        bool needs_newline = false;
        if (valid && file_size > 0) {
            valid = fseek(file, -1, SEEK_END) == 0;
            const int final_character = valid ? fgetc(file) : EOF;
            valid = valid && final_character != EOF;
            needs_newline = valid && final_character != '\n';
        }
        if (valid) {
            valid = fseek(file, 0, SEEK_END) == 0;
        }
        if (valid && needs_newline) {
            valid = fputc('\n', file) != EOF;
        }
    }

    if (valid) {
        valid = fwrite(record, 1, LAST_POSITION_RECORD_BYTES, file) ==
            LAST_POSITION_RECORD_BYTES;
    }
    if (valid) {
        valid = fflush(file) == 0;
    }
    if (fclose(file) != 0) {
        valid = false;
    }
    return valid;
}

bool read_compressed_asset_file(
    const char* path,
    size_t* compressed_size
) {
    FILE* file = fopen(path, "rb");
    if (file == nullptr) {
        return false;
    }

    bool valid = fseek(file, 0, SEEK_END) == 0;
    const long file_size = valid ? ftell(file) : -1;
    valid = valid && file_size >= 4 &&
        static_cast<unsigned long>(file_size) <= MAX_COMPRESSED_ASSET_BYTES &&
        fseek(file, 0, SEEK_SET) == 0;

    size_t bytes_read = 0;
    if (valid) {
        bytes_read = fread(
            compressed_asset_buffer,
            1,
            static_cast<size_t>(file_size),
            file
        );
        valid = bytes_read == static_cast<size_t>(file_size) && !ferror(file);
    }
    fclose(file);

    if (valid) {
        *compressed_size = bytes_read;
    }
    return valid;
}

bool decompress_lz10_asset(
    const u8* source,
    size_t source_size,
    void* destination_pointer,
    size_t expected_size
) {
    if (source == nullptr || destination_pointer == nullptr || source_size < 4 ||
        source[0] != 0x10) {
        return false;
    }

    const size_t declared_size = static_cast<size_t>(source[1]) |
        (static_cast<size_t>(source[2]) << 8) |
        (static_cast<size_t>(source[3]) << 16);
    if (declared_size != expected_size) {
        return false;
    }

    u8* destination = static_cast<u8*>(destination_pointer);
    size_t input_offset = 4;
    size_t output_offset = 0;

    while (output_offset < expected_size) {
        if (input_offset >= source_size) {
            return false;
        }
        const u8 flags = source[input_offset++];

        for (u8 mask = 0x80; mask != 0 && output_offset < expected_size; mask >>= 1) {
            if ((flags & mask) == 0) {
                if (input_offset >= source_size) {
                    return false;
                }
                destination[output_offset++] = source[input_offset++];
                continue;
            }

            if (input_offset + 2 > source_size) {
                return false;
            }
            const u8 first = source[input_offset++];
            const u8 second = source[input_offset++];
            const size_t length = static_cast<size_t>(first >> 4) + 3;
            const size_t displacement =
                ((static_cast<size_t>(first & 0x0F) << 8) | second) + 1;
            if (displacement > output_offset ||
                output_offset + length > expected_size) {
                return false;
            }

            for (size_t index = 0; index < length; ++index) {
                destination[output_offset] = destination[output_offset - displacement];
                ++output_offset;
            }
        }
    }

    const size_t trailing_size = source_size - input_offset;
    return trailing_size <= 3;
}

bool load_compressed_asset(
    const char* path,
    void* destination,
    size_t expected_size
) {
    size_t compressed_size = 0;
    if (!read_compressed_asset_file(path, &compressed_size)) {
        return false;
    }

    if (compressed_size >= ASSET_MAGIC_SIZE + 4 &&
        memcmp(
            compressed_asset_buffer,
            ASSET_MAGIC_RGB555,
            ASSET_MAGIC_SIZE
        ) == 0) {
        return decompress_lz10_asset(
            compressed_asset_buffer + ASSET_MAGIC_SIZE,
            compressed_size - ASSET_MAGIC_SIZE,
            destination,
            expected_size
        );
    }

    if (expected_size % sizeof(u16) != 0 ||
        compressed_size < ASSET_MAGIC_SIZE + ASSET_PALETTE_BYTES + 4 ||
        memcmp(
            compressed_asset_buffer,
            ASSET_MAGIC_PALETTE8,
            ASSET_MAGIC_SIZE
        ) != 0) {
        return false;
    }

    const size_t pixel_count = expected_size / sizeof(u16);
    const u8* palette = compressed_asset_buffer + ASSET_MAGIC_SIZE;
    const u8* index_stream = palette + ASSET_PALETTE_BYTES;
    const size_t index_stream_size = compressed_size -
        ASSET_MAGIC_SIZE - ASSET_PALETTE_BYTES;
    if (!decompress_lz10_asset(
            index_stream,
            index_stream_size,
            destination,
            pixel_count
        )) {
        return false;
    }

    // The decompressed indices occupy the first half of the u16 destination.
    // Expanding backwards is overlap-safe: each write touches only indices that
    // have already been read.
    u8* indices = static_cast<u8*>(destination);
    u16* pixels = static_cast<u16*>(destination);
    for (size_t index = pixel_count; index > 0; --index) {
        const u8 palette_index = indices[index - 1];
        const size_t palette_offset = static_cast<size_t>(palette_index) * 2;
        pixels[index - 1] = static_cast<u16>(
            palette[palette_offset] |
            (static_cast<u16>(palette[palette_offset + 1]) << 8) |
            BIT(15)
        );
    }
    return true;
}

bool format_page_path(
    char* path,
    size_t capacity,
    int chapter_index,
    int page_number,
    const char* asset_name
) {
    const int path_length = snprintf(
        path,
        capacity,
        "%s/%04d/page%03d_%s.dsm",
        current_book_path,
        chapters[chapter_index].number,
        page_number,
        asset_name
    );
    return path_length >= 0 && path_length < static_cast<int>(capacity);
}

bool load_full_page_asset(int buffer_index, int chapter_index, int page_number) {
    char path[PATH_CAPACITY];
    if (!format_page_path(
            path,
            sizeof(path),
            chapter_index,
            page_number,
            "full"
        )) {
        return false;
    }
    return load_compressed_asset(path, page_buffers[buffer_index], PAGE_BYTES);
}

bool load_page_asset(int chapter_index, int page_number) {
    if (chapter_index < 0 || chapter_index >= chapter_count ||
        page_number < 1 || page_number > chapters[chapter_index].page_count) {
        return false;
    }

    const int candidate_buffer = active_page_buffer ^ 1;
    full_page_loaded[candidate_buffer] = false;
    char path[PATH_CAPACITY];
    if (!format_page_path(
            path,
            sizeof(path),
            chapter_index,
            page_number,
            "preview"
        ) ||
        !load_compressed_asset(
            path,
            preview_buffers[candidate_buffer],
            PREVIEW_BYTES
        )) {
        return false;
    }

    if (zoom_index > 0) {
        if (!load_full_page_asset(candidate_buffer, chapter_index, page_number)) {
            return false;
        }
        full_page_loaded[candidate_buffer] = true;
    }

    active_page_buffer = candidate_buffer;
    return true;
}

bool ensure_full_page_loaded() {
    if (full_page_loaded[active_page_buffer]) {
        return true;
    }

    if (!load_full_page_asset(
            active_page_buffer,
            current_chapter_index,
            current_page
        )) {
        return false;
    }
    full_page_loaded[active_page_buffer] = true;
    return true;
}

int floor_divide(int value, int divisor) {
    if (value >= 0) {
        return value / divisor;
    }
    return -((-value + divisor - 1) / divisor);
}

ImageSize rotated_size(int width, int height, int rotation) {
    return (rotation & 1) != 0
        ? ImageSize{height, width}
        : ImageSize{width, height};
}

// Convert an integer pixel in the rotated image back to the stored, upright asset.
// The caller must first check the point against rotated_size().
inline void rotated_pixel_to_source(
    int rotated_x,
    int rotated_y,
    int source_width,
    int source_height,
    int rotation,
    int* source_x,
    int* source_y
) {
    switch (rotation) {
        case 1: // 90 degrees clockwise
            *source_x = rotated_y;
            *source_y = source_height - 1 - rotated_x;
            break;
        case 2: // 180 degrees
            *source_x = source_width - 1 - rotated_x;
            *source_y = source_height - 1 - rotated_y;
            break;
        case 3: // 90 degrees counterclockwise
            *source_x = source_width - 1 - rotated_y;
            *source_y = rotated_x;
            break;
        default:
            *source_x = rotated_x;
            *source_y = rotated_y;
            break;
    }
}

// View centers are represented at twice their real coordinate so a half-pixel
// center is not discarded before applying a quarter-turn transformation.
void rotated_edge_to_source_twice(
    int rotated_x_twice,
    int rotated_y_twice,
    int rotation,
    int* source_x_twice,
    int* source_y_twice
) {
    switch (rotation) {
        case 1:
            *source_x_twice = rotated_y_twice;
            *source_y_twice = 2 * PAGE_HEIGHT - rotated_x_twice;
            break;
        case 2:
            *source_x_twice = 2 * PAGE_WIDTH - rotated_x_twice;
            *source_y_twice = 2 * PAGE_HEIGHT - rotated_y_twice;
            break;
        case 3:
            *source_x_twice = 2 * PAGE_WIDTH - rotated_y_twice;
            *source_y_twice = rotated_x_twice;
            break;
        default:
            *source_x_twice = rotated_x_twice;
            *source_y_twice = rotated_y_twice;
            break;
    }
}

void source_edge_to_rotated_twice(
    int source_x_twice,
    int source_y_twice,
    int rotation,
    int* rotated_x_twice,
    int* rotated_y_twice
) {
    switch (rotation) {
        case 1:
            *rotated_x_twice = 2 * PAGE_HEIGHT - source_y_twice;
            *rotated_y_twice = source_x_twice;
            break;
        case 2:
            *rotated_x_twice = 2 * PAGE_WIDTH - source_x_twice;
            *rotated_y_twice = 2 * PAGE_HEIGHT - source_y_twice;
            break;
        case 3:
            *rotated_x_twice = source_y_twice;
            *rotated_y_twice = 2 * PAGE_WIDTH - source_x_twice;
            break;
        default:
            *rotated_x_twice = source_x_twice;
            *rotated_y_twice = source_y_twice;
            break;
    }
}

void clamp_view() {
    const CropSize crop = ZOOM_CROPS[zoom_index];
    const ImageSize page = rotated_size(
        PAGE_WIDTH,
        PAGE_HEIGHT,
        rotation_quarters_clockwise
    );
    // The virtual viewport may move one complete viewport beyond any page edge.
    // At the limit, the page touches the opposite screen boundary geometrically,
    // while all sampled LCD pixels may be the white background.
    const int min_x = -crop.width;
    const int min_y = -crop.height;
    const int max_x = page.width;
    const int max_y = page.height;

    if (view_x < min_x) view_x = min_x;
    if (view_y < min_y) view_y = min_y;
    if (view_x > max_x) view_x = max_x;
    if (view_y > max_y) view_y = max_y;
}

void center_view() {
    const CropSize crop = ZOOM_CROPS[zoom_index];
    const ImageSize page = rotated_size(
        PAGE_WIDTH,
        PAGE_HEIGHT,
        rotation_quarters_clockwise
    );
    view_x = floor_divide(page.width - crop.width, 2);
    view_y = floor_divide(page.height - crop.height, 2);
    clamp_view();
}

bool reset_zoom_and_center_view() {
    const int previous_zoom = zoom_index;
    const int previous_view_x = view_x;
    const int previous_view_y = view_y;

    zoom_index = 0;
    center_view();

    return zoom_index != previous_zoom ||
        view_x != previous_view_x ||
        view_y != previous_view_y;
}

bool select_page(int chapter_index, int page_number, bool save_position = true) {
    if (!load_page_asset(chapter_index, page_number)) {
        return false;
    }

    current_chapter_index = chapter_index;
    current_page = page_number;
    // A newly selected page keeps the current zoom but starts centered.
    center_view();
    if (save_position) {
        // Navigation remains usable if the SD card becomes read-only; the page
        // load succeeds even when its resume marker cannot be saved.
        persist_current_position();
    }
    return true;
}

bool load_saved_or_first_page() {
    const int resume_chapter_index = current_chapter_index;
    const int resume_page = current_page;
    if (select_page(resume_chapter_index, resume_page, false)) {
        return true;
    }

    if (resume_chapter_index == 0 && resume_page == 1) {
        return false;
    }
    if (!select_page(0, 1, false)) {
        return false;
    }

    // Repair a stale marker when a previously saved asset was removed.
    persist_current_position();
    return true;
}

bool switch_book_transactionally(int next_book_index) {
    if (next_book_index < 0 || next_book_index >= book_count) {
        return false;
    }
    if (next_book_index == current_book_index) {
        return true;
    }

    memcpy(manifest_scratch, chapters, sizeof(chapters));
    char previous_book_path[PATH_CAPACITY];
    strcpy(previous_book_path, current_book_path);
    const int previous_book_index = current_book_index;
    const int previous_chapter_count = chapter_count;
    const int previous_chapter_index = current_chapter_index;
    const int previous_page = current_page;
    const int previous_active_page_buffer = active_page_buffer;
    const bool previous_full_page_loaded_0 = full_page_loaded[0];
    const bool previous_full_page_loaded_1 = full_page_loaded[1];
    const int previous_zoom_index = zoom_index;
    const int previous_view_x = view_x;
    const int previous_view_y = view_y;
    const int previous_rotation = rotation_quarters_clockwise;

    bool switched = activate_book(next_book_index);
    if (switched) {
        // A book remembers its page only. Opening it starts from the predictable
        // upright, minimum-zoom centered view used at application startup.
        zoom_index = 0;
        rotation_quarters_clockwise = 0;
        switched = load_saved_or_first_page();
    }

    if (switched) {
        return true;
    }

    memcpy(chapters, manifest_scratch, sizeof(chapters));
    strcpy(current_book_path, previous_book_path);
    current_book_index = previous_book_index;
    chapter_count = previous_chapter_count;
    current_chapter_index = previous_chapter_index;
    current_page = previous_page;
    active_page_buffer = previous_active_page_buffer;
    full_page_loaded[0] = previous_full_page_loaded_0;
    full_page_loaded[1] = previous_full_page_loaded_1;
    zoom_index = previous_zoom_index;
    view_x = previous_view_x;
    view_y = previous_view_y;
    rotation_quarters_clockwise = previous_rotation;
    return false;
}

bool select_adjacent_page(int direction) {
    int chapter_index = current_chapter_index;
    int page_number = current_page + direction;

    if (direction > 0 &&
        page_number > chapters[chapter_index].page_count) {
        if (chapter_index + 1 >= chapter_count) {
            return false;
        }
        ++chapter_index;
        page_number = 1;
    } else if (direction < 0 && page_number < 1) {
        if (chapter_index == 0) {
            return false;
        }
        --chapter_index;
        page_number = chapters[chapter_index].page_count;
    }

    return select_page(chapter_index, page_number);
}

void change_zoom(int direction) {
    const int next_zoom = zoom_index + direction;
    if (next_zoom < 0 || next_zoom >= ZOOM_LEVEL_COUNT || next_zoom == zoom_index) {
        return;
    }

    const CropSize old_crop = ZOOM_CROPS[zoom_index];
    const int center_x_twice = view_x * 2 + old_crop.width;
    const int center_y_twice = view_y * 2 + old_crop.height;

    zoom_index = next_zoom;
    const CropSize new_crop = ZOOM_CROPS[zoom_index];
    view_x = floor_divide(center_x_twice - new_crop.width, 2);
    view_y = floor_divide(center_y_twice - new_crop.height, 2);
    clamp_view();
}

void rotate_view(int quarter_turns_clockwise) {
    const CropSize crop = ZOOM_CROPS[zoom_index];
    const int old_center_x_twice = view_x * 2 + crop.width;
    const int old_center_y_twice = view_y * 2 + crop.height;

    int source_center_x_twice = 0;
    int source_center_y_twice = 0;
    rotated_edge_to_source_twice(
        old_center_x_twice,
        old_center_y_twice,
        rotation_quarters_clockwise,
        &source_center_x_twice,
        &source_center_y_twice
    );

    rotation_quarters_clockwise =
        (rotation_quarters_clockwise + quarter_turns_clockwise + 4) & 3;

    int new_center_x_twice = 0;
    int new_center_y_twice = 0;
    source_edge_to_rotated_twice(
        source_center_x_twice,
        source_center_y_twice,
        rotation_quarters_clockwise,
        &new_center_x_twice,
        &new_center_y_twice
    );

    view_x = floor_divide(new_center_x_twice - crop.width, 2);
    view_y = floor_divide(new_center_y_twice - crop.height, 2);
    clamp_view();
}

bool pan_view(u32 repeated_keys) {
    const CropSize crop = ZOOM_CROPS[zoom_index];
    const int horizontal_step = (crop.width * PAN_STEP_PIXELS + SCREEN_WIDTH - 1) / SCREEN_WIDTH;
    const int vertical_step = (crop.height * PAN_STEP_PIXELS + LOGICAL_HEIGHT - 1) / LOGICAL_HEIGHT;
    const int old_x = view_x;
    const int old_y = view_y;

    // The D-pad moves the viewport, so the page moves in the opposite direction.
    // view_x/view_y use rotated-image coordinates, which makes these axes rotate
    // with the displayed page after L or R is pressed.
    if (repeated_keys & KEY_LEFT) view_x -= horizontal_step;
    if (repeated_keys & KEY_RIGHT) view_x += horizontal_step;
    if (repeated_keys & KEY_UP) view_y -= vertical_step;
    if (repeated_keys & KEY_DOWN) view_y += vertical_step;

    clamp_view();
    return view_x != old_x || view_y != old_y;
}

int scale_drag_delta(int pixel_delta, int source_extent, int screen_extent) {
    const int magnitude = pixel_delta < 0 ? -pixel_delta : pixel_delta;
    const int scaled = (magnitude * source_extent + screen_extent / 2) / screen_extent;
    return pixel_delta < 0 ? -scaled : scaled;
}

bool drag_view(DragState* drag_state) {
    touchPosition touch = {};
    if (!touchRead(&touch)) {
        drag_state->active = false;
        return false;
    }

    if (!drag_state->active) {
        drag_state->active = true;
        drag_state->touch_x = touch.px;
        drag_state->touch_y = touch.py;
        drag_state->starting_view_x = view_x;
        drag_state->starting_view_y = view_y;
        return false;
    }

    const CropSize crop = ZOOM_CROPS[zoom_index];
    const int old_x = view_x;
    const int old_y = view_y;
    const int touch_delta_x = static_cast<int>(touch.px) - drag_state->touch_x;
    const int touch_delta_y = static_cast<int>(touch.py) - drag_state->touch_y;

    // Subtracting the finger displacement makes the page follow the finger.
    view_x = drag_state->starting_view_x -
        scale_drag_delta(touch_delta_x, crop.width, SCREEN_WIDTH);
    view_y = drag_state->starting_view_y -
        scale_drag_delta(touch_delta_y, crop.height, LOGICAL_HEIGHT);
    clamp_view();
    return view_x != old_x || view_y != old_y;
}

void render_preview_slice(u16* destination, int logical_y_start) {
    const u16* source = preview_buffers[active_page_buffer];
    const ImageSize rotated_page = rotated_size(
        PAGE_WIDTH,
        PAGE_HEIGHT,
        rotation_quarters_clockwise
    );
    const ImageSize rotated_preview = rotated_size(
        PREVIEW_WIDTH,
        PREVIEW_HEIGHT,
        rotation_quarters_clockwise
    );
    const int preview_view_x = floor_divide(
        view_x * rotated_preview.width,
        rotated_page.width
    );
    const int preview_view_y = floor_divide(
        view_y * rotated_preview.height,
        rotated_page.height
    );

    for (int screen_y = 0; screen_y < SCREEN_HEIGHT; ++screen_y) {
        const int rotated_y = preview_view_y + logical_y_start + screen_y;
        u16* destination_row = &destination[screen_y * SCREEN_WIDTH];

        if (rotated_y < 0 || rotated_y >= rotated_preview.height) {
            for (int screen_x = 0; screen_x < SCREEN_WIDTH; ++screen_x) {
                destination_row[screen_x] = COLOR_WHITE;
            }
            continue;
        }

        for (int screen_x = 0; screen_x < SCREEN_WIDTH; ++screen_x) {
            const int rotated_x = preview_view_x + screen_x;
            if (rotated_x < 0 || rotated_x >= rotated_preview.width) {
                destination_row[screen_x] = COLOR_WHITE;
                continue;
            }

            int source_x = 0;
            int source_y = 0;
            rotated_pixel_to_source(
                rotated_x,
                rotated_y,
                PREVIEW_WIDTH,
                PREVIEW_HEIGHT,
                rotation_quarters_clockwise,
                &source_x,
                &source_y
            );
            destination_row[screen_x] = source[source_y * PREVIEW_WIDTH + source_x];
        }
    }
}

inline u16 sample_rotated_page_pixel(
    const u16* source,
    const ImageSize& rotated_page,
    int rotated_x,
    int rotated_y
) {
    if (rotated_x < 0 || rotated_x >= rotated_page.width ||
        rotated_y < 0 || rotated_y >= rotated_page.height) {
        return COLOR_WHITE;
    }

    int source_x = 0;
    int source_y = 0;
    rotated_pixel_to_source(
        rotated_x,
        rotated_y,
        PAGE_WIDTH,
        PAGE_HEIGHT,
        rotation_quarters_clockwise,
        &source_x,
        &source_y
    );
    return source[source_y * PAGE_WIDTH + source_x];
}

u16 bilinear_rgb555(
    u16 top_left,
    u16 top_right,
    u16 bottom_left,
    u16 bottom_right,
    u32 fraction_x,
    u32 fraction_y
) {
    const u32 inverse_x = 256 - fraction_x;
    const u32 inverse_y = 256 - fraction_y;
    const u32 weight_top_left = inverse_x * inverse_y;
    const u32 weight_top_right = fraction_x * inverse_y;
    const u32 weight_bottom_left = inverse_x * fraction_y;
    const u32 weight_bottom_right = fraction_x * fraction_y;

    u16 result = BIT(15);
    for (int shift = 0; shift <= 10; shift += 5) {
        const u32 channel =
            (((top_left >> shift) & 31) * weight_top_left +
             ((top_right >> shift) & 31) * weight_top_right +
             ((bottom_left >> shift) & 31) * weight_bottom_left +
             ((bottom_right >> shift) & 31) * weight_bottom_right +
             32768) >> 16;
        result |= static_cast<u16>(channel << shift);
    }
    return result;
}

void render_smooth_max_zoom_slice(u16* destination, int logical_y_start) {
    const CropSize crop = ZOOM_CROPS[zoom_index];
    const u16* source = page_buffers[active_page_buffer];
    const ImageSize rotated_page = rotated_size(
        PAGE_WIDTH,
        PAGE_HEIGHT,
        rotation_quarters_clockwise
    );
    const s32 x_step = (static_cast<s32>(crop.width) << 16) / SCREEN_WIDTH;
    const s32 y_step = (static_cast<s32>(crop.height) << 16) / LOGICAL_HEIGHT;

    // Map output pixel centers to source pixel centers. The -0.5 source-pixel
    // offset is the standard bilinear resize convention and makes the 960-wide
    // texture act as 1.25x supersampling at the apparent 3x maximum zoom.
    const s32 starting_x =
        view_x * (1 << 16) + x_step / 2 - (1 << 15);
    const s32 starting_y =
        view_y * (1 << 16) + logical_y_start * y_step + y_step / 2 - (1 << 15);

    for (int screen_y = 0; screen_y < SCREEN_HEIGHT; ++screen_y) {
        const s32 rotated_y_fixed = starting_y + screen_y * y_step;
        const int rotated_y = floor_divide(rotated_y_fixed, 1 << 16);
        const u32 fraction_y = static_cast<u32>(
            rotated_y_fixed - rotated_y * (1 << 16)
        ) >> 8;
        u16* destination_row = &destination[screen_y * SCREEN_WIDTH];

        s32 rotated_x_fixed = starting_x;
        for (int screen_x = 0; screen_x < SCREEN_WIDTH; ++screen_x) {
            const int rotated_x = floor_divide(rotated_x_fixed, 1 << 16);
            const u32 fraction_x = static_cast<u32>(
                rotated_x_fixed - rotated_x * (1 << 16)
            ) >> 8;

            const u16 top_left = sample_rotated_page_pixel(
                source,
                rotated_page,
                rotated_x,
                rotated_y
            );
            const u16 top_right = sample_rotated_page_pixel(
                source,
                rotated_page,
                rotated_x + 1,
                rotated_y
            );
            const u16 bottom_left = sample_rotated_page_pixel(
                source,
                rotated_page,
                rotated_x,
                rotated_y + 1
            );
            const u16 bottom_right = sample_rotated_page_pixel(
                source,
                rotated_page,
                rotated_x + 1,
                rotated_y + 1
            );
            destination_row[screen_x] = bilinear_rgb555(
                top_left,
                top_right,
                bottom_left,
                bottom_right,
                fraction_x,
                fraction_y
            );
            rotated_x_fixed += x_step;
        }
    }
}

void render_screen_slice(u16* destination, int logical_y_start) {
    if (zoom_index == 0) {
        render_preview_slice(destination, logical_y_start);
        return;
    }

    if (zoom_index == ZOOM_LEVEL_COUNT - 1) {
        render_smooth_max_zoom_slice(destination, logical_y_start);
        return;
    }

    const CropSize crop = ZOOM_CROPS[zoom_index];
    const u16* source = page_buffers[active_page_buffer];
    const ImageSize rotated_page = rotated_size(
        PAGE_WIDTH,
        PAGE_HEIGHT,
        rotation_quarters_clockwise
    );

    const u32 x_step = (static_cast<u32>(crop.width) << 16) / SCREEN_WIDTH;
    const u32 y_step = (static_cast<u32>(crop.height) << 16) / LOGICAL_HEIGHT;
    const u32 starting_y = static_cast<u32>(logical_y_start) * y_step + y_step / 2;

    for (int screen_y = 0; screen_y < SCREEN_HEIGHT; ++screen_y) {
        const int rotated_y = view_y + static_cast<int>(
            (starting_y + screen_y * y_step) >> 16
        );
        u16* destination_row = &destination[screen_y * SCREEN_WIDTH];

        if (rotated_y < 0 || rotated_y >= rotated_page.height) {
            for (int screen_x = 0; screen_x < SCREEN_WIDTH; ++screen_x) {
                destination_row[screen_x] = COLOR_WHITE;
            }
            continue;
        }

        u32 rotated_x_offset = x_step / 2;
        for (int screen_x = 0; screen_x < SCREEN_WIDTH; ++screen_x) {
            const int rotated_x = view_x + static_cast<int>(rotated_x_offset >> 16);
            if (rotated_x < 0 || rotated_x >= rotated_page.width) {
                destination_row[screen_x] = COLOR_WHITE;
                rotated_x_offset += x_step;
                continue;
            }

            int source_x = 0;
            int source_y = 0;
            rotated_pixel_to_source(
                rotated_x,
                rotated_y,
                PAGE_WIDTH,
                PAGE_HEIGHT,
                rotation_quarters_clockwise,
                &source_x,
                &source_y
            );
            destination_row[screen_x] = source[source_y * PAGE_WIDTH + source_x];
            rotated_x_offset += x_step;
        }
    }
}

void present_reader_view() {
    render_screen_slice(top_screen_buffer, 0);
    render_screen_slice(bottom_screen_buffer, SCREEN_HEIGHT);

    DC_FlushRange(top_screen_buffer, sizeof(top_screen_buffer));
    DC_FlushRange(bottom_screen_buffer, sizeof(bottom_screen_buffer));
    swiWaitForVBlank();
    dmaCopy(top_screen_buffer, bgGetGfxPtr(top_background), sizeof(top_screen_buffer));
    dmaCopy(bottom_screen_buffer, bgGetGfxPtr(bottom_background), sizeof(bottom_screen_buffer));
}

void fill_buffer(u16* buffer, u16 color) {
    for (int i = 0; i < SCREEN_WIDTH * SCREEN_HEIGHT; ++i) {
        buffer[i] = color;
    }
}

void fill_rectangle(u16* buffer, int x, int y, int width, int height, u16 color) {
    if (x < 0) {
        width += x;
        x = 0;
    }
    if (y < 0) {
        height += y;
        y = 0;
    }
    if (x + width > SCREEN_WIDTH) width = SCREEN_WIDTH - x;
    if (y + height > SCREEN_HEIGHT) height = SCREEN_HEIGHT - y;
    if (width <= 0 || height <= 0) return;

    for (int row = 0; row < height; ++row) {
        u16* destination = &buffer[(y + row) * SCREEN_WIDTH + x];
        for (int column = 0; column < width; ++column) {
            destination[column] = color;
        }
    }
}

void draw_rectangle(u16* buffer, int x, int y, int width, int height, u16 color) {
    fill_rectangle(buffer, x, y, width, 2, color);
    fill_rectangle(buffer, x, y + height - 2, width, 2, color);
    fill_rectangle(buffer, x, y, 2, height, color);
    fill_rectangle(buffer, x + width - 2, y, 2, height, color);
}

constexpr u8 DIGIT_FONT[10][7] = {
    {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E},
    {0x04, 0x0C, 0x14, 0x04, 0x04, 0x04, 0x1F},
    {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F},
    {0x1E, 0x01, 0x01, 0x0E, 0x01, 0x01, 0x1E},
    {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02},
    {0x1F, 0x10, 0x10, 0x1E, 0x01, 0x01, 0x1E},
    {0x0E, 0x10, 0x10, 0x1E, 0x11, 0x11, 0x0E},
    {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},
    {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E},
    {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x01, 0x0E},
};

void draw_digit(u16* buffer, int x, int y, int digit, int scale, u16 color) {
    if (digit < 0 || digit > 9 || scale < 1) return;

    for (int row = 0; row < 7; ++row) {
        for (int column = 0; column < 5; ++column) {
            if (DIGIT_FONT[digit][row] & (1U << (4 - column))) {
                fill_rectangle(
                    buffer,
                    x + column * scale,
                    y + row * scale,
                    scale,
                    scale,
                    color
                );
            }
        }
    }
}

int decimal_length(int value) {
    int length = 1;
    while (value >= 10) {
        value /= 10;
        ++length;
    }
    return length;
}

void draw_number(u16* buffer, int x, int y, int value, int scale, u16 color) {
    if (value < 0) return;
    const int length = decimal_length(value);
    const int advance = 6 * scale;
    int divisor = 1;
    for (int i = 1; i < length; ++i) divisor *= 10;

    for (int i = 0; i < length; ++i) {
        draw_digit(buffer, x + i * advance, y, (value / divisor) % 10, scale, color);
        divisor /= 10;
    }
}

void draw_backspace_icon(u16* buffer, int center_x, int center_y, u16 color) {
    fill_rectangle(buffer, center_x - 9, center_y - 1, 17, 3, color);
    for (int offset = 0; offset < 6; ++offset) {
        fill_rectangle(buffer, center_x - 9 + offset, center_y - 1 - offset, 2, 2, color);
        fill_rectangle(buffer, center_x - 9 + offset, center_y + 1 + offset, 2, 2, color);
    }
}

void draw_confirm_icon(u16* buffer, int center_x, int center_y, u16 color) {
    for (int offset = 0; offset < 6; ++offset) {
        fill_rectangle(buffer, center_x - 10 + offset, center_y + offset, 3, 3, color);
    }
    for (int offset = 0; offset < 10; ++offset) {
        fill_rectangle(buffer, center_x - 4 + offset, center_y + 5 - offset, 3, 3, color);
    }
}

void present_bottom_dialog() {
    DC_FlushRange(bottom_screen_buffer, sizeof(bottom_screen_buffer));
    swiWaitForVBlank();
    dmaCopy(bottom_screen_buffer, bgGetGfxPtr(bottom_background), sizeof(bottom_screen_buffer));
}

void draw_page_dialog(const char* input, bool invalid) {
    fill_buffer(bottom_screen_buffer, COLOR_BLACK);
    fill_rectangle(bottom_screen_buffer, 18, 5, 220, 182, COLOR_PANEL);
    draw_rectangle(
        bottom_screen_buffer,
        18,
        5,
        220,
        182,
        invalid ? COLOR_ERROR : COLOR_ACCENT
    );

    // The header shows the valid range as "1 - page_count".
    draw_number(bottom_screen_buffer, 30, 15, 1, 1, COLOR_WHITE);
    fill_rectangle(bottom_screen_buffer, 39, 18, 8, 2, COLOR_WHITE);
    draw_number(
        bottom_screen_buffer,
        51,
        15,
        chapters[current_chapter_index].page_count,
        1,
        COLOR_WHITE
    );

    const int input_length = static_cast<int>(strlen(input));
    if (input_length == 0) {
        fill_rectangle(bottom_screen_buffer, 112, 31, 32, 2, COLOR_WHITE);
    } else {
        const int scale = 2;
        const int total_width = input_length * 6 * scale - scale;
        const int start_x = (SCREEN_WIDTH - total_width) / 2;
        for (int i = 0; i < input_length; ++i) {
            draw_digit(bottom_screen_buffer, start_x + i * 6 * scale, 25, input[i] - '0', scale, COLOR_WHITE);
        }
    }

    constexpr int keypad_x = 32;
    constexpr int keypad_y = 54;
    constexpr int button_width = 60;
    constexpr int button_height = 28;
    constexpr int gap_x = 6;
    constexpr int gap_y = 4;

    for (int row = 0; row < 4; ++row) {
        for (int column = 0; column < 3; ++column) {
            const int x = keypad_x + column * (button_width + gap_x);
            const int y = keypad_y + row * (button_height + gap_y);
            const bool confirm_button = row == 3 && column == 2;
            fill_rectangle(
                bottom_screen_buffer,
                x,
                y,
                button_width,
                button_height,
                confirm_button ? COLOR_ACCENT : COLOR_BUTTON
            );
            draw_rectangle(bottom_screen_buffer, x, y, button_width, button_height, COLOR_WHITE);

            if (row < 3) {
                const int digit = row * 3 + column + 1;
                draw_digit(bottom_screen_buffer, x + 25, y + 7, digit, 2, COLOR_WHITE);
            } else if (column == 0) {
                draw_backspace_icon(bottom_screen_buffer, x + button_width / 2, y + button_height / 2, COLOR_WHITE);
            } else if (column == 1) {
                draw_digit(bottom_screen_buffer, x + 25, y + 7, 0, 2, COLOR_WHITE);
            } else {
                draw_confirm_icon(bottom_screen_buffer, x + button_width / 2, y + 10, COLOR_WHITE);
            }
        }
    }
}

int parse_page_input(const char* input) {
    if (*input == '\0') return 0;

    errno = 0;
    char* end = nullptr;
    const long page = strtol(input, &end, 10);
    if (errno == ERANGE || *end != '\0' || page < 1 ||
        page > chapters[current_chapter_index].page_count) {
        return 0;
    }
    return static_cast<int>(page);
}

int page_input_dialog() {
    char input[PAGE_INPUT_CAPACITY] = {};
    int input_length = 0;
    bool invalid = false;

    draw_page_dialog(input, false);
    present_bottom_dialog();

    while (true) {
        scanKeys();
        const u32 keys_down = keysDown();

        if (keys_down & KEY_B) {
            return 0;
        }

        bool redraw = false;
        bool confirm = (keys_down & KEY_A) != 0;

        if (keys_down & KEY_TOUCH) {
            touchPosition touch = {};
            if (touchRead(&touch)) {
                constexpr int keypad_x = 32;
                constexpr int keypad_y = 54;
                constexpr int button_width = 60;
                constexpr int button_height = 28;
                constexpr int gap_x = 6;
                constexpr int gap_y = 4;

                for (int row = 0; row < 4; ++row) {
                    for (int column = 0; column < 3; ++column) {
                        const int x = keypad_x + column * (button_width + gap_x);
                        const int y = keypad_y + row * (button_height + gap_y);
                        if (touch.px < x || touch.px >= x + button_width ||
                            touch.py < y || touch.py >= y + button_height) {
                            continue;
                        }

                        if (row < 3) {
                            if (input_length < PAGE_INPUT_CAPACITY - 1) {
                                input[input_length++] = static_cast<char>('1' + row * 3 + column);
                                input[input_length] = '\0';
                                redraw = true;
                            }
                        } else if (column == 0) {
                            if (input_length > 0) {
                                input[--input_length] = '\0';
                                redraw = true;
                            }
                        } else if (column == 1) {
                            if (input_length < PAGE_INPUT_CAPACITY - 1) {
                                input[input_length++] = '0';
                                input[input_length] = '\0';
                                redraw = true;
                            }
                        } else {
                            confirm = true;
                        }
                    }
                }
            }
        }

        if (confirm) {
            const int selected_page = parse_page_input(input);
            if (selected_page > 0) {
                return selected_page;
            }
            invalid = true;
            redraw = true;
        } else if (redraw) {
            invalid = false;
        }

        if (redraw) {
            draw_page_dialog(input, invalid);
            present_bottom_dialog();
        } else {
            swiWaitForVBlank();
        }
    }
}

void initialize_graphics() {
    lcdMainOnBottom();

    videoSetMode(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG);
    bottom_background = bgInit(3, BgType_Bmp16, BgSize_B16_256x256, 0, 0);

    videoSetModeSub(MODE_5_2D);
    vramSetBankC(VRAM_C_SUB_BG);
    top_background = bgInitSub(3, BgType_Bmp16, BgSize_B16_256x256, 0, 0);
}

void wait_for_picker_acknowledgement() {
    while (true) {
        scanKeys();
        if (keysDown() & (KEY_A | KEY_B)) {
            return;
        }
        swiWaitForVBlank();
    }
}

void open_runtime_book_picker() {
    consoleDemoInit();
    const int selected_book = choose_book(current_book_index, true);
    if (selected_book >= 0 && selected_book != current_book_index) {
        iprintf("\x1b[2J\x1b[1;1H");
        iprintf("DS-Mangaman\n\nLoading %.28s...\n", books[selected_book].title);
        if (!switch_book_transactionally(selected_book)) {
            iprintf("\nThe selected book or its saved page could not be loaded.\n");
            iprintf("The previous book is still open.\n\nA/B: return");
            wait_for_picker_acknowledgement();
        }
    }

    initialize_graphics();
    keysSetRepeat(10, 2);
    present_reader_view();
}

} // namespace

int main() {
    if (!isDSiMode()) {
        fatal_error(
            "DSi mode is required.",
            "Launch the ROM as DSi-enhanced software."
        );
    }
    setCpuClock(true);

    consoleDemoInit();
    iprintf("DS-Mangaman\n\nMounting SD card...\n");
    if (!fatInitDefault()) {
        fatal_error(
            "SD card initialization failed.",
            "Launch through a DSi SD-capable loader."
        );
    }

    iprintf("Scanning book manifests...\n");
    if (!scan_books()) {
        fatal_error(
            "No valid books were found.",
            "Copy ds-mangaman/books to the SD root."
        );
    }

    const int selected_book = choose_book();
    iprintf("\x1b[2J\x1b[1;1H");
    iprintf("DS-Mangaman\n\nLoading %.28s...\n", books[selected_book].title);
    if (!activate_book(selected_book)) {
        fatal_error(
            "The selected book is invalid.",
            "Export it again with the current GUI."
        );
    }
    if (!load_saved_or_first_page()) {
        fatal_error(
            "The saved or first page could not be loaded.",
            "Check the SD card files and page sizes."
        );
    }

    initialize_graphics();
    keysSetRepeat(10, 2);
    present_reader_view();
    DragState drag_state = {};
    int pending_zoom_direction = 0;
    int pending_zoom_frames = 0;
    bool book_shortcut_latched = false;

    while (true) {
        scanKeys();
        const u32 keys_down = keysDown();
        const u32 keys_held = keysHeld();
        const u32 repeated_keys = keysDownRepeat();
        bool redraw = false;
        bool view_rebased = false;
        bool dialog_opened = false;

        const bool book_chord_held =
            (keys_held & BOOK_PICKER_CHORD) == BOOK_PICKER_CHORD;
        if (!book_chord_held) {
            book_shortcut_latched = false;
        }
        if (book_chord_held && !book_shortcut_latched) {
            book_shortcut_latched = true;
            pending_zoom_direction = 0;
            pending_zoom_frames = 0;
            drag_state.active = false;
            open_runtime_book_picker();
            continue;
        }

        bool zoom_key_scheduled = false;
        if (keys_down & KEY_SELECT) {
            pending_zoom_direction = 1;
            pending_zoom_frames = BOOK_SHORTCUT_GRACE_FRAMES;
            zoom_key_scheduled = true;
        } else if (keys_down & KEY_START) {
            pending_zoom_direction = -1;
            pending_zoom_frames = BOOK_SHORTCUT_GRACE_FRAMES;
            zoom_key_scheduled = true;
        }
        if ((keys_down & ~BOOK_PICKER_CHORD) != 0) {
            pending_zoom_direction = 0;
            pending_zoom_frames = 0;
        } else if (pending_zoom_direction != 0 && !zoom_key_scheduled) {
            --pending_zoom_frames;
        }

        if (pending_zoom_direction > 0 && pending_zoom_frames <= 0) {
            const int previous_zoom = zoom_index;
            if (zoom_index + 1 < ZOOM_LEVEL_COUNT && ensure_full_page_loaded()) {
                change_zoom(1);
            }
            if (zoom_index != previous_zoom) {
                redraw = true;
                view_rebased = true;
            }
            pending_zoom_direction = 0;
        } else if (pending_zoom_direction < 0 && pending_zoom_frames <= 0) {
            const int previous_zoom = zoom_index;
            change_zoom(-1);
            if (zoom_index != previous_zoom) {
                redraw = true;
                view_rebased = true;
            }
            pending_zoom_direction = 0;
        }
        if (keys_down & KEY_A) {
            if (reset_zoom_and_center_view()) {
                redraw = true;
                view_rebased = true;
            }
        }

        if (keys_down & KEY_X) {
            if (select_adjacent_page(1)) {
                redraw = true;
                view_rebased = true;
            }
        } else if (keys_down & KEY_Y) {
            if (select_adjacent_page(-1)) {
                redraw = true;
                view_rebased = true;
            }
        }

        if (keys_down & KEY_L) {
            rotate_view(1);
            redraw = true;
            view_rebased = true;
        } else if (keys_down & KEY_R) {
            rotate_view(-1);
            redraw = true;
            view_rebased = true;
        }

        if (keys_down & KEY_B) {
            drag_state.active = false;
            const int selected_page = page_input_dialog();
            if (selected_page > 0 && selected_page != current_page) {
                select_page(current_chapter_index, selected_page);
            }
            redraw = true;
            view_rebased = true;
            dialog_opened = true;
        }

        if (view_rebased) {
            drag_state.active = false;
        }

        if (!dialog_opened && (keys_held & KEY_TOUCH)) {
            redraw = drag_view(&drag_state) || redraw;
        } else {
            drag_state.active = false;
            if (!dialog_opened && !view_rebased) {
                redraw = pan_view(
                    repeated_keys & (KEY_LEFT | KEY_RIGHT | KEY_UP | KEY_DOWN)
                ) || redraw;
            }
        }

        if (redraw) {
            present_reader_view();
        } else {
            swiWaitForVBlank();
        }
    }
}
