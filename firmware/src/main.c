// Creator Micro 2 — custom firmware: individual control of the 13 per-key LEDs
// and the 8 underglow LEDs.
//
// ROOT CAUSE OF THE OLD "LEDs never light" BLOCKER (found by disassembling stock
// v0.6.1; addresses in ~/AI/worklouder/HANDOFF.md):
//
//   1. The addressable-LED VDD rail is gated by GPIO 36/37/38 (top-board power),
//      driven by wl_io::init_top_board_power_gpio (0x42008f38) to 37=1,36=0,38=1
//      at boot. GPIO36 is the strips' supply enable (active-high); stock only
//      raises it to 1 in its lights-enable path (0x4200aa78 -> gpio_set_level(36,1)).
//      Every previous custom attempt's GPIO "enable sweep" EXCLUDED 36 and 37 as
//      "PSRAM pins", so the LED rail was simply never powered.
//
//   2. Stock's power-OFF recipe (0x4201c788) drives 36/37/38 LOW and then latches
//      pad holds: gpio_hold_en(44) + gpio_deep_sleep_hold_en(). Those live in the
//      battery-backed RTC domain (RTC_CNTL_DIG_ISO 0x60008094 bit11 AUTOHOLD,
//      RTC_CNTL_DIG_PAD_HOLD 0x600080dc). After a stock power-off ALL digital pads
//      stay held across a flash/reset, so gpio_set_level is silently ignored until
//      the holds are released. Stock's app_main (0x42019880) releases them first
//      thing at boot; no custom firmware did. We replicate that here.
//
//   3. On the N16R8 module, octal PSRAM claims GPIO33-37 — so this build MUST keep
//      CONFIG_SPIRAM=n or GPIO36/37 can't be driven at all (see sdkconfig.defaults).
//
// Strip topology (read from stock's two led_strip_new_spi_device call sites):
//   keys     : GPIO 7, 13 LEDs, SPI2_HOST
//   underglow: GPIO 6,  8 LEDs, SPI3_HOST, GRB, WS2812
//
// Serial command protocol (newline-terminated, over the USB-Serial-JTAG console):
//   k <i> <rrggbb> | k all <rrggbb>   set key LED
//   u <i> <rrggbb> | u all <rrggbb>   set underglow LED
//   clear                             all off
//   demo                              per-key rainbow sweep
//   bright <0-255>                    global brightness scale
//   dump                              print inherited/live hold+GPIO register state
// Each command replies with a line starting "ok" or "err".

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "driver/ledc.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/gpio_reg.h"
#include "soc/io_mux_reg.h"
#include "led_strip.h"

static const char *TAG = "cm2";

#define KEY_GPIO   7
#define KEY_N      13
#define AMB_GPIO   6
#define AMB_N      8

// Top-board power-rail enables (GPIO36 = addressable-LED VDD enable).
#define RAIL_36    36
#define RAIL_37    37
#define RAIL_38    38

// Three single-colour status/"touch" LEDs by the touch pad, driven by LEDC PWM
// (stock: wl_io status-LED trio, 8-bit @5kHz on GPIO 35/45/48).
static const int STAT_GPIO[3] = { 35, 45, 48 };

static led_strip_handle_t s_keys, s_amb;
static uint8_t s_kbuf[KEY_N][3];   // stored RGB (pre-brightness)
static uint8_t s_abuf[AMB_N][3];
static uint8_t s_bright = 255;     // global scale, 0..255

// ---- register dump helpers -------------------------------------------------

static void dump_regs(const char *when)
{
    printf("regs[%s]: DIG_ISO(0x60008094)=0x%08lx PAD_HOLD(0x600080d8)=0x%08lx "
           "DIG_PAD_HOLD(0x600080dc)=0x%08lx\n",
           when,
           (unsigned long)REG_READ(RTC_CNTL_DIG_ISO_REG),
           (unsigned long)REG_READ(RTC_CNTL_PAD_HOLD_REG),
           (unsigned long)REG_READ(RTC_CNTL_DIG_PAD_HOLD_REG));
    printf("regs[%s]: GPIO_OUT=0x%08lx GPIO_OUT1=0x%08lx GPIO_ENABLE=0x%08lx "
           "GPIO_ENABLE1=0x%08lx\n",
           when,
           (unsigned long)REG_READ(GPIO_OUT_REG),
           (unsigned long)REG_READ(GPIO_OUT1_REG),
           (unsigned long)REG_READ(GPIO_ENABLE_REG),
           (unsigned long)REG_READ(GPIO_ENABLE1_REG));
    printf("regs[%s]: IO_MUX g6=0x%08lx g7=0x%08lx g36=0x%08lx g37=0x%08lx g38=0x%08lx\n",
           when,
           (unsigned long)REG_READ(IO_MUX_GPIO6_REG),
           (unsigned long)REG_READ(IO_MUX_GPIO7_REG),
           (unsigned long)REG_READ(IO_MUX_GPIO36_REG),
           (unsigned long)REG_READ(IO_MUX_GPIO37_REG),
           (unsigned long)REG_READ(IO_MUX_GPIO38_REG));
}

