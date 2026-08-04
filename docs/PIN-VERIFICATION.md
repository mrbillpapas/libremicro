# Pin verification: touch pad, rotary encoder, rear button

Re-verification of the three GPIO assignments that `docs/HARDWARE.md` marks
**provisional**, plus the resolution of the GPIO 2 conflict that was blocking the
power-management work.

**Method:** static analysis of the vendor firmware image that was actually running on the
device — `firmware_v0.6.1_merged.bin`, which self-identifies as `Creator Micro 2`,
`v0.6.1`, ESP-IDF `5.3.2.250210`. Nothing was flashed and no hardware was touched. The
vendor image is not in this repo and must stay out of it.

This build is compiled with `ESP_ERROR_CHECK` assert strings intact, so every
`gpio_config` / `gpio_isr_handler_add` call site carries the **literal source expression**
including the vendor's own pin symbol name (`PIN_ENC_A`, `PIN_TOUCH_OUT_L`, …) and the
`wl_io.cpp` line number. That turns pin identification from inference into direct reading:
the assert string names the symbol, and the immediate operand in the adjacent instruction
gives its value.

**Method validated against known-good facts.** The same six `gpio_config` call sites also
reproduce two already-CONFIRMED entries in `HARDWARE.md` — `init_top_board_power_gpio()`
(`pin_bit_mask` high dword `0x70` → GPIO 36/37/38, `mode = 2` = `GPIO_MODE_OUTPUT`) and
`init_charge_enable_gpio()` (high dword `0x1000` → GPIO 44, `mode = 3` =
`GPIO_MODE_INPUT_OUTPUT`, followed by `gpio_set_level(44, 0)`). Those two independently
confirm that the `gpio_config_t` field offsets and the 64-bit `pin_bit_mask` split are being
read correctly.

## Summary

| Signal | Was (provisional) | **Verified** | Direction / polarity | Confidence |
|---|---|---|---|---|
| Rear button (`PIN_REAR_BTN`) | GPIO 14 | **GPIO 2** | input, **active LOW** | **Very high** |
| Touch pad (`PIN_TOUCH_OUT_L`) | GPIO 2 | **GPIO 14** | input, **active HIGH** (see caveat) | pin: **very high**; polarity: **medium-high** |
| Encoder A (`PIN_ENC_A`) | GPIO 12 | **GPIO 12** | input, ANYEDGE | **Very high** |
| Encoder B (`PIN_ENC_B`) | unknown | **GPIO 11** | input, ANYEDGE | **Very high** |
| Encoder switch (`PIN_ENC_SWITCH`) | unknown | **GPIO 4** | input, **active LOW** | **Very high** |
| ext0 deep-sleep wake pin | "GPIO 2, conflicts" | **GPIO 2 = the rear button** | wake on level **0** | **Very high** |
| USB detect (`PIN_USB_DETECT`) — bonus | not recorded | **GPIO 42** | input, **active LOW** | **Very high** |

**The provisional touch/rear assignments were swapped.** Touch is 14, rear is 2. Once
swapped, the GPIO 2 conflict evaporates: GPIO 2 is the rear button, and the rear button is
exactly the thing stock uses as the ext0 wake source. Both citations were right; they were
about the same signal.

