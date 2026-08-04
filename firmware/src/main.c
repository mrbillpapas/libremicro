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
// ============================================================================
// v2 — "thin transport": the pad now EMITS INPUT EVENTS as well as taking LED
// commands. See docs/PROTOCOL.md for the grammar and firmware/README.md for the
// v2 notes. Everything v1 did still works byte-for-byte; v2 only adds.
// ============================================================================
//
// Serial command protocol (newline-terminated, over the USB-Serial-JTAG console):
//   k <i> <rrggbb> | k all <rrggbb>   set key LED
//   u <i> <rrggbb> | u all <rrggbb>   set underglow LED
//   kf <rrggbb> x13                   set ALL 13 key LEDs, ONE refresh      (v2)
//   uf <rrggbb> x8                    set ALL 8 underglow LEDs, ONE refresh (v2)
//   clear                             all off
//   demo                              per-key rainbow sweep
//   bright <0-255>                    global brightness scale
//   t <i> <0-255> | t all <0-255>     status LED PWM duty
//   tflash [count]                    blink the status LEDs
//   dump                              print inherited/live hold+GPIO register state
//   mscan                             print the RAW live matrix bitmap       (v2)
//   ver                               report protocol/feature support        (v2)
//   batt                              report the current battery reading     (v2)
// Each command replies with a line starting "ok" or "err".
//
// Device -> host event lines (v2). These NEVER start with "ok" or "err", so the
// host can demultiplex acks from events on the one shared link by line prefix:
//   key <logical 0-12> down | key <logical 0-12> up
//   enc cw | enc ccw | enc press | enc release      (guarded, see below)
//   touch down | touch up                            (guarded, see below)
//   rear down | rear up                              (guarded, see below)
//   batt <percent> <0|1>                             (guarded, see below)
// Diagnostic notices are emitted as "# ..." lines, which the host ignores.

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <stdarg.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "driver/gpio_filter.h"
#include "driver/i2c_master.h"
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

// ---- key matrix (VERIFIED pin map, docs/HARDWARE.md) ------------------------
//
// Scanned 4x4 matrix, 13 of 16 slots populated.
//   rows: push-pull outputs, active-high strobes, exactly one high at a time
//   cols: inputs with internal pull-down, read HIGH when a key in the strobed
//         row is pressed
// Raw matrix index = 4*row + col, so 0..15 with three unpopulated slots.
#define MTX_ROWS 4
#define MTX_COLS 4
static const gpio_num_t MTX_ROW_GPIO[MTX_ROWS] = { 46, 17, 40, 47 };
static const gpio_num_t MTX_COL_GPIO[MTX_COLS] = { 13,  5, 21,  1 };

#define MTX_ROW_SETTLE_US   10   // let the row line and column caps settle
#define MTX_SCAN_PERIOD_MS   2   // active-scan period (needs FREERTOS_HZ=1000)
#define MTX_DEBOUNCE_MS      6   // a transition must persist this long to count
#define MTX_LINGER_MS       40   // keep actively scanning this long after quiet
#define MTX_IDLE_POLL_MS    25   // safety net if a column edge is ever missed

// ===========================================================================
// THE ONE PLACE TO FIX THE KEY NUMBERING.
// ===========================================================================
//
// Raw matrix index (4*row + col) -> LOGICAL key index, or -1 for the three
// slots that hold a non-key control instead of a switch.
//
// PROTOCOL.md requires firmware to emit LOGICAL indices (0..12), because that
// is the numbering config and the host daemon speak. Logical index is assigned
// row-major over POPULATED slots only, so it is a lookup, NOT arithmetic on the
// matrix index. Derived from host/daemon/libremicro/layout.py:
//
//   grid   row 0: [encoder]  key  key  [joystick]   -> logical 0,1   at cols 1,2
//          row 1:   key      key  key    key        -> logical 2,3,4,5
//          row 2:   key      key  key    key        -> logical 6,7,8,9
//          row 3:  [touch]   key  key    key        -> logical 10,11,12 at cols 1,2,3
//
// This agrees with layout.py's rowcol_to_logical(): e.g. row 3 ordinal 0 is
// logical 10 = 2+4+4, and grid_col(3,0) == 1.
//
// *** UNVERIFIED ASSUMPTION — CORRECT IT HERE AND NOWHERE ELSE ***
// Two things below are assumptions, not measurements:
//   (a) that matrix column order (GPIO 13, 5, 21, 1) runs physically
//       LEFT-TO-RIGHT, i.e. col 0 == grid column 0; and
//   (b) that matrix row order (GPIO 46, 17, 40, 47) runs physically
//       TOP-TO-BOTTOM, i.e. row 0 == the encoder/joystick row.
// docs/HARDWARE.md calls (a) out explicitly as still open: it cannot be
// determined from the LEDs, only by pressing real keys. If the columns turn out
// reversed, or the rows, the fix is to rewrite THIS TABLE ONLY — every event the
// firmware emits and every index the host sees flows through it. Use the `mscan`
// command to read the raw bitmap while holding a known key and rebuild the table
// from what you see.
static const int8_t MTX_TO_LOGICAL[MTX_ROWS * MTX_COLS] = {
    // matrix row 0 — assumed top row: encoder at col 0, joystick at col 3
    /*  0 */ -1,  /*  1 */  0,  /*  2 */  1,  /*  3 */ -1,
    // matrix row 1 — four keys
    /*  4 */  2,  /*  5 */  3,  /*  6 */  4,  /*  7 */  5,
    // matrix row 2 — four keys
    /*  8 */  6,  /*  9 */  7,  /* 10 */  8,  /* 11 */  9,
    // matrix row 3 — assumed bottom row: capacitive touch pad at col 0.
    // NOTE logical 10 and 11 are the two switches under the single wide keycap
    // (layout.py SHARED_KEYCAPS) — two switches, two LEDs, one cap.
    /* 12 */ -1,  /* 13 */ 10,  /* 14 */ 11,  /* 15 */ 12,
};

// ===========================================================================
// UNVERIFIED INPUTS — encoder, touch pad, rear button. OFF BY DEFAULT.
// ===========================================================================
//
// docs/HARDWARE.md marks these three pin maps "provisional, NOT yet re-verified":
// they came from a side decode and the analysis pass that would have confirmed
// them crashed first. There is also a known unresolved conflict — GPIO 2 is cited
// BOTH as the touch IC's active-low interrupt AND as the ext0 wake pin.
//
// Why this is a compile-time flag and the key matrix is not: the matrix pin map
// is verified, so driving those rows as outputs is known-safe. Here a wrong map
// could point an OUTPUT at something that must not be driven, which can do real
// physical harm. So the default build simply never touches these pins, and the
// safe half of v2 (the matrix) ships without waiting on them.
//
// Set to 1 only once the pins below are confirmed against the vendor disassembly.
// Defensive invariant, deliberately kept true throughout this block: every pin
// here is configured GPIO_MODE_INPUT and NEVER driven. If you find yourself
// adding an output here, stop and re-verify the map first.
#ifndef LM_ENABLE_UNVERIFIED_INPUTS
#define LM_ENABLE_UNVERIFIED_INPUTS 0
#endif