// Replicate stock app_main's boot preamble: release every pad hold that a prior
// stock power-off may have latched into the battery-backed RTC domain.
static void release_holds(void)
{
    // Global digital-pad autohold (set by gpio_deep_sleep_hold_en at stock OFF).
    gpio_deep_sleep_hold_dis();

    // Per-pad digital holds. 44 is stock's charge-enable (held HIGH at OFF);
    // 6/7 are LED data, 36/37/38 the LED power rail — release all defensively.
    const gpio_num_t dig[] = {6, 7, 36, 37, 38, 44};
    for (size_t i = 0; i < sizeof(dig) / sizeof(dig[0]); i++) {
        gpio_hold_dis(dig[i]);
    }

    // RTC-domain holds stock sets on {2,19,20} (rear button / USB D-/D+).
    const gpio_num_t rtc[] = {2, 19, 20};
    for (size_t i = 0; i < sizeof(rtc) / sizeof(rtc[0]); i++) {
        rtc_gpio_hold_dis(rtc[i]);
    }

    // Belt-and-braces: force-unhold all digital pads and clear the hold latch
    // directly, in case a pad was held by a mechanism the API calls above miss.
    REG_SET_BIT(RTC_CNTL_DIG_ISO_REG, RTC_CNTL_DG_PAD_FORCE_UNHOLD);
    REG_CLR_BIT(RTC_CNTL_DIG_ISO_REG, RTC_CNTL_DG_PAD_AUTOHOLD_EN);
    REG_WRITE(RTC_CNTL_DIG_PAD_HOLD_REG, 0);
}

// Bring up the top-board power rail: config 36/37/38 as outputs and drive high.
// GPIO36=1 is the addressable-LED supply enable.
static void power_rail_on(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << RAIL_36) | (1ULL << RAIL_37) | (1ULL << RAIL_38),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = 0,
        .pull_down_en = 0,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&io));
    gpio_set_level(RAIL_37, 1);
    gpio_set_level(RAIL_38, 1);
    gpio_set_level(RAIL_36, 1);   // <- the enable that was missing all along
}

// Bring up the 3 status LEDs on LEDC (low-speed, 8-bit, 5 kHz), all off.
static void init_status_leds(void)
{
    ledc_timer_config_t t = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_8_BIT,
        .timer_num       = LEDC_TIMER_0,
        .freq_hz         = 5000,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&t);
    for (int i = 0; i < 3; i++) {
        ledc_channel_config_t c = {
            .gpio_num   = STAT_GPIO[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel    = LEDC_CHANNEL_0 + i,
            .timer_sel  = LEDC_TIMER_0,
            .duty       = 0,
            .hpoint     = 0,
        };
        ledc_channel_config(&c);
    }
}

static void set_status(int i, int duty)
{
    if (i < 0 || i > 2) return;
    if (duty < 0) duty = 0; if (duty > 255) duty = 255;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0 + i, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0 + i);
}

static void status_flash(int count)
{
    for (int f = 0; f < count; f++) {
        for (int i = 0; i < 3; i++) set_status(i, 255);
        vTaskDelay(pdMS_TO_TICKS(140));
        for (int i = 0; i < 3; i++) set_status(i, 0);
        vTaskDelay(pdMS_TO_TICKS(140));
    }
}

static led_strip_handle_t mk_strip(int gpio, uint32_t n, spi_host_device_t bus)
{
    led_strip_config_t sc = {
        .strip_gpio_num = gpio,
        .max_leds = n,
        .led_pixel_format = LED_PIXEL_FORMAT_GRB,
        .led_model = LED_MODEL_WS2812,
        .flags = { .invert_out = false },
    };
    led_strip_spi_config_t sp = {
        .clk_src = SPI_CLK_SRC_DEFAULT,
        .spi_bus = bus,
        .flags = { .with_dma = true },
    };
    led_strip_handle_t h = NULL;
    esp_err_t e = led_strip_new_spi_device(&sc, &sp, &h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "strip gpio%d init failed: %s", gpio, esp_err_to_name(e));
        return NULL;
    }
    return h;
}