Every pin below carries **no internal pull-up and no internal pull-down** at runtime
(`pull_up_en = 0`, `pull_down_en = 0`), is `GPIO_MODE_INPUT`, uses `GPIO_INTR_ANYEDGE`, and
gets a hardware pin glitch filter. Firmware v2 must match that; see
[Common configuration](#common-configuration-stock-does-the-same-thing-to-all-five-pins).

Cross-version check was **not available** — see [Why the second image doesn't
corroborate](#why-the-second-image-doesnt-corroborate).

---

## 1. Rear button — GPIO 2

### Conclusion

`PIN_REAR_BTN` = **GPIO 2**. Input, no internal pulls, `GPIO_INTR_ANYEDGE`, hardware pin
glitch filter enabled. **Pressed = level LOW**, so there is an external pull-up (or the
touch/top board supplies one — see the RTC pull-up note below).

### Confidence

**Very high.** Five independent sites agree, three of which name the symbol in a string.

### Evidence

**a. `wl_io::init_rear_button_gpio()` @ `0x42009054`** (`src/drivers/wl_io.cpp:114`, `:118`)

```
memset(&io, 0, 24)
io.pin_bit_mask = 0x00000004      ; movi.n a8,4 ; s32i.n a8,a1,0   -> bit 2  = GPIO 2
io.pin_bit_mask_hi = 0
io.mode          = 1              ; GPIO_MODE_INPUT
io.pull_up_en    = 0              ; left zero by the memset
io.pull_down_en  = 0              ; left zero by the memset
io.intr_type     = 3              ; GPIO_INTR_ANYEDGE
gpio_config(&io)                                          <- assert expr "gpio_config(&io)", line 114
install_pin_glitch_filter(2)                              ; movi.n a10,2 ; call8 0x42008fe4
gpio_isr_handler_add(2, wl_io::rear_button_irq_handler, this)   <- line 118
```

The assert string at DROM `0x3c0e0d0c` is
`gpio_isr_handler_add(static_cast<gpio_num_t>(PIN_REAR_BTN), wl_io::rear_button_irq_handler, this)`
and the `gpio_num_t` argument register is loaded with the immediate **2** three instructions
earlier (`0x4200909b`). The `pin_bit_mask` in the same function is `0x4` = bit 2. Two
encodings of the same number in the same function.

**b. `wl_io::rear_button_irq_handler` @ `0x40375d7c` (IRAM)** — polarity

```
gpio_get_level(2)                 ; movi a10,2 ; callx8 -> 0x4207d0e8 (gpio_get_level)
a11 = 1 ; a8 = 0
movnez a11, a8, a10               ; if (level != 0) a11 = 0   =>  a11 = (level == 0)
this->[37] = a11
post io event id 6, payload { a11 }
```

`movnez` semantics were cross-checked against a known reference in the same image
(`init_encoder_gpio`'s `gpio_install_isr_service` error tolerance, `0x420091df`), so the
inversion is not a decoding artefact.

**c. Consumer agrees.** `wl_selftest`'s `event_handler` @ `0x4200c375`:
`bnei a4, 6, done` then `if (data[0] != 0) log "Selftest: Rear button pressed"`. So
`payload != 0` means pressed, and the ISR sets `payload = (level == 0)`.

**d. The power-off recipe treats GPIO 2 as a pulled-up RTC input.**
`init_pm_executor_recipes()` ENTER-OFF lambda @ `0x4201c788` iterates a 2-entry DROM array at
`0x3c201058` whose contents are `{2, 20}` (GPIO 2 and GPIO 20 = USB D+), and for each pin
calls, in order:

```
rtc_gpio_hold_dis(pin)
rtc_gpio_init(pin)                                     <- "rtc_gpio_init(pin)", line 162
rtc_gpio_set_direction(pin, RTC_GPIO_MODE_INPUT_ONLY)  <- line 163  (mode arg = 0)
rtc_gpio_pullup_en(pin)                                <- line 164
rtc_gpio_pulldown_dis(pin)                             <- line 165
```

Function identities confirmed from the IDF `rtc_io.c` name strings at `0x42042160`–`0x4204219c`
(`rtc_gpio_init`, `rtc_gpio_set_direction`, `rtc_gpio_pullup_en`, `rtc_gpio_pulldown_dis`,
`rtc_gpio_hold_dis`, `rtc_gpio_get_level`, …). An **RTC pull-up on GPIO 2** is only
meaningful for an active-low button.

**e. The ULP rescue path uses GPIO 2.** See §5.

### How to confirm on hardware

Boot firmware v2 (or stock) and read GPIO 2 while pressing the rear button: it should idle
HIGH and read LOW while held. From the existing `dump` command path, print
`gpio_get_level(2)` in a loop. Also verify the button does **not** move GPIO 14.

---

## 2. Touch pad — GPIO 14

### Conclusion

`PIN_TOUCH_OUT_L` = **GPIO 14**. Input, no internal pulls, `GPIO_INTR_ANYEDGE`, hardware
pin glitch filter. Confirms the "external touch IC, plain digital interrupt, *not* the
ESP32 touch peripheral" reading — there is no `touch_pad_*` / `touch_element` API use
anywhere in the image.

**Polarity: stock treats level HIGH as "touched"**, i.e. active **HIGH**, contradicting the
`_L` in the symbol name. See the caveat.

### Confidence

**Pin: very high.** Three sites, one naming the symbol.
**Polarity: medium-high** — the code path is unambiguous, but the symbol name argues the
other way and I cannot rule out that the value being propagated is "not idle" rather than
"pressed".

### Evidence

**a. `wl_io::init_touchpad_gpio()` @ `0x420090b8`** (`wl_io.cpp:282`, `:293`)

```
memset(&io, 0, 24)
io.pin_bit_mask = 0x00004000      ; l32r from literal 0x42000834  -> bit 14 = GPIO 14
io.mode          = 1              ; GPIO_MODE_INPUT
io.pull_up_en    = 0
io.pull_down_en  = 0
io.intr_type     = 3              ; GPIO_INTR_ANYEDGE
gpio_config(&io)                                          <- line 282
install_pin_glitch_filter(14)                             ; movi a10,14 ; call8 0x42008fe4
gpio_isr_handler_add(14, wl_io::touchpad_irq_handler, this)    <- line 293
```

Assert string at DROM `0x3c0e0da0`:
`gpio_isr_handler_add(static_cast<gpio_num_t>(PIN_TOUCH_OUT_L), wl_io::touchpad_irq_handler, this)`,
with immediate **14** in the pin register at `0x420090fe`, and `pin_bit_mask` `0x4000`
= bit 14 in the same function.

**b. GPIO 14 is used for nothing else.** An exhaustive scan of every `movi`-of-14 into the
first argument register across both code segments finds exactly three app-side sites: the
two above and the `gpio_get_level(14)` in the ISR. No `rtc_gpio_*`, no `gpio_hold_en`, no
pull configuration, no LEDC, no matrix use.

**c. `wl_io::touchpad_irq_handler` @ `0x40375d30` (IRAM)** — polarity

```
gpio_get_level(14)
a11 = 0 ; a12 = 1
moveqz a12, a11, a10              ; if (level == 0) a12 = 0   =>  a12 = (level != 0)
post io event id 2, payload { 0, a12 }
```

The value handed to the event is the **raw level**, with no inversion — unlike the rear
button and the encoder switch in the same file, which both use `movnez` to compute
`(level == 0)`. The asymmetry is deliberate in the compiled code.

**d. Both consumers treat `payload[1] != 0` as pressed.**

- `wl_touch_btn::init()` @ `0x4200ee9c` registers handler `0x4200eef0` on the io event base
  for **event id 2** (`movi.n a11, 2` at `0x4200eebb`) — which is the id the touch ISR
  posts, a clean cross-check on the ISR→module wiring. That handler
  (`bnei a4, 2` at `0x4200eef3`) forwards `payload[1]` into `wl_button::update`
  (`0x4203d0d8` via `0x4200ecd8`), and `wl_button::update` branches on
  `beqz a3` → released / else → pressed. No inversion anywhere in that chain.
- `wl_selftest` @ `0x4200c34d`: `bnei a4, 2, next` then
  `if (data[1] != 0) log "Selftest: Touch button pressed"`.

**e. Supporting argument for active-HIGH.** The vendor selftest requires the touch button
to be exercised before it passes (`"Selftest: Waiting for %d keys, encoder rotation/press,
touch button, rear button"`). If the pad idled HIGH with this polarity, that selftest item
would self-satisfy at boot on every unit — something the vendor would very likely have
noticed. So the pad most likely idles LOW and pulses HIGH on touch.

### Caveat on `_L`

`PIN_TOUCH_OUT_L` reads like "OUT, active Low", which is what the provisional note in
`HARDWARE.md` assumed. Two readings survive the evidence:

1. **`_L` = "Left".** The touch pad sits at grid `(3,0)`, bottom-**left**, next to the
   three PWM status LEDs. A `TOUCH_OUT_L` / `TOUCH_OUT_R` style naming for a multi-output
   touch IC would explain both the suffix and the active-high sense. This is consistent with
   everything in the image.
2. **`_L` = active-low and stock's event payload means "idle/released", not "pressed".**
   This does not survive well — `wl_button::update` and the selftest both spell "pressed"
   for nonzero — but I cannot disprove it from the image alone, because the polarity of the
   external IC's output is a board fact, not a firmware fact.

Either way, the *behaviour to replicate* is unambiguous: sample GPIO 14 in the ANYEDGE ISR
and treat **HIGH as active**, which is what stock does.

### How to confirm on hardware

Read GPIO 14 with the pad untouched and touched. Expect idle LOW / touched HIGH. If it
comes back idle HIGH / touched LOW, invert the sense in firmware v2 and correct this
document — do **not** ship the active-high reading unverified, because a wrong polarity here
means a permanently-latched touch button.

While you are there, confirm the pin is actively driven (not floating): with the pad
untouched, temporarily enable an internal pull-up and then an internal pull-down and check
the level does not follow the pull. If it does follow, the IC output is open-drain and
firmware v2 needs a pull that stock does not configure.

---

## 3. Rotary encoder — A = GPIO 12, B = GPIO 11, switch = GPIO 4

### Conclusion

- `PIN_ENC_A` = **GPIO 12**
- `PIN_ENC_B` = **GPIO 11**
- `PIN_ENC_SWITCH` = **GPIO 4**, **active LOW**

All three: input, no internal pulls, `GPIO_INTR_ANYEDGE`, hardware pin glitch filter. A and
B share one ISR (`encoder_rotate_irq_handler`); the switch has its own.

### Confidence

**Very high** for all three pins and for the switch polarity. The quadrature *decode* is
fully recovered (below). Which physical rotation direction is "clockwise" is **not
determinable from the image** — see the direction subsection.

### Evidence

**`wl_io::init_encoder_gpio()` @ `0x4200911c`** (`wl_io.cpp:308, 315, 322, 341, 343, 346, 349`)

Three separate `gpio_config` calls, one per pin, each preceded by `memset(&io, 0, 24)`:

| Call site | `pin_bit_mask` | GPIO | line |
|---|---|---|---|
| `0x42009140` | `0x00001000` (literal `0x420007fc`) | **12** | 308 |
| `0x42009173` | `0x00000800` (literal `0x42000854`) | **11** | 315 |
| `0x420091a5` | `0x00000010` (`movi.n a8, 16`)   | **4**  | 322 |

all with `mode = 1` (INPUT), `pull_up_en = 0`, `pull_down_en = 0`, `intr_type = 3` (ANYEDGE).

Then, in order:

```
install_pin_glitch_filter(12)          ; 0x420091bf
install_pin_glitch_filter(11)          ; 0x420091c4
install_pin_glitch_filter(4)           ; 0x420091c9
gpio_install_isr_service(0)            ; 0x420091ce, tolerating 0x103 ESP_ERR_INVALID_STATE
                                       ;              and 0x10D ESP_ERR_NOT_ALLOWED
gpio_isr_handler_add(12, encoder_rotate_irq_handler, this)   <- "…PIN_ENC_A…",      line 343
gpio_isr_handler_add(11, encoder_rotate_irq_handler, this)   <- "…PIN_ENC_B…",      line 346
gpio_isr_handler_add( 4, encoder_button_irq_handler, this)   <- "…PIN_ENC_SWITCH…", line 349
```

Assert strings at DROM `0x3c0e0e34` (`PIN_ENC_A`), `0x3c0e0e98` (`PIN_ENC_B`),
`0x3c0e0efc` (`PIN_ENC_SWITCH`). In every case the immediate in the pin register matches the
`pin_bit_mask` bit set earlier in the same function.

Note the shared literal: `0x420007fc` holds `0x1000` and is used both as the *low* dword mask
here (→ GPIO 12) and as the *high* dword mask in `init_charge_enable_gpio` (→ GPIO 44, an
already-confirmed pin). The store offset distinguishes them, which is a useful consistency
check on the 64-bit mask reading.

**Switch polarity.** `encoder_button_irq_handler` @ `0x40375d0c` calls helper `0x40375cb0`:

```
gpio_get_level(4)
a2 = 1 ; a8 = 0 ; movnez a2, a8, a10     =>  return (level == 0)
```

then edge-detects against `this->[41]` and posts io event id 1 with `payload[1] = pressed`.
`wl_selftest` @ `0x4200c324`: `bnei a4, 1` then
`if (data[1] != 0) log "Selftest: Encoder button pressed"`. So **pressed = LOW**.

**Pins 12, 11 and 4 are used for nothing else** in the app: the only app-side uses of each
immediate in an argument register are the `gpio_config`, glitch-filter, `isr_handler_add`
and `gpio_get_level` sites above.

### Quadrature convention — what is and isn't determinable

`encoder_rotate_irq_handler` @ `0x40375cc8`:

```
a  = (gpio_get_level(12) != 0)         ; PIN_ENC_A
b  = (gpio_get_level(11) != 0)         ; PIN_ENC_B
delta = decoder_update(&this[44], /*arg1=*/ b, /*arg2=*/ a)
if (delta != 0) post io event id 0, payload { 0, (int8)delta }
```

`decoder_update` @ `0x4203d2d4`:

```
prev  = (this->u8[0] << 1) | this->u8[1]     ; u8[0] = previous B, u8[1] = previous A
now   = (b            << 1) |  a            ; so state = (B << 1) | A
this->u8[0..1] = { b, a }
raw = TABLE[prev * 4 + now]                 ; TABLE = 16 signed bytes at DROM 0x3c201bf4
if (raw == 0) return 0                      ; illegal / no transition
accum = this->i32[8]
   if (accum != 0 && sign(raw) != sign(accum)) accum = raw   ; reversal resets
   else                                       accum += raw
this->i32[8] = accum
if (abs(accum) < (int8)this->u8[12]) return 0                ; detent threshold
out = (accum >= 1) ? -1 : +1                                 ; NOTE: sign is flipped here
if (this->u8[13]) out = -out                                 ; direction-invert flag
this->i32[4] += out                                          ; running count
this->i32[8] = 0
return out
```

The transition table at DROM `0x3c201bf4` is the classic 16-entry form:

```
        now= 00   01   10   11
prev=00     0,  -1,  +1,   0
prev=01    +1,   0,   0,  -1
prev=10    -1,   0,   0,  +1
prev=11     0,  +1,  -1,   0
```

with `state = (B << 1) | A`. So the Gray sequence `00 → 01 → 11 → 10 → 00` — which is
**A leading B** — yields `raw = -1` per edge, and because the emit step flips the sign, that
rotation direction produces **`+1`** out of the decoder (before `u8[13]`).

**What this does not tell you:** whether A-leads-B is physically clockwise. That depends on
which terminal of the EC11-style encoder is wired to GPIO 12 versus GPIO 11 on the PCB, which
is not in the firmware. Stock resolves it in the keymap layer — action codes `0x1201` =
`ENC_CW`, `0x1202` = `ENC_CC`, `0x1203` = `ENC_CLK` (name table @ `0x42019b80`) — but I could
not locate the site that converts the signed delta into `0x1201`/`0x1202`, so even the
firmware-internal sign→CW mapping is unproven.

### How to confirm on hardware

1. **Pins.** Poll GPIO 12, 11 and 4 while turning and pressing. Expect 12 and 11 to produce
   a two-bit Gray sequence and 4 to go LOW on press.
2. **Direction.** Log `(b << 1) | a` on every edge, turn the knob one detent **clockwise as
   seen from the top of the device**, and record whether the state sequence is
   `00 → 01 → 11 → 10` (A leads B, decoder emits `+1`) or the reverse. Write the answer into
   `HARDWARE.md`; it is one measurement and it is the only way to get it.
3. **Detents per event.** Count how many events one physical click produces with your
   threshold. See the unproven list below.

---

## 4. The GPIO 2 conflict — resolved

### Conclusion

**There is no conflict.** GPIO 2 is the **rear button**, and stock uses that same pin as the
**ext0 deep-sleep wake source**, waking on level **0**. The provisional note put the touch
pad on GPIO 2 and the rear button on GPIO 14; those two were swapped. With touch moved to
GPIO 14, "GPIO 2 is the touch input" and "GPIO 2 is the ext0 wake pin" stop competing —
the second statement is simply the rear button's second role.

This is also electrically the only sane arrangement: `esp_sleep_enable_ext0_wakeup()`
requires an RTC-capable pad, and the button that wakes the device from OFF is the obvious
candidate for it.

### Confidence

**Very high.** The ext0 call site names the symbol, and the pin immediate is literal.

### Evidence

**a. The ext0 call itself** — `init_pm_executor_recipes()` ENTER-OFF lambda,
`src/wl_pm_recipes.cpp:208`, at `0x4201c936`:

```
a11 = 0            ; wake level = 0  (LOW)
a10 = 2            ; gpio_num = 2
callx8 [0x42001870]                     ; esp_sleep_enable_ext0_wakeup
ESP_ERROR_CHECK expr @0x3c0e3800 = "esp_sleep_enable_ext0_wakeup(REAR_BTN_PIN, 0)"
```

The assert string spells `REAR_BTN_PIN` and the immediate is `2`. That single site settles
the question.

**b. Stock waits for the rear button to be released before arming ext0** — `0x4201c913`:

```
for (i = 0; i <= 39; i++) {
    if (rtc_gpio_get_level(2) != 0) break;      ; wait for HIGH = released
    vTaskDelay(50 ms);
}
if (rtc_gpio_get_level(2) == 1) enable_ext0(2, 0);
else  log "PM recipe: OFF -> rear still LOW, skipping EXT0 wake"
                 / "[OFF] rear stuck LOW, ext0 SKIPPED"
```

A level-0 ext0 wake armed while the button is still held would wake the device immediately;
stock guards against exactly that with a ~2 s release window. This only makes sense if
GPIO 2 is a button that reads LOW when pressed. **Firmware v2's power-off path must replicate
this guard** or the device will bounce straight back out of deep sleep.

**c. The boot-side log confirms the pairing:** `"[BOOT] wake=EXT0 (rear button)"` (DROM
`0x3c0e317c`), alongside `"[BOOT] wake=ULP (usb plug)"` and
`"[BOOT] wake=none (reset or power-on)"`.

**d. The OFF recipe's RTC pin list is `{2, 20}`** (DROM `0x3c201058`) — the rear button and
USB D+, each given `rtc_gpio_hold_dis` → `rtc_gpio_init` → `INPUT_ONLY` →
`rtc_gpio_pullup_en` → `rtc_gpio_pulldown_dis`. GPIO 19 (USB D−) is handled separately with
`rtc_gpio_pulldown_en` / `rtc_gpio_pullup_dis` / `rtc_gpio_hold_en`, then
`esp_sleep_enable_ulp_wakeup()` for the USB-plug watcher
(`"PM recipe: OFF -> ULP watching D+ for a plug, rear via EXT0"`). Note the RTC pull-up on
GPIO 2 is applied *by the firmware* only in the OFF path — during normal run the pin has no
internal pull at all, so there is an external pull-up (or one on a rail that OFF cuts, which
is why the RTC pull-up is needed).

**e. Nothing in the image ever configures GPIO 2 as an output** or touches it outside the
rear-button, ext0, OFF-recipe and ULP-rescue paths.

### How to confirm on hardware

Power the device off through stock's OFF path, then press the rear button: it should wake.
Then, on firmware v2, arm `esp_sleep_enable_ext0_wakeup(GPIO_NUM_2, 0)` and verify (a) the
device sleeps, (b) a rear-button press wakes it, and (c) arming while the button is *held*
reproduces stock's "rear stuck LOW" skip rather than an instant re-wake.

---

## 5. `{"rescue":"rear_button_via_ulp"}` explained

The string in the stock RPC reply comes from `src/drivers/wl_bootloader_rescue.cpp`, and it
is **also GPIO 2**.

`arm()` @ `0x42008768`, on GPIO 2 throughout:

```
<0x4208d8f4>()                                       ; ULP pre-init / halt
rtc_gpio_deinit(2)
rtc_gpio_init(2)                     <- "ULP rescue: rtc_gpio_init failed: %d", line 78
rtc_gpio_set_direction(2, RTC_GPIO_MODE_INPUT_ONLY)
rtc_gpio_pullup_en(2)
rtc_gpio_pulldown_dis(2)
rtc_gpio_hold_en(2)
ulp_riscv_load_binary(blob @ DROM 0x3c201088, 446 bytes)   <- line 92
ulp_set_wakeup_period(0, 250000)                           <- line 104   (250 ms)
ulp_riscv_run()                                            <- line 111
log "ULP rescue armed (period=%u us, rear button -> SW_SYS_RST)"   <- line 115
```

The only caller is the **`sys.bootloader` RPC handler** (`0x4200811b`), which reports
`"rescue": "rear_button_via_ulp"` on success and `"none"` on failure (`0x42008120` /
`0x42008129`). So this is not a background watchdog — stock arms the ULP-RISCV rear-button
watcher *immediately before rebooting into the bootloader*, so that a rear-button press can
force a `SW_SYS_RST` and escape a stuck DFU/bootloader state.

Consequences for firmware v2 — **two hazards, both about inherited state, not about
replicating the feature**:

1. **GPIO 2 may arrive under an RTC hold.** `rtc_gpio_hold_en(2)` survives reset. Firmware
   v2 must call `rtc_gpio_hold_dis(GPIO_NUM_2)` (and `gpio_hold_dis` / `gpio_deep_sleep_hold_dis`,
   which it already does for the LED rail) *before* configuring the rear button, or the pin
   is stuck and the button appears dead. This is the same class of bug as the LED-rail hold
   already documented in `HARDWARE.md`.
2. **The ULP may still be running** with a 250 ms wakeup period, holding the rear-button pad
   in the RTC domain. If firmware v2 does not stop it, an unrelated rear-button press could
   trigger a system reset. `ulp_riscv_timer_stop()` / halting the ULP at boot is the safe
   move.

Firmware v2 does **not** need to reproduce the rescue itself to be safe — but if it offers a
"reboot to bootloader" command, arming the same watcher is a genuinely good idea, and the
recipe above is complete enough to reimplement.

The 446-byte ULP blob was not disassembled: no RISC-V-capable disassembler is available in
this environment (`llvm-objdump` from the Command Line Tools has no `riscv32` target
registered, and no `riscv32-esp-elf` toolchain is installed). The pin evidence is entirely on
the Xtensa side, so this does not weaken the conclusion.

---

## Common configuration (stock does the same thing to all five pins)

Firmware v2 should match this exactly.

**`gpio_config_t` for every input above** — `mode = GPIO_MODE_INPUT`,
`pull_up_en = GPIO_PULLUP_DISABLE`, `pull_down_en = GPIO_PULLDOWN_DISABLE`,
`intr_type = GPIO_INTR_ANYEDGE`. Note that stock relies on **external** pulls for the rear
button and encoder switch; adding an internal pull-up would be harmless for those two but is
not what stock does, and for the touch pad it could fight the IC's output driver.

**Glitch filter — applied to every one of these pins, not just the rear button.** This
corrects the provisional note, which attributed the filter to the rear button alone.
`install_pin_glitch_filter(gpio_num_t)` @ `0x42008fe4` (`wl_io.cpp:70`, `:77`):

```
gpio_pin_glitch_filter_config_t cfg = { .clk_src = 4, .gpio_num = pin };
gpio_new_pin_glitch_filter(&cfg, &filter)
    on error: log "gpio_new_pin_glitch_filter(gpio%d) failed: %d"      (line 70)
gpio_glitch_filter_enable(filter)
    on error: log "gpio_glitch_filter_enable(gpio%d) failed: %d"       (line 77)
```

Called with **2** (rear), **14** (touch), **12**, **11**, **4** (encoder) and **42** (USB
detect). `clk_src = 4` is `SOC_MOD_CLK_APB` on the ESP32-S3, i.e.
`GLITCH_FILTER_CLK_SRC_DEFAULT` — this is an inference from the `soc_module_clk_t` enum
value, not from a string, but `_DEFAULT` is the only sensible choice and the *pin* glitch
filter is fixed-width anyway (it rejects pulses shorter than two source clocks, ~25 ns; there
is no configurable window). Firmware v2 should simply use
`gpio_new_pin_glitch_filter` + `gpio_glitch_filter_enable` on all five input pins.

**ISR service:** `gpio_install_isr_service(0)`, tolerating `ESP_ERR_INVALID_STATE` (`0x103`)
and `ESP_ERR_NOT_ALLOWED` (`0x10D`) so repeated init is safe.

**Event ids** posted by `wl_io` on its `WORKLOUDER_IO_EVENTS` loop (wrapper @ `0x40375c8c`,
per-event posters @ `0x42008e44`…`0x42008ec4`):

| id | Signal | Payload |
|---|---|---|
| 0 | encoder rotate | `{ 0, int8 delta }` |
| 1 | encoder switch | `{ 0, uint8 pressed }` (pressed = level 0) |
| 2 | touch pad | `{ 0, uint8 active }` (active = level 1) |
| 4 | USB connected | — |
| 5 | USB disconnected | — |
| 6 | rear button | `{ uint8 pressed }` (pressed = level 0) |

`wl_io::init()` @ `0x4200936c` → `init_gpio()` @ `0x4200934c` calls, in order:
`init_encoder_gpio`, `init_touchpad_gpio`, `init_status_leds` (`0x42008c68`, LEDC, GPIO 35 …),
`init_usb_detect_gpio`, `init_rear_button_gpio`; then `init_top_board_power_gpio` and
`init_charge_enable_gpio`. Those are **all six** `gpio_config` call sites in the entire app
image — an exhaustive scan for references to `gpio_config` (`0x4207d2d0`) finds exactly eight
`l32r` sites, all inside these six functions. So the input pin inventory below is complete
for anything configured through `gpio_config`, and it has **no conflicts** with the key
matrix (rows 46/17/40/47, cols 13/5/21/1), the LED pins (7/6/35/45/48), the LED rail
(36/37/38), I²C (8/9), charge-enable (44) or USB (19/20).

**Bonus: USB detect = GPIO 42, active LOW.** Not previously recorded in `HARDWARE.md`.
`init_usb_detect_gpio()` @ `0x4200926c` (`wl_io.cpp:146`, `:154`) sets the `pin_bit_mask`
**high** dword to `0x400` → GPIO 32 + 10 = **42**, `mode = 1`, `intr_type = 3`, plus the
glitch filter; assert string `…PIN_USB_DETECT…` @ `0x3c0e0fa4`. The ISR @ `0x40375d50`
computes `connected = (gpio_get_level(42) == 0)` and posts event 4 (connected) or 5
(disconnected). Stock also posts an initial state event at init
(`"Posted initial USB state event: %s"` with `CONNECTED`/`DISCONNECTED`) — worth copying, so
firmware v2 knows the USB state before the first edge.

---

## Why the second image doesn't corroborate

The task expected cross-checking `firmware_v0.6.1_merged.bin` against
`firmware_v0.9.0-sdk.1_merged.bin`. **That cross-check is not available: the two images are
for different products.**

| | v0.6.1 | v0.9.0-sdk.1 |
|---|---|---|
| Product string | `Creator Micro 2` | `Work Louder` / `Nomad [E]` |
| Build name | — | `nomad-e-fw` |
| Modules | `src/drivers/wl_io.cpp`, `wl_rear_btn.cpp`, `wl_usb_wake_ulp.cpp`, `wl_bootloader_rescue.cpp` | + LVGL (`components/lvgl/**`), `wl_display_tft.cpp`, `micropython_embed`, `src/ui/**` |
| Pin symbols | `PIN_ENC_A/B/SWITCH`, `PIN_TOUCH_OUT_L`, `PIN_REAR_BTN`, `PIN_USB_DETECT` | none of these |
| Encoder naming | `encoder_rotate_irq_handler`, `init_encoder_gpio` | `ENCODER_LEFT` / `ENCODER_RIGHT`, `on_encoder_event` |
| Touch strings | `init_touchpad_gpio`, `touchpad_irq_handler`, `TOUCH_OUT_L` | none |

v0.9.0-sdk.1 is a device with a TFT display and an embedded MicroPython SDK. Its pin map is
irrelevant to the Creator Micro 2 and using it as corroboration would be actively
misleading.

**What replaces the cross-version check:** within v0.6.1, each pin is attested by three to
five independent mechanisms — the `pin_bit_mask` bit, the `gpio_num_t` immediate at the
`gpio_isr_handler_add` site whose assert string names the vendor's own symbol, the
`gpio_get_level` immediate in the ISR, the consumer-side event-id/payload check, and (for
GPIO 2) the ext0 / RTC-array / ULP-rescue paths. The two already-CONFIRMED pin groups
(36/37/38 and 44) fall out of the same six functions and match, which validates the decoding
chain end to end.

---

## What remains unproven

Stated plainly, because a confidently wrong pin map that gets flashed is worse than an
honest gap.

1. **Touch pad polarity (medium-high, not certain).** The code path is unambiguous —
   `payload = (level != 0)` and every consumer treats nonzero as pressed — but the vendor's
   own symbol `PIN_TOUCH_OUT_L` suggests active-low, and the polarity of the external touch
   IC's output is a board property that no amount of firmware reading can settle.
   *Test:* read GPIO 14 untouched vs touched (§2).

2. **Encoder rotation direction → clockwise.** The decode is fully recovered (table, state
   packing, reversal reset, sign flip, invert flag), and "A leads B ⇒ decoder emits +1" is
   solid. Which physical direction that is depends on PCB wiring.
   *Test:* log `(B<<1)|A` while turning one detent clockwise (§3).

3. **Encoder detents-per-event (the `u8[12]` threshold) and the `u8[13]` invert flag default.**
   The decoder reads a detent threshold at `decoder+12` and an invert flag at `decoder+13`
   (= `wl_io+56` / `wl_io+57`, since the ISR passes `&this[44]`). The only routine I found
   writing those two bytes (`0x420093fc`) writes **0** to both — but I could not prove that
   routine operates on the `wl_io` object: it also writes `-1` to `wl_io+52`, which under this
   layout is the decoder's accumulator, and that makes no sense as a reset. So
   `threshold = 0` is **not** established. It matters: with a threshold of 0 the decoder emits
   on *every* valid quadrature edge, i.e. 4 events per detent on a typical EC11, which stock
   users would notice — so the runtime value is probably nonzero (4 is the obvious candidate)
   and likely set from settings or the keymap.
   *Test:* count events per physical click and pick the divisor that gives 1.

4. **Whether the sign out of the decoder maps to `ENC_CW` or `ENC_CC` in stock.** The action
   codes exist (`0x1201`/`0x1202`/`0x1203`, name table @ `0x42019b80`) but the delta→code
   conversion site was not located. Since (2) is unproven anyway, this only matters if you
   want firmware v2 to be sign-compatible with stock keymaps.

5. **Whether an external pull-up exists on GPIO 2 / GPIO 4, or whether the pull comes from a
   rail.** Stock configures no internal pull during normal run but does enable an RTC pull-up
   on GPIO 2 in the OFF recipe, which hints that the run-time pull-up lives on a rail that OFF
   cuts. If firmware v2 ever powers down the top board while still wanting the rear button
   readable, it needs the same RTC pull-up.
   *Test:* probe the pin idle level with all internal pulls disabled, and again with the LED
   rail (GPIO 36/37/38) driven low.

6. **The 446-byte ULP-RISCV rescue blob** (DROM `0x3c201088`) was not disassembled — no
   RISC-V disassembler available. This does not affect any pin conclusion; the pin comes from
   the Xtensa-side `rtc_gpio_*` calls, which are all on GPIO 2.

7. **Matrix column order** — still open, unchanged by this work, and not determinable from the
   firmware (it is a physical-vs-logical mapping question). Noted here only so it is not
   mistaken for something this pass resolved.

---

## Tooling notes (proposed `tools/` additions — not applied)

Per instruction, `tools/` was **not modified**. The analysis ran from scratch copies in a
scratchpad. What I would fold in:

1. **`tools/fwlib.py` — a shared image module.** `map_image.py`, `disasm.py` and
   `find_l32r.py` each re-parse the merged image and each hard-code the *same* single firmware
   path, and `disasm.py` hard-codes an `objdump` path
   (a hard-coded `tools/xtensa-esp-elf/...` path) that does not exist on this
   machine — the working toolchain is at
   `~/.platformio/packages/toolchain-xtensa-esp-elf/bin/xtensa-esp-elf-objdump`. A single
   `Img` class holding the segment table with `off_to_va`, `va_to_off`, `read`, `u32`,
   `cstr`, `disasm`, plus `IMAGES = {tag: path}` and `--image` on every script, removes the
   duplication and makes multi-image work possible. `OBJDUMP` should be resolved from
   `$PATH`, then `$XTENSA_OBJDUMP`, then the PlatformIO location, with a clear error.

2. **`find_l32r.py` — support searching by literal *value*, not just literal address.**
   Nearly every finding here came from "which code loads a literal whose *contents* are X"
   (a string VA, a function address, a constant like `0x1201`). That is two steps today:
   scan the image for the 4-byte word, then run `find_l32r` on each hit. A
   `l32r_loading_value(v)` helper (word-aligned scan → `l32r` scan) would collapse it. Also
   worth fixing: the current sign-extension in `find_l32r.py` open-codes a branch that is
   simply `((imm16 - 0x10000) << 2)` for the always-negative L32R offset.

3. **`tools/find_calls.py` — a CALL0/4/8/12 target scanner.** `imm18` is
   `(word >> 6)`, sign-extended, target `= ((PC & ~3) + 4) + (imm18 << 2)`. Finding the
   callers of a function was needed repeatedly (e.g. proving `install_pin_glitch_filter` is
   applied to all five input pins, and that the ULP rescue is armed only from the
   `sys.bootloader` RPC).

4. **`tools/gpio_map.py` — the payoff script.** Locate every `gpio_config` call site by
   `l32r`-value, walk back over the `memset(&cfg,0,24)` / field-store idiom, and print a
   table of `pin_bit_mask` → GPIO list, `mode`, `pull_up_en`, `pull_down_en`, `intr_type`,
   plus the `ESP_ERROR_CHECK` expression string and `file:line`. That one script would have
   produced most of this document mechanically, and re-running it on any future vendor
   release is a one-line regression check on the pin map.

5. **Disassembly alignment helper.** `objdump -b binary` loses sync on the data bytes and
   single-byte padding the compiler leaves between functions, silently producing
   plausible-looking garbage (`excw`, bogus `l32r` targets). Two fixes proved essential and
   should be library functions: scan for `ENTRY` prologues (`36 x1 00`, i.e. `b0 == 0x36 &&
   (b1 & 0x0f) == 0x01`) to find real function starts, and always start a disassembly at a
   known-good boundary — a branch/call target or a prologue — rather than at a round address.

6. **`tools/README.md`** should state the real toolchain location and note that
   `firmware_v0.9.0-sdk.1_merged.bin` is a **different product** (`Nomad [E]`), so it must
   not be used to cross-check Creator Micro 2 facts.