// The three aux inputs are now separately gated, because they no longer carry the same
// risk. Their pins are all CONFIRMED (docs/PIN-VERIFICATION.md), and all three are read
// as inputs and never driven — so a wrong pin could only produce spurious events, not
// damage. The rear button is the exception, and not because of its pin:
//
//   GPIO 2 is also stock's ext0 wake pin, and stock's rear-button "rescue" arms a
//   ULP-RISCV watcher that forces SW_SYS_RST when it sees that pin go low. That watcher
//   is armed by the sys.bootloader RPC — which is exactly what our own flashing script
//   calls — so it may well be live right now. With it running, pressing the rear button
//   reboots the pad. We halt the ULP at boot (see aux_inputs_start) but that is a
//   mitigation, not a proof, so REAR stays opt-in until someone confirms it on hardware.
//
// LM_ENABLE_UNVERIFIED_INPUTS=1 still turns everything on, for compatibility.
#ifndef LM_ENABLE_ENCODER
#define LM_ENABLE_ENCODER (LM_ENABLE_UNVERIFIED_INPUTS ? 1 : 1)
#endif
#ifndef LM_ENABLE_TOUCH
#define LM_ENABLE_TOUCH   (LM_ENABLE_UNVERIFIED_INPUTS ? 1 : 1)
#endif
#ifndef LM_ENABLE_REAR
#define LM_ENABLE_REAR    (LM_ENABLE_UNVERIFIED_INPUTS ? 1 : 0)
#endif

#define LM_ENABLE_AUX (LM_ENABLE_ENCODER || LM_ENABLE_TOUCH || LM_ENABLE_REAR)

// Pin numbers, now RESOLVED from the vendor firmware — see docs/PIN-VERIFICATION.md
// for the evidence (3-5 independent attestations per pin).
//
// The old provisional table had TOUCH AND REAR SWAPPED. That swap was the entire
// "GPIO 2 is cited as both touch and ext0 wake" conflict: GPIO 2 is the rear
// button, and stock genuinely does use it as the ext0 wake pin. No conflict.
#define LM_PIN_TOUCH    14   // external touch IC interrupt
#define LM_PIN_REAR     2    // rear button, active LOW; ALSO stock's ext0 wake pin
#define LM_PIN_ENC_A    12   // quadrature A (vendor gpio_config mask 0x1000)
#define LM_PIN_ENC_B    11   // quadrature B (mask 0x800)
#define LM_PIN_ENC_SW   4    // encoder push switch, active LOW (mask 0x10)
// Which rotation direction is "cw" is NOT in the firmware — it depends on PCB
// wiring. If the dial feels backwards, swap ENC_A and ENC_B.

// Touch polarity is the ONE thing static analysis could not settle. The ISR passes
// the raw level with no inversion, unlike the rear button and encoder switch in the
// same vendor file which both compute (level == 0) — so active HIGH is the reading,
// and "_L" in PIN_TOUCH_OUT_L is probably "Left" (the pad sits at grid (3,0)) rather
// than "active Low". One hardware read settles it: if touch fires when you are NOT
// touching, flip this to 0.
#define LM_TOUCH_ACTIVE_HIGH 1

// Quadrature transitions per detent. An EC11-style detented encoder gives 4 edges per
// click; if one physical click produces several events, lower this, and if it takes several
// clicks to produce one event, raise it.
#ifndef LM_ENC_STEPS_PER_DETENT
#define LM_ENC_STEPS_PER_DETENT 4
#endif

// Direction invert, mirroring stock's own u8[13] flag. Which rotation is physically
// clockwise depends on PCB wiring and is NOT in the vendor firmware, so this is the one
// knob that can only be set by turning the knob. Flip it if cw/ccw come out backwards.
#ifndef LM_ENC_INVERT
#define LM_ENC_INVERT 0
#endif

#define LM_AUX_POLL_MS  2    // poll period for the guarded inputs
#define LM_AUX_TAP_MS   150  // min gap between touch/rear reports, anti-chatter

// ===========================================================================
// BATTERY — MAX77972 charger + ModelGauge-m5 fuel gauge over I2C. READ ONLY.
// ===========================================================================
//
// Everything below was decoded out of the vendor v0.6.1 image (the same method
// docs/PIN-VERIFICATION.md describes). The MAX77972 has no public datasheet, so
// stock firmware IS the datasheet here. What was recovered:
//
//   * The bus. wl_io::init (0x4200936c) calls Arduino Wire.begin(sda, scl, hz)
//     with the literals sda=8, scl=18, hz=100000 (the frequency is the literal
//     0x186a0 at 0x4200088c). TwoWire::begin(this, sdaPin, sclPin, frequency)
//     is confirmed by following it to i2cInit(num, this[17], this[18], freq).
//
//     *** THE ONE DISAGREEMENT WITH docs/HARDWARE.md ***  That doc lists I2C as
//     GPIO 8/9. 8 matches; SCL does NOT. GPIO 8/9 happens to be the arduino-esp32
//     *default* I2C pair for the ESP32-S3, which is exactly what you would write
//     down if you assumed rather than measured -- and stock explicitly overrides
//     the default with 18 while leaving SDA at 8. GPIO 18 is also not configured
//     for anything else anywhere in the vendor image (PIN-VERIFICATION.md's
//     exhaustive gpio_config inventory), and neither is GPIO 9. So the vendor
//     image says 18, and this file believes the vendor image -- but because SCL
//     is an *output* and the doc disagrees, batt_init() PROBES: it brings the bus
//     up on SCL=18, asks the gauge to ACK, and if it doesn't, tears the bus down
//     and retries on SCL=9. Whichever ACKs wins, and `batt` / the boot notice
//     report which one, so one flash settles the question for the doc.
//
//   * Two addresses, one register space. The register accessors (read-modify-write
//     at 0x420cbe14, the 42-register bulk read at 0x420cbe70) both compute the
//     slave address the same way: `addr = (reg > 0xFF) ? 0x37 : 0x36`. So bank 0
//     (regs 0x000-0x0FF, the live gauge/charger registers) lives at 7-bit 0x36 and
//     bank 1 (regs 0x100-0x1FF, the nonvolatile config block) at 0x37. We only
//     ever touch bank 0, and only ever read.
//
//   * 16-bit registers, LSB first. The transport (0x42049b54) does
//     beginTransmission(addr); write(reg); endTransmission(false); requestFrom(addr,2)
//     then assembles `(second << 8) | first`.
//
//   * The scale factors, which are what actually identify the registers. Stock's
//     own accessors apply: 78.125 uV/LSB to reg 0x1A (-> vcell), 0.15625 mA/LSB to
//     int16 reg 0x1C (-> current), 1/256 %/LSB to reg 0x07 (-> "soc"), 1/256 degC
//     to int16 reg 0x1B/0x34, 0.5 mAh/LSB to regs 0x06/0x10/0x23, and (reg 0xFF >> 8)
//     for an integer percent. Those are textbook ModelGauge m5 units (0.5 mAh and
//     0.15625 mA both imply a 10 mOhm sense resistor), and the surrounding map
//     agrees with m5 too: 0x10 FullCapRep, 0x23 FullCapNom, 0x17 Cycles, 0x34
//     DieTemp, 0xFF VFSOC, 0x00 Status with POR in bit 1. That cross-check is why
//     the addresses below are read as decoded facts rather than guesses.
//
//   * Charging. Stock's is_charging() (0x420cbab0) returns true exactly when its
//     charge-state enum is prequal_trickle / fast_charge_cc / fast_charge_cv_or_topoff,
//     and its state decoder (0x420cc28c) produces those three from
//     `chg_dtls = (reg 0xD7 >> 8) & 0x0F` values 0, 1 and 2 respectively. Two
//     independent code paths, one conclusion: charging == chg_dtls <= 2.
//     Note chg_dtls 8 + a bit in reg 0x3A is stock's "charge_done" -- FULL, which
//     is deliberately NOT charging, matching what a host wants to show.
//
// WHAT THIS BLOCK WILL NEVER DO:
//   - write any MAX77972 register (not one, not ever -- see the flag note below);
//   - touch GPIO 44, the charge-enable. Mis-driving a charger is a real hazard and
//     nothing here needs it;
//   - report a percentage it did not actually read. Every failure path reports
//     "unknown", never a number;
//   - take the LEDs or the key matrix down with it. A sulking fuel gauge must not
//     stop a macropad lighting up.
//
// Default ON. Unlike the encoder/touch/rear block above, the register semantics
// here are not assumptions -- they are stock's own scale factors and stock's own
// charging predicate, cross-validated against the published ModelGauge m5 units.
// The bus is driven read-only, and the one genuinely open question (SCL 18 vs 9)
// is resolved at runtime by probing instead of by picking. Set to 0 to compile the
// whole thing out; then `ver` reports batt=none and no I2C pin is ever configured.
#ifndef LM_ENABLE_BATTERY
#define LM_ENABLE_BATTERY 1
#endif