// ---- pixel plumbing --------------------------------------------------------

static inline uint8_t scale(uint8_t v) { return (uint16_t)v * s_bright / 255; }

static void push_key(int i)
{
    if (!s_keys || i < 0 || i >= KEY_N) return;
    led_strip_set_pixel(s_keys, i, scale(s_kbuf[i][0]), scale(s_kbuf[i][1]), scale(s_kbuf[i][2]));
}
static void push_amb(int i)
{
    if (!s_amb || i < 0 || i >= AMB_N) return;
    led_strip_set_pixel(s_amb, i, scale(s_abuf[i][0]), scale(s_abuf[i][1]), scale(s_abuf[i][2]));
}
static void refresh_all(void)
{
    if (s_keys) led_strip_refresh(s_keys);
    if (s_amb)  led_strip_refresh(s_amb);
}
static void set_key(int i, uint8_t r, uint8_t g, uint8_t b) { s_kbuf[i][0]=r; s_kbuf[i][1]=g; s_kbuf[i][2]=b; push_key(i); }
static void set_amb(int i, uint8_t r, uint8_t g, uint8_t b) { s_abuf[i][0]=r; s_abuf[i][1]=g; s_abuf[i][2]=b; push_amb(i); }

static void all_off(void)
{
    memset(s_kbuf, 0, sizeof(s_kbuf));
    memset(s_abuf, 0, sizeof(s_abuf));
    for (int i = 0; i < KEY_N; i++) push_key(i);
    for (int i = 0; i < AMB_N; i++) push_amb(i);
    refresh_all();
}

// HSV->RGB (h,s,v in 0..255) for the demo.
static void hsv(uint8_t h, uint8_t s, uint8_t v, uint8_t *r, uint8_t *g, uint8_t *b)
{
    uint8_t region = h / 43, rem = (h - region * 43) * 6;
    uint8_t p = (v * (255 - s)) >> 8;
    uint8_t q = (v * (255 - ((s * rem) >> 8))) >> 8;
    uint8_t t = (v * (255 - ((s * (255 - rem)) >> 8))) >> 8;
    switch (region) {
        case 0: *r=v; *g=t; *b=p; break;
        case 1: *r=q; *g=v; *b=p; break;
        case 2: *r=p; *g=v; *b=t; break;
        case 3: *r=p; *g=q; *b=v; break;
        case 4: *r=t; *g=p; *b=v; break;
        default:*r=v; *g=p; *b=q; break;
    }
}

// Non-blocking one-shot demo frame driver (advances a rainbow each call).
static void demo_burst(int frames)
{
    const int n = KEY_N + AMB_N;
    for (int f = 0; f < frames; f++) {
        for (int i = 0; i < KEY_N; i++) {
            uint8_t r,g,b; hsv((uint8_t)((i * 256 / n) + f * 4), 255, 255, &r,&g,&b);
            set_key(i, r, g, b);
        }
        for (int j = 0; j < AMB_N; j++) {
            uint8_t r,g,b; hsv((uint8_t)(((j + KEY_N) * 256 / n) + f * 4), 255, 255, &r,&g,&b);
            set_amb(j, r, g, b);
        }
        refresh_all();
        vTaskDelay(pdMS_TO_TICKS(33));
    }
}

// ---- serial command parser -------------------------------------------------

static int parse_hex(const char *s, uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (strlen(s) < 6) return -1;
    char t[3] = {0};
    t[0]=s[0]; t[1]=s[1]; *r=(uint8_t)strtol(t,NULL,16);
    t[0]=s[2]; t[1]=s[3]; *g=(uint8_t)strtol(t,NULL,16);
    t[0]=s[4]; t[1]=s[5]; *b=(uint8_t)strtol(t,NULL,16);
    return 0;
}