#define LM_I2C_SDA_GPIO   8       // vendor-attested, agrees with docs/HARDWARE.md
#define LM_I2C_SCL_GPIO   18      // vendor-attested; docs/HARDWARE.md says 9
#define LM_I2C_SCL_ALT    9       // the doc's value, tried second if 18 won't ACK
#define LM_I2C_HZ         100000  // stock's Wire.begin frequency literal

#define LM_FG_ADDR        0x36    // bank 0: regs 0x00-0xFF (bank 1 = 0x37, unused)

// Bank-0 registers, all 16-bit little-endian. Only these five are ever read.
#define LM_REG_STATUS       0x00  // m5 Status; bit 1 = POR
#define LM_REG_REPSOC       0x07  // reported state of charge, 1/256 % per LSB
#define LM_REG_VCELL        0x1A  // cell voltage, 78.125 uV per LSB
#define LM_REG_CHG_DETAILS  0xD7  // charger details; bits 11:8 = chg_dtls
#define LM_REG_VFSOC        0xFF  // voltage-based SOC, 1/256 % per LSB

// Sanity window on VCELL. A 1S Li-ion outside this is not a battery we can
// believe, so we report unknown rather than a percentage derived from noise.
#define LM_BATT_MV_MIN    2000
#define LM_BATT_MV_MAX    5000

#define LM_BATT_POLL_MS   15000   // a battery, not a sensor
#define LM_BATT_FAILS_MAX 3       // consecutive read failures before "unknown"

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

// ---- line-atomic output ----------------------------------------------------
//
// v2 has more than one writer: the command loop emits "ok"/"err" acks while the
// scan task emits event lines. Both land on the same USB-Serial-JTAG stream, and
// the host demultiplexes them by line prefix — which only works if a line never
// gets cut in half by the other writer. So every complete line goes out through
// out_line(), under one mutex. Callers include the trailing "\n".
static SemaphoreHandle_t s_out_mux;

static void out_line(const char *fmt, ...)
{
    char buf[224];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 1) n = (int)sizeof(buf) - 1;   // truncated, still one line

    if (s_out_mux) xSemaphoreTake(s_out_mux, portMAX_DELAY);
    fwrite(buf, 1, (size_t)n, stdout);
    fflush(stdout);
    if (s_out_mux) xSemaphoreGive(s_out_mux);
}

// ---- register dump helpers -------------------------------------------------

static void dump_regs(const char *when)
{
    out_line("regs[%s]: DIG_ISO(0x60008094)=0x%08lx PAD_HOLD(0x600080d8)=0x%08lx "
             "DIG_PAD_HOLD(0x600080dc)=0x%08lx\n",
             when,
             (unsigned long)REG_READ(RTC_CNTL_DIG_ISO_REG),
             (unsigned long)REG_READ(RTC_CNTL_PAD_HOLD_REG),
             (unsigned long)REG_READ(RTC_CNTL_DIG_PAD_HOLD_REG));
    out_line("regs[%s]: GPIO_OUT=0x%08lx GPIO_OUT1=0x%08lx GPIO_ENABLE=0x%08lx "
             "GPIO_ENABLE1=0x%08lx\n",
             when,
             (unsigned long)REG_READ(GPIO_OUT_REG),
             (unsigned long)REG_READ(GPIO_OUT1_REG),
             (unsigned long)REG_READ(GPIO_ENABLE_REG),
             (unsigned long)REG_READ(GPIO_ENABLE1_REG));
    out_line("regs[%s]: IO_MUX g6=0x%08lx g7=0x%08lx g36=0x%08lx g37=0x%08lx g38=0x%08lx\n",
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
    //
    // v2 adds the key-matrix pads (rows 46/17/40/47, cols 13/5/21/1). A latched
    // hold on a row pad would make gpio_set_level silently do nothing and the
    // matrix would read as permanently idle — the exact same failure mode that
    // kept the LED rail dark, just on a different pin. Release them too.
    //
    // The battery block adds the I2C pads (SDA 8, SCL 18 or 9). A latched hold on
    // SDA or SCL would leave the line stuck and every transaction would time out --
    // same failure mode again, third pin group. Note 44 is RELEASED here and never
    // driven anywhere in this firmware; releasing a hold is not the same as taking
    // control of the charger.
    const gpio_num_t dig[] = {6, 7, 36, 37, 38, 44,
                              46, 17, 40, 47, 13, 5, 21, 1,
                              8, 9, 18};
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

// ---- key matrix scanning ---------------------------------------------------
//
// Shape (as docs/HARDWARE.md describes): a dedicated task, woken by an any-edge
// interrupt on the column inputs, so the pad costs nothing while nobody is
// typing and the serial command loop is never starved by polling.
//
//   idle    : ALL rows driven HIGH, column interrupts armed. Pressing any key
//             pulls its column high -> edge -> the task is notified. (Rows all
//             high means we cannot tell WHICH key, only that something moved,
//             which is all a wake needs to know.)
//   active  : column interrupts masked (row strobing would retrigger them
//             constantly), rows strobed one at a time every MTX_SCAN_PERIOD_MS.
//             Falls back to idle once nothing has changed for MTX_LINGER_MS.
//
// The idle wait also has a MTX_IDLE_POLL_MS timeout. That covers the one race
// the interrupt cannot: a key pressed in the instant between the last scan and
// arming, whose column is already high so no edge ever arrives.

static TaskHandle_t s_scan_task;

// Debounce state, per RAW matrix slot (0..15).
static uint8_t     s_mtx_state[MTX_ROWS * MTX_COLS];    // debounced: 1 = down
static uint8_t     s_mtx_cand[MTX_ROWS * MTX_COLS];     // last raw sample
static TickType_t  s_mtx_cand_at[MTX_ROWS * MTX_COLS];  // when it first differed
static bool        s_mtx_warned[MTX_ROWS * MTX_COLS];   // "unpopulated" notice sent
static uint8_t     s_mtx_last_raw[MTX_ROWS];            // newest raw sample, for `mscan`

static void IRAM_ATTR mtx_col_isr(void *arg)
{
    (void)arg;
    if (s_scan_task) {
        vTaskNotifyGiveFromISR(s_scan_task, NULL);
    }
}

static void mtx_gpio_init(void)
{
    uint64_t rowmask = 0, colmask = 0;
    for (int r = 0; r < MTX_ROWS; r++) rowmask |= 1ULL << MTX_ROW_GPIO[r];
    for (int c = 0; c < MTX_COLS; c++) colmask |= 1ULL << MTX_COL_GPIO[c];

    // Rows: push-pull outputs. Start LOW so no key can read as pressed before
    // the scanner owns the pins.
    gpio_config_t rows = {
        .pin_bit_mask = rowmask,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&rows));
    for (int r = 0; r < MTX_ROWS; r++) gpio_set_level(MTX_ROW_GPIO[r], 0);

    // Columns: inputs with the internal pull-down, so an unstrobed column reads
    // LOW rather than floating. Any-edge interrupt provides the idle wake.
    gpio_config_t cols = {
        .pin_bit_mask = colmask,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type    = GPIO_INTR_ANYEDGE,
    };
    ESP_ERROR_CHECK(gpio_config(&cols));

    esp_err_t e = gpio_install_isr_service(0);
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) {   // INVALID_STATE = already installed
        ESP_LOGE(TAG, "gpio isr service: %s", esp_err_to_name(e));
    }
    for (int c = 0; c < MTX_COLS; c++) {
        gpio_isr_handler_add(MTX_COL_GPIO[c], mtx_col_isr, NULL);
        gpio_intr_disable(MTX_COL_GPIO[c]);
    }
}

// Strobe one row high, let it settle, sample all four columns.
// Returns a bitmap of columns reading HIGH (i.e. pressed) in that row.
static uint8_t mtx_read_row(int row)
{
    for (int r = 0; r < MTX_ROWS; r++) gpio_set_level(MTX_ROW_GPIO[r], 0);
    gpio_set_level(MTX_ROW_GPIO[row], 1);
    esp_rom_delay_us(MTX_ROW_SETTLE_US);

    uint8_t bits = 0;
    for (int c = 0; c < MTX_COLS; c++) {
        if (gpio_get_level(MTX_COL_GPIO[c])) bits |= (uint8_t)(1u << c);
    }
    gpio_set_level(MTX_ROW_GPIO[row], 0);
    return bits;
}

static void mtx_read_all(uint8_t out[MTX_ROWS])
{
    for (int r = 0; r < MTX_ROWS; r++) out[r] = mtx_read_row(r);
}

// One debounce pass. Emits at most one event line per slot per transition.
// Returns true if anything is down or a transition is still settling — i.e.
// whether it is too early to go back to sleep.
static bool mtx_scan_once(void)
{
    uint8_t raw[MTX_ROWS];
    mtx_read_all(raw);
    memcpy(s_mtx_last_raw, raw, sizeof(raw));   // published for `mscan`

    TickType_t now = xTaskGetTickCount();
    const TickType_t settle = pdMS_TO_TICKS(MTX_DEBOUNCE_MS);
    bool busy = false;

    for (int r = 0; r < MTX_ROWS; r++) {
        for (int c = 0; c < MTX_COLS; c++) {
            int m = r * MTX_COLS + c;
            uint8_t now_raw = (raw[r] >> c) & 1u;

            if (now_raw == s_mtx_state[m]) {
                s_mtx_cand[m] = now_raw;          // any bounce has cancelled itself
            } else if (s_mtx_cand[m] != now_raw) {
                s_mtx_cand[m] = now_raw;          // new candidate, start its clock
                s_mtx_cand_at[m] = now;
            } else if ((TickType_t)(now - s_mtx_cand_at[m]) >= settle) {
                s_mtx_state[m] = now_raw;         // held long enough: commit

                int logical = MTX_TO_LOGICAL[m];
                if (logical >= 0) {
                    // The ONLY place a key event reaches the wire. Logical index,
                    // per PROTOCOL.md.
                    out_line("key %d %s\n", logical, now_raw ? "down" : "up");
                } else if (now_raw && !s_mtx_warned[m]) {
                    // A slot the lookup table says holds the encoder/joystick/touch
                    // pad just went active. That means MTX_TO_LOGICAL is wrong for
                    // this board (most likely the column or row order assumption).
                    // Say so once, on a "#" line the host ignores, rather than
                    // inventing a key index.
                    s_mtx_warned[m] = true;
                    out_line("# warn raw matrix slot %d (row %d col %d) is active but "
                             "mapped unpopulated - check MTX_TO_LOGICAL\n", m, r, c);
                }
            }

            if (s_mtx_state[m] || s_mtx_cand[m] != s_mtx_state[m]) busy = true;
        }
    }
    return busy;
}

// Park the matrix for an interrupt wake: every row high, columns armed.
static void mtx_arm_idle(void)
{
    for (int r = 0; r < MTX_ROWS; r++) gpio_set_level(MTX_ROW_GPIO[r], 1);
    esp_rom_delay_us(MTX_ROW_SETTLE_US);
    for (int c = 0; c < MTX_COLS; c++) gpio_intr_enable(MTX_COL_GPIO[c]);
}

// Take the pins back for active strobing.
static void mtx_begin_active(void)
{
    for (int c = 0; c < MTX_COLS; c++) gpio_intr_disable(MTX_COL_GPIO[c]);
    for (int r = 0; r < MTX_ROWS; r++) gpio_set_level(MTX_ROW_GPIO[r], 0);
}

static void mtx_scan_task(void *arg)
{
    (void)arg;
    for (;;) {
        mtx_arm_idle();
        // Driving the rows high to arm generates column edges of our own making.
        // Discard those first, or every re-arm would instantly re-wake us and the
        // task would spin at the linger period forever. If a genuine press lands in
        // this window its notification is discarded too — that is precisely what
        // the MTX_IDLE_POLL_MS timeout below is for.
        ulTaskNotifyTake(pdTRUE, 0);
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(MTX_IDLE_POLL_MS));
        mtx_begin_active();

        TickType_t quiet_since = xTaskGetTickCount();
        for (;;) {
            if (mtx_scan_once()) {
                quiet_since = xTaskGetTickCount();
            } else if ((TickType_t)(xTaskGetTickCount() - quiet_since)
                       >= pdMS_TO_TICKS(MTX_LINGER_MS)) {
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(MTX_SCAN_PERIOD_MS));
        }
    }
}

// ---- guarded: encoder / touch pad / rear button -----------------------------
//
// Entirely inert unless LM_ENABLE_UNVERIFIED_INPUTS is 1. See the pin block near
// the top of the file for why. Emits, per docs/PROTOCOL.md:
//   enc cw | enc ccw | enc press | enc release | touch down/up | rear down/up

#if LM_ENABLE_AUX