static void handle_line(char *line)
{
    // tokenize
    char *tok[4]; int nt = 0;
    for (char *p = strtok(line, " \t"); p && nt < 4; p = strtok(NULL, " \t")) tok[nt++] = p;
    if (nt == 0) return;

    if (!strcmp(tok[0], "clear")) { all_off(); printf("ok clear\n"); return; }
    if (!strcmp(tok[0], "demo"))  { demo_burst(120); all_off(); printf("ok demo\n"); return; }
    if (!strcmp(tok[0], "dump"))  { dump_regs("live"); printf("ok dump\n"); return; }
    if (!strcmp(tok[0], "tflash")) { status_flash(nt >= 2 ? atoi(tok[1]) : 6); printf("ok tflash\n"); return; }
    if (!strcmp(tok[0], "t") && nt >= 3) {
        int duty = atoi(tok[2]);
        if (!strcmp(tok[1], "all")) { for (int i = 0; i < 3; i++) set_status(i, duty); printf("ok t all %d\n", duty); return; }
        int i = atoi(tok[1]);
        if (i < 0 || i > 2) { printf("err index\n"); return; }
        set_status(i, duty); printf("ok t %d %d\n", i, duty); return;
    }
    if (!strcmp(tok[0], "bright") && nt >= 2) {
        int v = atoi(tok[1]); if (v < 0) v = 0; if (v > 255) v = 255; s_bright = (uint8_t)v;
        for (int i=0;i<KEY_N;i++) push_key(i);
        for (int i=0;i<AMB_N;i++) push_amb(i);
        refresh_all();
        printf("ok bright %d\n", s_bright); return;
    }

    // k/u commands
    int is_key = !strcmp(tok[0], "k");
    int is_amb = !strcmp(tok[0], "u");
    if ((is_key || is_amb) && nt >= 3) {
        uint8_t r,g,b;
        if (parse_hex(tok[2], &r,&g,&b) != 0) { printf("err badhex\n"); return; }
        int n = is_key ? KEY_N : AMB_N;
        if (!strcmp(tok[1], "all")) {
            for (int i = 0; i < n; i++) is_key ? set_key(i,r,g,b) : set_amb(i,r,g,b);
            refresh_all();
            printf("ok %s all %02x%02x%02x\n", tok[0], r,g,b); return;
        }
        int i = atoi(tok[1]);
        if (i < 0 || i >= n) { printf("err index\n"); return; }
        is_key ? set_key(i,r,g,b) : set_amb(i,r,g,b);
        refresh_all();
        printf("ok %s %d %02x%02x%02x\n", tok[0], i, r,g,b); return;
    }

    printf("err unknown\n");
}

void app_main(void)
{
    ESP_LOGW(TAG, "=== CM2 custom LED firmware ===");

    // 1. Show the state we inherited (proves the hold theory on-device), then
    //    release any latched holds before touching a single pin.
    dump_regs("boot");
    release_holds();
    dump_regs("released");

    // 2. Power the top-board LED rail (GPIO36 enable + 37/38).
    power_rail_on();

    // 3. Bring up both addressable strips with the stock topology.
    s_keys = mk_strip(KEY_GPIO, KEY_N, SPI2_HOST);
    s_amb  = mk_strip(AMB_GPIO, AMB_N, SPI3_HOST);
    ESP_LOGW(TAG, "strips: keys=%p amb=%p", (void*)s_keys, (void*)s_amb);

    // 3 status/"touch" LEDs (GPIO 35/45/48, PWM). Blink them at boot to confirm.
    init_status_leds();
    status_flash(2);

    all_off();

    // 4. Startup demo so light is visible immediately with no host attached.
    demo_burst(150);
    all_off();
    // Leave a gentle idle indicator: key 0 dim white so we can see it's alive.
    set_key(0, 12, 12, 12);
    refresh_all();

    // 5. Serial command loop over USB-Serial-JTAG.
    usb_serial_jtag_driver_config_t ucfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    usb_serial_jtag_driver_install(&ucfg);
    usb_serial_jtag_vfs_use_driver();
    setvbuf(stdin, NULL, _IONBF, 0);

    printf("ok ready keys=%d under=%d\n", s_keys?KEY_N:0, s_amb?AMB_N:0);

    char line[64]; int len = 0;
    for (;;) {
        uint8_t c;
        int n = usb_serial_jtag_read_bytes(&c, 1, pdMS_TO_TICKS(100));
        if (n <= 0) continue;
        if (c == '\r') continue;
        if (c == '\n') {
            line[len] = 0;
            if (len) handle_line(line);
            len = 0;
        } else if (len < (int)sizeof(line) - 1) {
            line[len++] = (char)c;
        } else {
            len = 0;  // overflow, resync on next newline
        }
    }
}