// Quadrature decode, matched to what stock v0.6.1 actually does. The vendor's decoder was
// fully recovered (docs/PIN-VERIFICATION.md) and the differences from a naive implementation
// are exactly the things that make a naive one feel wonky:
//
//   1. State is (B << 1) | A, not (A << 1) | B. Getting this backwards inverts direction.
//   2. On a direction REVERSAL the accumulator is reset to the new step rather than added
//      to. Without this, jitter either side of a detent accumulates and eventually fakes a
//      step you never made — the main source of phantom events.
//   3. After emitting, the accumulator is zeroed, not decremented by the threshold. Keeping
//      a remainder lets error carry forward between detents.
//
// The table is the classic 16-entry form, indexed prev*4 + now, taken from DROM 0x3c201bf4.
static const int8_t LM_QUAD_LUT[16] = {
     0, -1,  1,  0,
     1,  0,  0, -1,
    -1,  0,  0,  1,
     0,  1, -1,  0,
};

// Stock installs a hardware pin glitch filter on every one of these inputs
// (docs/PIN-VERIFICATION.md). It is not decoration: an EC11 encoder's contacts bounce, and
// each bounce is an edge the quadrature decoder has to either filter or mis-count. Doing it
// in the GPIO peripheral costs nothing and removes the bounce before software ever sees it.
static void aux_install_glitch_filter(int pin)
{
    if (pin < 0) return;
    gpio_glitch_filter_handle_t filt = NULL;
    gpio_pin_glitch_filter_config_t cfg = {
        .clk_src = GLITCH_FILTER_CLK_SRC_DEFAULT,
        .gpio_num = pin,
    };
    if (gpio_new_pin_glitch_filter(&cfg, &filt) == ESP_OK && filt) {
        gpio_glitch_filter_enable(filt);
    } else {
        out_line("# warn glitch filter unavailable on GPIO %d\n", pin);
    }
}

static void aux_cfg_input(int pin, gpio_pulldown_t pd, gpio_pullup_t pu)
{
    if (pin < 0) return;
    // INPUT ONLY. Never GPIO_MODE_OUTPUT in this block — see the note above.
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << pin,
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = pu,
        .pull_down_en = pd,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);
    aux_install_glitch_filter(pin);
}

static void aux_task(void *arg)
{
    (void)arg;
    const bool have_enc    = LM_ENABLE_ENCODER && LM_PIN_ENC_A >= 0 && LM_PIN_ENC_B >= 0;
    const bool have_enc_sw = LM_ENABLE_ENCODER && LM_PIN_ENC_SW >= 0;
    const bool have_touch  = LM_ENABLE_TOUCH;
    const bool have_rear   = LM_ENABLE_REAR;

    uint8_t prev_ab = 0;
    int32_t accum = 0;
    // Seed from the ACTUAL pin levels. Assuming idle-high made the very first poll look
    // like a transition, which emitted a phantom `touch up` on every boot.
    uint8_t sw_state   = have_enc_sw ? (uint8_t)gpio_get_level(LM_PIN_ENC_SW) : 1;
    uint8_t touch_state = have_touch ? (uint8_t)gpio_get_level(LM_PIN_TOUCH) : 1;
    uint8_t rear_state  = have_rear  ? (uint8_t)gpio_get_level(LM_PIN_REAR)  : 1;
    TickType_t last_touch = 0, last_rear = 0;

    if (have_enc) {
        prev_ab = (uint8_t)((gpio_get_level(LM_PIN_ENC_A) << 1) | gpio_get_level(LM_PIN_ENC_B));
    }

    for (;;) {
        TickType_t now = xTaskGetTickCount();

        if (have_enc) {
            // (B << 1) | A — stock's ordering. See LM_QUAD_LUT above.
            uint8_t ab = (uint8_t)((gpio_get_level(LM_PIN_ENC_B) << 1)
                                   | gpio_get_level(LM_PIN_ENC_A));
            if (ab != prev_ab) {
                const int8_t raw = LM_QUAD_LUT[(prev_ab << 2) | ab];
                prev_ab = ab;
                if (raw != 0) {                     // 0 = illegal double transition, ignore
                    if (accum != 0 && ((raw > 0) != (accum > 0))) {
                        accum = raw;                // reversal resets, per stock
                    } else {
                        accum += raw;
                    }
                    if (accum >= LM_ENC_STEPS_PER_DETENT
                            || accum <= -LM_ENC_STEPS_PER_DETENT) {
                        const bool cw = (accum > 0) != (LM_ENC_INVERT != 0);
                        accum = 0;                  // zero, don't carry a remainder
                        out_line(cw ? "enc cw\n" : "enc ccw\n");
                    }
                }
            }
        }

        if (have_enc_sw) {
            uint8_t lv = (uint8_t)gpio_get_level(LM_PIN_ENC_SW);
            if (lv != sw_state) {
                sw_state = lv;
                out_line("enc %s\n", lv ? "release" : "press");   // active-low
            }
        }

        // Touch and rear report BOTH edges as `touch down`/`touch up` (and likewise for
        // rear), not a single bare line. A bare line carries no duration, so the host's
        // recogniser could never fire a `hold` binding on these two controls — the tap had
        // no measurable length. docs/PROTOCOL.md's `parse_device_line` accepts both
        // spellings, so this costs nothing and makes hold work.
        uint8_t tv = have_touch ? (uint8_t)gpio_get_level(LM_PIN_TOUCH) : touch_state;
        if (have_touch && tv != touch_state) {
            touch_state = tv;
            const bool touched = LM_TOUCH_ACTIVE_HIGH ? (tv != 0) : (tv == 0);
            // Debounce the press edge only; the release must always be reported, or a
            // suppressed `up` would leave the host believing the control is still held.
            if (!touched) {
                out_line("touch up\n");
            } else if ((TickType_t)(now - last_touch) >= pdMS_TO_TICKS(LM_AUX_TAP_MS)) {
                last_touch = now;
                out_line("touch down\n");
            }
        }

        uint8_t rv = have_rear ? (uint8_t)gpio_get_level(LM_PIN_REAR) : rear_state;
        if (have_rear && rv != rear_state) {
            rear_state = rv;
            const bool pressed = (rv == 0);          // active low
            if (!pressed) {
                out_line("rear up\n");
            } else if ((TickType_t)(now - last_rear) >= pdMS_TO_TICKS(LM_AUX_TAP_MS)) {
                last_rear = now;
                out_line("rear down\n");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(LM_AUX_POLL_MS));
    }
}

static void aux_inputs_start(void)
{
    if (LM_ENABLE_REAR) {
        // GPIO 2 carries two hazards, both from stock's rear-button rescue path.
        //
        // 1. It can arrive under an RTC hold (rtc_gpio_hold_en), which makes reads
        //    meaningless — the same failure class that kept the LED rail dark.
        // 2. Stock arms a ULP-RISCV watcher that forces SW_SYS_RST when this pin goes
        //    low. It is armed by the sys.bootloader RPC, which our own flashing script
        //    calls, so it may be running right now. With it live, pressing the rear
        //    button reboots the pad.
        //
        // Hazard 1 is handled here. Hazard 2 is NOT: halting the ULP needs the `ulp`
        // component, which ESP-IDF only exposes when CONFIG_ULP_COPROC_ENABLED is set,
        // and enabling that purely to stop a coprocessor we never use is a poor trade.
        // Since REAR is opt-in, whoever enables it owns that problem: either clear
        // RTC_CNTL_ULP_CP_SLP_TIMER_EN, or power-cycle the pad without going through
        // sys.bootloader first, since that RPC is what arms the watcher.
        rtc_gpio_hold_dis(LM_PIN_REAR);
        rtc_gpio_deinit(LM_PIN_REAR);
    }

    // Stock disables BOTH internal pulls on all of these — the board has external
    // pulls, and fighting them with an internal pull-up skews the thresholds.
    if (LM_ENABLE_TOUCH)  aux_cfg_input(LM_PIN_TOUCH,  GPIO_PULLDOWN_DISABLE, GPIO_PULLUP_DISABLE);
    if (LM_ENABLE_REAR)   aux_cfg_input(LM_PIN_REAR,   GPIO_PULLDOWN_DISABLE, GPIO_PULLUP_DISABLE);
    if (LM_ENABLE_ENCODER) {
        aux_cfg_input(LM_PIN_ENC_A,  GPIO_PULLDOWN_DISABLE, GPIO_PULLUP_DISABLE);
        aux_cfg_input(LM_PIN_ENC_B,  GPIO_PULLDOWN_DISABLE, GPIO_PULLUP_DISABLE);
    }
    if (LM_ENABLE_ENCODER) aux_cfg_input(LM_PIN_ENC_SW, GPIO_PULLDOWN_DISABLE, GPIO_PULLUP_DISABLE);
    xTaskCreatePinnedToCore(aux_task, "lm_aux", 3072, NULL, 4, NULL, 1);
    ESP_LOGW(TAG, "UNVERIFIED inputs enabled: touch=%d rear=%d encA=%d encB=%d encSW=%d",
             LM_PIN_TOUCH, LM_PIN_REAR, LM_PIN_ENC_A, LM_PIN_ENC_B, LM_PIN_ENC_SW);
}

#else  /* !LM_ENABLE_AUX */

// Default build: these pins are never configured and never read.
static void aux_inputs_start(void)
{
    ESP_LOGW(TAG, "unverified inputs (encoder/touch/rear) compiled OUT "
                  "- set LM_ENABLE_UNVERIFIED_INPUTS=1 once pins are confirmed");
}

#endif /* LM_ENABLE_AUX */

// ---- battery: MAX77972 fuel gauge, read-only --------------------------------
//
// See the big evidence block near the top of the file for where every address
// below comes from. Structure of this section:
//
//   batt_read16()   one register read, no retries beyond the driver's own
//   batt_sample()   the five reads + the arithmetic, into a snapshot struct
//   batt_task()     poll, and emit `batt <pct> <0|1>` ONLY when it changes
//   cmd_batt()      the on-demand report
//
// The published state is a small struct guarded by the same discipline as the
// rest of the file: written only by the battery task, read by the command loop.
// It is four scalars and a bool, so a torn read can only ever mix two adjacent
// samples of the same battery -- not worth a mutex, and a mutex here could block
// the command loop behind an I2C timeout, which is exactly what we don't want.

#if LM_ENABLE_BATTERY

typedef struct {
    bool     valid;      // false = unknown; NEVER report a percentage when false
    uint8_t  percent;    // 0..100, from REPSOC
    uint8_t  charging;   // 0/1, from chg_dtls
    uint16_t mv;         // cell millivolts, for diagnostics
    uint16_t repsoc;     // raw register values, so a human can check the decode
    uint16_t vfsoc;
    uint16_t chgdet;
    uint16_t status;
} batt_state_t;

static batt_state_t s_batt;          // published reading
static int  s_batt_scl = -1;         // which SCL GPIO actually answered
static bool s_batt_live;             // bus is up and the gauge ACKed at least once

static i2c_master_bus_handle_t s_i2c_bus;
static i2c_master_dev_handle_t s_fg_dev;

// One 16-bit register, LSB first, exactly as stock's transport assembles it.
static esp_err_t batt_read16(uint8_t reg, uint16_t *out)
{
    if (!s_fg_dev) return ESP_ERR_INVALID_STATE;
    uint8_t rx[2] = {0, 0};
    esp_err_t e = i2c_master_transmit_receive(s_fg_dev, &reg, 1, rx, sizeof(rx), 50);
    if (e != ESP_OK) return e;
    *out = (uint16_t)(rx[0] | ((uint16_t)rx[1] << 8));
    return ESP_OK;
}

// Bring the bus up on `scl` and see whether the gauge ACKs. Leaves the bus
// installed on success; fully tears it down on failure, so a wrong pin guess
// does not leave a pad configured as an open-drain output.
static bool batt_try_bus(int scl)
{
    i2c_master_bus_config_t bcfg = {
        .i2c_port = -1,                 // let the driver pick a free port
        .sda_io_num = (gpio_num_t)LM_I2C_SDA_GPIO,
        .scl_io_num = (gpio_num_t)scl,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags = { .enable_internal_pullup = true },
    };
    if (i2c_new_master_bus(&bcfg, &s_i2c_bus) != ESP_OK) {
        s_i2c_bus = NULL;
        return false;
    }

    // Address-only probe. This is a write of the address byte and nothing else --
    // it cannot alter a register, which is the whole reason it is safe to try a
    // pin the documentation disagrees with.
    if (i2c_master_probe(s_i2c_bus, LM_FG_ADDR, 100) != ESP_OK) {
        i2c_del_master_bus(s_i2c_bus);
        s_i2c_bus = NULL;
        // Put both pads back to plain inputs so nothing is left driving them.
        gpio_reset_pin((gpio_num_t)LM_I2C_SDA_GPIO);
        gpio_reset_pin((gpio_num_t)scl);
        return false;
    }

    i2c_device_config_t dcfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = LM_FG_ADDR,
        .scl_speed_hz    = LM_I2C_HZ,
    };
    if (i2c_master_bus_add_device(s_i2c_bus, &dcfg, &s_fg_dev) != ESP_OK) {
        s_fg_dev = NULL;
        i2c_del_master_bus(s_i2c_bus);
        s_i2c_bus = NULL;
        return false;
    }
    return true;
}

// Read the five registers and turn them into a snapshot. Returns false, and
// leaves *out untouched, on any read failure or implausible reading.
static bool batt_sample(batt_state_t *out)
{
    uint16_t repsoc = 0, vcell = 0, chgdet = 0, vfsoc = 0, status = 0;

    if (batt_read16(LM_REG_REPSOC, &repsoc) != ESP_OK) return false;
    if (batt_read16(LM_REG_VCELL, &vcell) != ESP_OK) return false;
    if (batt_read16(LM_REG_CHG_DETAILS, &chgdet) != ESP_OK) return false;
    // The next two are diagnostics only; a failure on them still fails the sample,
    // because a bus that half-works is a bus we should not be trusting.
    if (batt_read16(LM_REG_VFSOC, &vfsoc) != ESP_OK) return false;
    if (batt_read16(LM_REG_STATUS, &status) != ESP_OK) return false;

    // 78.125 uV per LSB -> millivolts. 64-bit because 65535 * 78125 overflows u32.
    uint32_t mv = (uint32_t)(((uint64_t)vcell * 78125ULL) / 1000000ULL);
    if (mv < LM_BATT_MV_MIN || mv > LM_BATT_MV_MAX) return false;

    // REPSOC is percent in 1/256 steps. Round rather than truncate, then clamp:
    // a healthy m5 gauge can read slightly over 100 % right after a full charge.
    uint32_t pct = ((uint32_t)repsoc + 128u) >> 8;
    if (pct > 110u) return false;          // not a percentage; distrust the read
    if (pct > 100u) pct = 100u;

    uint8_t chg_dtls = (uint8_t)((chgdet >> 8) & 0x0Fu);

    out->valid    = true;
    out->percent  = (uint8_t)pct;
    out->charging = (chg_dtls <= 2u) ? 1u : 0u;
    out->mv       = (uint16_t)mv;
    out->repsoc   = repsoc;
    out->vfsoc    = vfsoc;
    out->chgdet   = chgdet;
    out->status   = status;
    return true;
}

static void batt_task(void *arg)
{
    (void)arg;

    // Try the vendor-attested SCL first, then the value docs/HARDWARE.md gives.
    // A couple of attempts each, because a boot-time bus glitch should not
    // permanently write the feature off.
    const int scl_candidates[2] = { LM_I2C_SCL_GPIO, LM_I2C_SCL_ALT };
    for (int attempt = 0; attempt < 2 && !s_batt_live; attempt++) {
        for (int i = 0; i < 2; i++) {
            if (batt_try_bus(scl_candidates[i])) {
                s_batt_scl  = scl_candidates[i];
                s_batt_live = true;
                break;
            }
        }
        if (!s_batt_live) vTaskDelay(pdMS_TO_TICKS(200));
    }

    if (!s_batt_live) {
        // Loud, once. The pad keeps working; `ver` will say batt=unknown.
        out_line("# batt no ack from 0x%02x on SDA=%d with SCL=%d or %d "
                 "- battery reporting disabled, everything else unaffected\n",
                 LM_FG_ADDR, LM_I2C_SDA_GPIO, LM_I2C_SCL_GPIO, LM_I2C_SCL_ALT);
        vTaskDelete(NULL);
        return;
    }

    out_line("# batt gauge 0x%02x acked on SDA=%d SCL=%d%s\n",
             LM_FG_ADDR, LM_I2C_SDA_GPIO, s_batt_scl,
             (s_batt_scl == LM_I2C_SCL_ALT)
                 ? " (the docs/HARDWARE.md pin, not the vendor firmware's 18)"
                 : " (the vendor firmware's pin; docs/HARDWARE.md says 9)");

    int  fails = 0;
    bool reported_unknown = false;
    bool have_reported = false;
    uint8_t last_pct = 0, last_chg = 0;

    for (;;) {
        batt_state_t s = {0};
        if (batt_sample(&s)) {
            fails = 0;
            reported_unknown = false;
            s_batt = s;
            // Emit only on change. At 115200 the link is the scarce resource, and
            // a percentage that has not moved is not news.
            if (!have_reported || s.percent != last_pct || s.charging != last_chg) {
                have_reported = true;
                last_pct = s.percent;
                last_chg = s.charging;
                out_line("batt %u %u\n", (unsigned)s.percent, (unsigned)s.charging);
            }
        } else if (++fails >= LM_BATT_FAILS_MAX) {
            s_batt.valid = false;
            have_reported = false;      // re-announce once the gauge comes back
            if (!reported_unknown) {
                reported_unknown = true;
                out_line("# batt read failed %d times - reporting unknown, "
                         "no percentage emitted\n", fails);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(LM_BATT_POLL_MS));
    }
}

static void batt_start(void)
{
    xTaskCreatePinnedToCore(batt_task, "lm_batt", 4096, NULL, 3, NULL, 1);
}

#else  /* !LM_ENABLE_BATTERY */

static void batt_start(void)
{
    ESP_LOGW(TAG, "battery reporting compiled OUT - set LM_ENABLE_BATTERY=1");
}

#endif /* LM_ENABLE_BATTERY */

// What `ver` advertises, so the host can tell "this firmware has no battery
// support at all" from "it has support but the gauge is not answering".
static const char *batt_cap(void)
{
#if LM_ENABLE_BATTERY
    if (!s_batt_live)  return "unknown";   // bus never came up / gauge silent
    return s_batt.valid ? "ok" : "unknown";
#else
    return "none";
#endif
}

// `batt` — report the current reading on demand. Always answers; answers
// "unknown" rather than inventing a number.
static void cmd_batt(void)
{
#if LM_ENABLE_BATTERY
    batt_state_t s = s_batt;             // one copy, so the reply is self-consistent
    if (!s.valid) {
        out_line("ok batt unknown live=%d scl=%d\n", s_batt_live ? 1 : 0, s_batt_scl);
        return;
    }
    out_line("ok batt %u %u mv=%u repsoc=%04x vfsoc=%04x chgdet=%04x "
             "chgdtls=%x status=%04x scl=%d\n",
             (unsigned)s.percent, (unsigned)s.charging, (unsigned)s.mv,
             s.repsoc, s.vfsoc, s.chgdet, (unsigned)((s.chgdet >> 8) & 0xF),
             s.status, s_batt_scl);
#else
    out_line("ok batt none\n");
#endif
}

// ---- serial command parser -------------------------------------------------

static int hexval(char ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

// Parse exactly 6 hex digits at `s` into r/g/b. Rejects non-hex rather than
// silently reading it as 0 (strtol's behaviour), which matters now that a single
// `kf` line carries 13 colours and one typo should not paint the pad black.
static int parse_hex(const char *s, uint8_t *r, uint8_t *g, uint8_t *b)
{
    int v[6];
    for (int i = 0; i < 6; i++) {
        v[i] = hexval(s[i]);
        if (v[i] < 0) return -1;
    }
    *r = (uint8_t)((v[0] << 4) | v[1]);
    *g = (uint8_t)((v[2] << 4) | v[3]);
    *b = (uint8_t)((v[4] << 4) | v[5]);
    return 0;
}

// `kf`/`uf` — set a whole zone from one line with a SINGLE strip refresh.
//
// docs/PROTOCOL.md: `kf <rrggbb>x13`, `uf <rrggbb>x8`. Colours are in STRIP index
// order, the same numbering `k <i>` / `u <i>` take, so the host's existing
// logical->strip translation is reused unchanged.
//
// Why it matters: the host measured a full-pad animated gradient at ~7.0 KB/s,
// 61% of the 115200 link, using per-pixel writes — and that also forced one strip
// refresh per pixel. One `kf` line is ~94 bytes for the whole key zone with one
// refresh.
//
// Both spellings are accepted: 13 space-separated 6-hex tokens (the documented
// form) and one concatenated 78-hex-digit blob (unambiguous, since the token
// lengths cannot collide). Nothing is written to the strip unless EVERY colour
// parses, so a malformed line leaves the pad exactly as it was.
static void handle_frame(int is_key, char *tok[], int nt)
{
    const int n = is_key ? KEY_N : AMB_N;
    uint8_t rgb[KEY_N][3];          // KEY_N >= AMB_N, so this sizes both

    if (nt == 2 && strlen(tok[1]) == (size_t)(6 * n)) {
        for (int i = 0; i < n; i++) {
            if (parse_hex(tok[1] + 6 * i, &rgb[i][0], &rgb[i][1], &rgb[i][2]) != 0) {
                out_line("err badhex\n");
                return;
            }
        }
    } else if (nt == n + 1) {
        for (int i = 0; i < n; i++) {
            if (parse_hex(tok[i + 1], &rgb[i][0], &rgb[i][1], &rgb[i][2]) != 0) {
                out_line("err badhex\n");
                return;
            }
        }
    } else {
        out_line("err count want %d\n", n);
        return;
    }

    for (int i = 0; i < n; i++) {
        if (is_key) {
            s_kbuf[i][0] = rgb[i][0]; s_kbuf[i][1] = rgb[i][1]; s_kbuf[i][2] = rgb[i][2];
            push_key(i);
        } else {
            s_abuf[i][0] = rgb[i][0]; s_abuf[i][1] = rgb[i][1]; s_abuf[i][2] = rgb[i][2];
            push_amb(i);
        }
    }
    // Exactly one refresh, of the one strip that changed.
    led_strip_handle_t h = is_key ? s_keys : s_amb;
    if (h) led_strip_refresh(h);
    out_line("ok %s %d\n", is_key ? "kf" : "uf", n);
}

// `mscan` — dump the RAW matrix bitmap, one nibble per row, bit c = column c.
// This is the tool for resolving the MTX_TO_LOGICAL column/row-order assumption:
// hold a known key, run mscan, see which raw slot is set.
// Reports the snapshot the SCAN TASK published, rather than strobing the rows
// here — two writers driving the row pins at once would give a garbled reading.
// Wakes the task, waits for it to take a fresh sample, then prints that.
static void cmd_mscan(void)
{
    if (s_scan_task) {
        xTaskNotifyGive(s_scan_task);
        vTaskDelay(pdMS_TO_TICKS(MTX_SCAN_PERIOD_MS * 4 + 4));
    }
    uint8_t raw[MTX_ROWS];
    memcpy(raw, s_mtx_last_raw, sizeof(raw));

    out_line("ok mscan r0=%x r1=%x r2=%x r3=%x "
             "(rows GPIO 46,17,40,47; bit c = col c of GPIO 13,5,21,1)\n",
             raw[0] & 0xf, raw[1] & 0xf, raw[2] & 0xf, raw[3] & 0xf);
}

#define LM_MAX_TOK 20   // `kf` is 1 + 13 tokens; leave room

static void handle_line(char *line)
{
    // tokenize
    char *tok[LM_MAX_TOK]; int nt = 0;
    for (char *p = strtok(line, " \t"); p && nt < LM_MAX_TOK; p = strtok(NULL, " \t")) tok[nt++] = p;
    if (nt == 0) return;

    if (!strcmp(tok[0], "clear")) { all_off(); out_line("ok clear\n"); return; }
    if (!strcmp(tok[0], "demo"))  { demo_burst(120); all_off(); out_line("ok demo\n"); return; }
    if (!strcmp(tok[0], "dump"))  { dump_regs("live"); out_line("ok dump\n"); return; }
    if (!strcmp(tok[0], "mscan")) { cmd_mscan(); return; }
    if (!strcmp(tok[0], "batt"))  { cmd_batt(); return; }
    if (!strcmp(tok[0], "ver")) {
        // Lets the host detect batched-frame support instead of guessing; it falls
        // back to per-pixel `k`/`u` when this is absent or reports frames=0.
        // batt=none|unknown|ok distinguishes "no battery support in this build"
        // from "support present, gauge not answering" from "reading is live".
        out_line("ok ver 2 keys=%d under=%d frames=1 events=key%s%s%s%s batt=%s\n",
                 KEY_N, AMB_N,
                 (LM_ENABLE_ENCODER ? ",enc" : "")
                 // Concatenating at compile time keeps this honest: the string lists
                 // exactly what the built firmware emits, not what it could emit.
                 , (LM_ENABLE_TOUCH ? ",touch" : ""),
                 (LM_ENABLE_REAR ? ",rear" : ""),
#if LM_ENABLE_BATTERY
                 ",batt",
#else
                 "",
#endif
                 batt_cap());
        return;
    }
    if (!strcmp(tok[0], "tflash")) { status_flash(nt >= 2 ? atoi(tok[1]) : 6); out_line("ok tflash\n"); return; }
    if (!strcmp(tok[0], "t") && nt >= 3) {
        int duty = atoi(tok[2]);
        if (!strcmp(tok[1], "all")) { for (int i = 0; i < 3; i++) set_status(i, duty); out_line("ok t all %d\n", duty); return; }
        int i = atoi(tok[1]);
        if (i < 0 || i > 2) { out_line("err index\n"); return; }
        set_status(i, duty); out_line("ok t %d %d\n", i, duty); return;
    }
    if (!strcmp(tok[0], "bright") && nt >= 2) {
        int v = atoi(tok[1]); if (v < 0) v = 0; if (v > 255) v = 255; s_bright = (uint8_t)v;
        for (int i=0;i<KEY_N;i++) push_key(i);
        for (int i=0;i<AMB_N;i++) push_amb(i);
        refresh_all();
        out_line("ok bright %d\n", s_bright); return;
    }

    // v2 batched whole-zone frames
    if (!strcmp(tok[0], "kf")) { handle_frame(1, tok, nt); return; }
    if (!strcmp(tok[0], "uf")) { handle_frame(0, tok, nt); return; }

    // k/u commands
    int is_key = !strcmp(tok[0], "k");
    int is_amb = !strcmp(tok[0], "u");
    if ((is_key || is_amb) && nt >= 3) {
        uint8_t r,g,b;
        if (parse_hex(tok[2], &r,&g,&b) != 0) { out_line("err badhex\n"); return; }
        int n = is_key ? KEY_N : AMB_N;
        if (!strcmp(tok[1], "all")) {
            for (int i = 0; i < n; i++) is_key ? set_key(i,r,g,b) : set_amb(i,r,g,b);
            refresh_all();
            out_line("ok %s all %02x%02x%02x\n", tok[0], r,g,b); return;
        }
        int i = atoi(tok[1]);
        if (i < 0 || i >= n) { out_line("err index\n"); return; }
        is_key ? set_key(i,r,g,b) : set_amb(i,r,g,b);
        refresh_all();
        out_line("ok %s %d %02x%02x%02x\n", tok[0], i, r,g,b); return;
    }

    out_line("err unknown\n");
}

void app_main(void)
{
    ESP_LOGW(TAG, "=== CM2 custom firmware v2 (LEDs + input events) ===");

    // 0. Output mutex first, so every line from here on is atomic.
    s_out_mux = xSemaphoreCreateMutex();

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

    // 6. v2 inputs. Started only now that the serial link is up, so no event line
    //    can be emitted before the host-visible stream exists. Pinned to core 1:
    //    the main task and the USB-Serial-JTAG driver live on core 0, so scanning
    //    cannot delay command handling, and command handling (a `demo` burst
    //    blocks for seconds) cannot delay scanning.
    mtx_gpio_init();
    xTaskCreatePinnedToCore(mtx_scan_task, "lm_scan", 3072, NULL, 5, &s_scan_task, 1);
    aux_inputs_start();

    // 7. Battery. Lowest priority of the three, and last, because it is the only
    //    one that can block on an external device: its very first act is an I2C
    //    probe that may time out. Nothing above it may wait on that.
    batt_start();

    out_line("ok ready keys=%d under=%d v2 frames=1\n", s_keys?KEY_N:0, s_amb?AMB_N:0);

    // Big enough for a `kf` line: "kf" + 13 * 7 + NUL = 94 bytes minimum.
    char line[160]; int len = 0;
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
