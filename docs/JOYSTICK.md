# The radial joystick

Reverse engineering of the one Creator Micro 2 input that had never been looked at: the
"radial joystick" at grid position `(0,3)`.

**Method:** static analysis of `firmware_v0.6.1_merged.bin` — the same vendor image, and the
same technique, as [`PIN-VERIFICATION.md`](PIN-VERIFICATION.md). Nothing was flashed and no
hardware was touched. The vendor image is not in this repo and must stay out of it.

**One method difference, stated up front.** Every other input was nailed by an
`ESP_ERROR_CHECK` assert string that literally spells the vendor's own pin symbol
(`PIN_ENC_A`, `PIN_TOUCH_OUT_L`, …). **The joystick has no such string** — its module
contains no `ESP_ERROR_CHECK` at all, which is why no `wl_joystick.cpp` path string exists in
the image. So there is **no vendor symbol name for these pins**. What replaces it is
arguably stronger: the pin numbers are `movi` immediates handed straight to
`analogRead(pin)`, and the whole ADC subsystem in the image has exactly **one** consumer,
which is this module. See §2.

---

## Summary

| Question | Answer | Confidence |
|---|---|---|
| Analog or digital? | **Analog. Two independent axes on two ADC channels.** Not switches, not a ladder, not an I²C expander. | **Very high** |
| X-axis pin | **GPIO 9** = `ADC_UNIT_1` / `ADC_CHANNEL_8` | **Very high** |
| Y-axis pin | **GPIO 10** = `ADC_UNIT_1` / `ADC_CHANNEL_9` | **Very high** |
| Which axis is physically horizontal / which way angle increases | **Unproven** — board fact, not a firmware fact | — |
| Centre click / press | **None.** Stock reads no third pin and no matrix slot for it. | **High** |
| Bit width | **12 bit**, raw 0–4095 (`analogReadResolution(12)`) | **Very high** |
| Attenuation | **`ADC_ATTEN_DB_12`** (Arduino default 3; no setter is linked in) | **High** |
| Per-axis polarity | **Inverted**: `v = clamp(4095 − raw, 0, 4095)` on *both* axes | **Very high** |
| Internal pulls | **None configured** — the pads are put in analog mode by `adc_oneshot`, never by `gpio_config` | **Very high** |
| Centre | Hard-coded **2047.5** counts. No calibration, no NVS, no auto-centre. | **Very high** |
| Deadzone | **0.15** of full radius (≈307 counts of deflection), then rescaled `(r−0.15)/0.85` | **Very high** |
| "Engaged" threshold | rescaled magnitude **> 0.2** ⇒ raw radius **> 0.32** (≈655 counts) | **Very high** |
| Event model | **Continuous angle** (normalised `[0,1)` turns) **+ magnitude**, sliced into keymap-defined **angular sectors**. Default map = 8 × 45°. | **Very high** |
| Poll rate | **100 Hz** — own task `wl_joystick`, 4 KB stack, prio 1, core 1, `vTaskDelay(10)` | **High** |
| Feeds `wl_button::update`? | **No.** Separate path; actions are routed as `kb.radial` (or `v.oai.rad` in vendor mode). | **High** |
| `WORKLOUDER_IO_EVENTS` id | **3** — the id missing from `PIN-VERIFICATION.md`'s table. Payload = one `float` magnitude, throttled to 200 ms while held. | **Very high** |
| In the vendor selftest? | **No** — selftest covers keys, encoder, touch, rear only | **Very high** |
| Calibration in NVS | **None** | **High** |
| ADC1-vs-WiFi conflict | **None** — both channels are on ADC1 | **Very high** |
| **Biggest hazard** | **`firmware/src/main.c` uses GPIO 9 as its I²C SCL fallback (`LM_I2C_SCL_ALT`).** That is the joystick X axis. | **Very high** |

**Stock does read the joystick, and reads it properly.** This is not a stubbed-out feature.

The single cheapest confirming test is in [§9](#9-the-one-test-to-run-first).

---

## 1. Analog or digital

### Conclusion

**Analog, two axes.** Stock samples two ADC channels every 10 ms and converts the pair to a
polar `(angle, magnitude)` with `atan2f` and `sqrtf`. There are no discrete direction
switches, no resistor ladder on a single pin, and no I²C expander involved. The physical part
behaves as a 2-axis potentiometer (thumbstick-style, or a 4-way gate wired across an X/Y
resistive network — the firmware cannot distinguish those, and does not care).

### Confidence

**Very high.** Three independent lines of evidence, and the decisive one is exclusivity: the
ADC driver in this image has exactly one consumer.

### Evidence

**a. The whole ADC stack is linked in and reachable.** The image contains the ESP-IDF
`adc_oneshot` driver (`adc_oneshot_new_unit` @ `0x4207bb68`, `adc_oneshot_config_channel` @
`0x4207bcf8`, `adc_oneshot_read` @ `0x4207be04`, `s_adc_io_init`, `adc_io_to_channel`) plus
the arduino-esp32 wrappers `__analogInit` @ `0x4206e3f4` and `__analogRead` @ `0x4206e4ec`
(function-name strings at DROM `0x3c205f34`–`0x3c205fb4`, `0x3c2056a4`, and the
`esp32-hal-adc.c` path at `0x3c0ef510`).

**b. Exactly one call chain reaches it.**

```
adc_oneshot_read (0x4207be04)      <- 1 caller:  __analogRead @ 0x4206e58d
__analogRead     (0x4206e4ec)      <- 2 callers: 0x4200943a and 0x42009443
                                                 (both inside ONE function, 0x42009434)
```

So the joystick sampler is the **only** thing in the entire stock image that takes an ADC
sample. Nothing else — not the battery (that is I²C to the MAX77972), not a thermistor, not
a VBUS divider.

**c. The two samples are combined as a vector, which only makes sense for a 2-axis stick.**
`0x42009434` reads two channels, subtracts a mid-scale centre from each, and calls
`atan2f(ny, nx)` and `sqrtf(nx² + ny²)`. Four discrete switches or a resistor ladder would
need neither. Full decode in §3.

**d. The keymap format agrees.** The built-in default `keymap.json` (DROM `0x3c0e1de2`
onwards) carries a `"joystick"` object whose sectors are **fractional angle ranges**:

```json
"joystick": {
  "type": "RADIAL",
  "sectors": [
    {"k": "KI_X",  "a1": 0.1875, "a2": 0.3125},
    {"k": "KC_P1", "a1": 0.3125, "a2": 0.4375},
    …
    {"k": "KC_P6", "a1": 0.9375, "a2": 0.0625},
    {"k": "KC_P7", "a1": 0.0625, "a2": 0.1875}
  ]
}
```

Arbitrary float boundaries with a wrap-around entry is a continuous-angle format. A
four-switch part would be indexed, not bounded.

### How to confirm on hardware

Read GPIO 9 and GPIO 10 as ADC1 channels 8 and 9 (12-bit, `ADC_ATTEN_DB_12`) and print both
raw values while moving the stick. Expect two smoothly varying values, each with a rest point
near mid-scale — not two-level digital behaviour. See §9.

---

## 2. The pins — GPIO 9 (X) and GPIO 10 (Y)

### Conclusion

- **X = GPIO 9** → `ADC_UNIT_1`, `ADC_CHANNEL_8`
- **Y = GPIO 10** → `ADC_UNIT_1`, `ADC_CHANNEL_9`

"X" and "Y" here mean *the firmware's* X and Y: GPIO 9's value is the `x` argument to `atan2f`
and GPIO 10's the `y`. Which of those is physically left-right on the faceplate is **not
determinable from the image** (see below).

### Confidence

**Very high** for the pin numbers. The immediates are literal, they are validated at runtime
by the ADC driver, and no other ADC pin is ever requested.

**Unproven:** which axis is horizontal, and which rotation direction increases the angle.

### Evidence

**a. The immediates, at the head of `0x42009434`:**

```
42009434:  entry  a1, 96
42009437:  movi   a10, 9            ; pin = 9
4200943a:  call8  0x4206e4ec        ; __analogRead(9)
4200943d:  s32i   a10, a1, 28       ; raw_x
42009440:  movi   a10, 10           ; pin = 10
42009443:  call8  0x4206e4ec        ; __analogRead(10)
42009446:  s32i.n a10, a1, 32       ; raw_y
```

**b. The argument is a GPIO number, not a channel number.** `__analogRead` @ `0x4206e4ec`
truncates its argument to 8 bits and immediately calls `adc_io_to_channel(pin, &unit,
&channel)` (`0x4207bb48` at `0x4206e4fd`), logging
`"Pin %u is not ADC pin!"` (DROM `0x3c0ef6e8`) on failure. So the value **9** is `GPIO_NUM_9`,
resolved by the driver to `ADC_UNIT_1` / `ADC_CHANNEL_8`; **10** → `ADC_UNIT_1` /
`ADC_CHANNEL_9`. (ESP32-S3: ADC1 CH0–CH9 = GPIO 1–10; ADC2 CH0–CH9 = GPIO 11–20.)

**c. Nothing else in the image touches GPIO 9 or 10.** There are exactly **eight** `l32r`
references to `gpio_config` (`0x4207d2d0`) in the whole app, at `0x42008f57`, `0x42008fa0`,
`0x42009075`, `0x420090da`, `0x42009140`, `0x42009173`, `0x420091a5`, `0x42009293` — the six
`wl_io` initialisers already enumerated in `PIN-VERIFICATION.md`. Their `pin_bit_mask`
values are `0x4`, `0x4000`, `0x1000`, `0x800`, `0x10`, hi-`0x400`, hi-`0x70`, hi-`0x1000`.
**None has bit 9 or bit 10 set**, and no literal `0x200` / `0x400` / `0x600` (bit 9 / bit 10 /
both, as a low dword) is `l32r`-loaded anywhere. So stock never configures 9 or 10 as digital
GPIO; the pads are put into analog mode by the ADC driver's `s_adc_io_init` and left there.

**d. Stock's I²C is on 8/18, not 8/9.** `wl_io::init` calls `Wire.begin(8, 18, 100000)` —
already established and since **confirmed on hardware** (`HARDWARE.md`). That matters here
because GPIO 9 is the arduino-esp32 *default* SCL, and it is exactly what one would guess for
SCL. Stock does not use it, and now we know why: it is the joystick.

**e. The task that owns the sampler is named `wl_joystick`.** `0x4200960c` (the module's
`start()`, vtable `0x3c100038` slot 1) does:

```
this->u8[28] = 0                       ; enabled = false
0x420093fc(this)                       ; reset state
analogReadResolution(12)               ; 0x4206e4d4, immediate 12
this->u8[28] = 1                       ; enabled = true
xTaskCreatePinnedToCore(trampoline 0x420c8a24, "wl_joystick", 4096,
                        &this->task_base /*this+16*/, prio 1, NULL, core 1)
```

The name string is at DROM `0x3c0e10f8` (`l32r` at `0x4200962b`). Its `run()` (vtable slot 2,
`0x4200a4b4`) is the loop that calls the sampler. That closes the loop: the two `analogRead`
immediates belong to the module the vendor itself calls the joystick.

### Why the axis orientation is unproven

`atan2f(ny, nx)` gives `angle = 0` when `nx > 0, ny = 0`. Both axes are inverted before
normalisation (`v = 4095 − raw`), so `nx > 0` means **raw GPIO 9 below mid-scale**. Whether
that is "right", "left", "up" or "down" on the faceplate depends on which wiper terminal is
wired to which pin and which way the pot is mounted — a PCB fact. Likewise, whether angle
increases clockwise or counter-clockwise as seen from above follows from the same wiring.
This is exactly the same class of gap as the encoder's "which way is CW" (§3 of
`PIN-VERIFICATION.md`), and it needs one measurement, not more analysis.

**Do not read direction semantics out of the default keymap.** Its sector keycodes are
placeholder filler in the same spirit as the `KC_A … KC_M` used for the 13 keys — and one of
them, `KI_X`, **is not even a valid action name** (the image's `KI_*` name table has
`KI_FP`, `KI_LS1…`, `KI_BLUP`, `KI_CS_*` … and no `KI_X`). The `KC_P1…KC_P7` ordering does
not form a coherent numpad compass either. It proves the *format*, not the geometry.

### How to confirm on hardware

Print `(raw9, raw10)` at ~20 Hz and push the stick to each of the four faceplate marks in
turn, recording the pair each time. That gives you, in one pass: which pin is horizontal,
which polarity, the true rest point, and the actual full-scale swing.

---

## 3. Signal conditioning, polarity and thresholds

### Conclusion

Fully recovered, byte for byte. The sampler `0x42009434` is:

```c
struct sample { float angle; float mag; bool deflected; bool engaged; };

sample wl_joystick::read(void)
{
    int x = analogRead(9);                       // GPIO 9,  ADC1_CH8
    int y = analogRead(10);                      // GPIO 10, ADC1_CH9

    x = 4095 - x;                                // both axes inverted
    y = 4095 - y;
    x = clamp(x, 0, 4095);
    y = clamp(y, 0, 4095);

    float nx = (x - 2047.5f) * (1.0f / 2047.5f); // -> [-1, +1]
    float ny = (y - 2047.5f) * (1.0f / 2047.5f);

    float a = atan2f(ny, nx);
    if (a < 0.0f) a += 6.2831855f;               // -> [0, 2pi)
    a = a / 6.2831855f;                          // -> [0, 1)  normalised turns

    float m = sqrtf(nx*nx + ny*ny);
    if (m > 1.0f) m = 1.0f;                      // corner radius sqrt(2) is clipped
    if (m < 0.15f) m = 0.0f;                     // DEADZONE
    else           m = (m - 0.15f) / 0.85f;      // rescale so 0.15 -> 0, 1.0 -> 1

    return { a, m, m > 0.0f, m > 0.2f };
}
```

`analogReadResolution(12)` is set once in `start()`, and `__analogReadResolution ==
__analogWidth == 12`, so the resolution-mapping step in `__analogRead` (`0x4206e338`) is a
no-op and `analogRead` returns **raw 12-bit counts**.

### Confidence

**Very high** for every constant — each is a literal in the pool, and the arithmetic was
disassembled instruction by instruction. **High** for the attenuation, which is a linked-in
default rather than an explicit call.

### Evidence

**Literal pool** (all verified by reading the word at the `l32r` target):

| Literal slot | Value | Meaning | Used at |
|---|---|---|---|
| `0x420008b8` | `0x00000fff` = 4095 | full scale / inversion base | `42009448`, `42009469`, `42009489` |
| `0x420008a0` | → `0x3c10005c` = `4095` | clamp-high, taken by address | `4200946f`, `4200948f` |
| `0x420008a4` | `0x44fff000` = **2047.5f** | centre | `42009499` |
| `0x420008a8` | `0x3a000801` = **1/2047.5f** | axis scale | `420094a2` |
| `0x420008ac` | `0x40c90fdb` = **2π** | angle wrap + divisor | `420094d4`, `420094dd` |
| `0x420008b0` | `0x3e19999a` = **0.15f** | deadzone | `4200952a`, `42009543` |
| `0x420008b4` | `0x3f59999a` = **0.85f** | `1 − deadzone`, rescale divisor | `42009540` |
| `0x4200023c` | `0x3e4ccccd` = **0.2f** | "engaged" threshold | `42009574` |
| `0x4200007c` | `0x3f800000` = 1.0f | magnitude clamp | `4200950e` |
| `0x42000020` | `0x00000000` = 0.0f | sign test / zeroing | several |
| `0x420008bc` | `0x420b7384` | **`atan2f`** (2-arg float wrapper → `0x420b7c04`) | `420094bf` |
| `0x420008c0` | `0x420b74a8` | **`sqrtf`** (1-arg, NaN/negative domain check) | `420094fe` |
| `0x42000090` | `0x40002274` | **`__divsf3`** — ESP32-S3 ROM, confirmed against `esp32s3_rev0_rom.elf` (`__call___divsf3` @ `0x40002274`) | `420094e3`, `4200954f` |

**Polarity.** `0x42009448`–`0x42009455` is `sub a9, a8, a9` / `sub a10, a8, a10` with
`a8 = 4095`, i.e. `v = 4095 − raw`, applied to **both** channels before anything else. The
clamp that follows is the compiled form of `std::clamp(v, 0, 4095)`: a `bltz` selecting
between `&local_zero` and `&v`, then a `bge` against 4095 selecting between that and
`&const_4095`.

**Deadzone.** `0x4200952a`: `olt.s b0, f0, f1` with `f1 = 0.15` → `bf b0, 0x42009540`. The
taken-when-false branch at `0x42009540` loads `a11 = 0.85f` (from `0x420008b4`; the `l32r` at
`0x42009540` is one objdump mis-syncs on, so it is decoded here by hand: bytes
`b1 dd dc` ⇒ `l32r a11, 0x420008b4`), subtracts 0.15 and calls `__divsf3`. The fall-through
sets magnitude to 0.0f.

So a raw radius below 0.15 reads as dead-centre, and the "engaged" flag needs the *rescaled*
value above 0.2, i.e. a raw radius above `0.15 + 0.2 × 0.85 = 0.32`. In counts: **≈307 counts
of deflection to leave the deadzone, ≈655 to engage.**

**The two booleans.** `0x4200955f`–`0x42009580`: `deflected = (0.0f < m)` via
`movt`, `engaged = (0.2f < m)` via `movf`. They are returned in `a4` as bytes 0 and 1 of one
word; the struct comes back in `a2..a5` as `{a2 = angle, a3 = magnitude, a4 = flags, a5 = 0}`,
which is how the caller at `0x4200a3bd`–`0x4200a3c6` unpacks it.

**Attenuation and unit config.** `__analogInit` builds
`adc_oneshot_chan_cfg_t { .atten = __analogAttenuation, .bitwidth = __analogWidth }` from two
byte globals. Their initialised values, read straight out of the `.data` segment at
`0x3fca2fc0`, are `0c 0c 03` →

```
__analogReadResolution = 12
__analogWidth          = 12      (ADC_BITWIDTH_12)
__analogAttenuation    =  3      (ADC_ATTEN_DB_12, a.k.a. the old "11 dB")
```

`__analogAttenuation` has exactly **one** reference in the image (`0x4206e480`, a read inside
`__analogInit`), so no `analogSetAttenuation` / `analogSetPinAttenuation` setter is linked in
and the value can never change at runtime. `adc_oneshot_new_unit` is called with a
memset-zeroed `init_cfg` other than `unit_id`, i.e. default clock source and
`ADC_ULP_MODE_DISABLE`.

**No calibration.** `adc_cali_create_scheme_curve_fitting` is present only because
`analogReadMilliVolts` is in the same translation unit; nothing calls it (the only ADC read
path is raw `analogRead`). No NVS namespace or key anywhere in the image mentions the
joystick, and the module's reset routine `0x420093fc` writes only zeros, `0.0f` and `−1` — no
stored centre, no stored span. **The 2047.5 centre is a hard-coded assumption.**

### How to confirm on hardware

1. **Rest point.** Read both channels untouched, 100 samples, and take the mean. If either
   axis rests far from 2047 (say outside 1900–2200), stock's fixed centre is being carried by
   the 0.15 deadzone and your implementation should measure the rest point at boot instead.
2. **Full scale.** Push to each extreme and record min/max per axis. If the swing does not
   reach anywhere near 0/4095, the physical radius never reaches 1.0 and the 0.32 engage
   threshold may be unreachable — which would be a reason to make the thresholds config-driven
   rather than copying stock's numbers.
3. **Noise.** Look at the peak-to-peak spread at rest. Stock does no averaging and no
   filtering at all; if the spread is more than a few tens of counts you will want a small
   median or IIR filter that stock does not have.

---

## 4. How stock turns it into events

### Conclusion

Two separate outputs, from one 100 Hz poll:

1. **A coarse `WORKLOUDER_IO_EVENTS` event, id `3`** — payload is a single `float`
   magnitude, posted on the rising edge of "deflected" and then at most every **200 ms**
   while held. It exists for the power/activity layer, not for actions.
2. **The action path** — the continuous normalised angle is looked up in the active layer's
   **sector table** and the matching keycode is pressed/released, with hysteresis. Sector
   count and boundaries come from `keymap.json`; the default map uses **8 sectors of 45°**.

There is **no press/click**, no diagonal-vs-cardinal distinction baked into firmware (the
sector table decides that), and it does **not** go through `wl_button::update` /
`wl_touch_btn` the way the touch pad does.

### Confidence

**Very high** for the mechanism and for event id 3 / its payload type. **High** for the
"no click" conclusion. **Medium-high** for the finer details of the emit function
(`0x4200a038`), which was read structurally rather than exhaustively.

### Evidence

**a. The task loop** — `0x4200a4b4` (vtable `0x3c100038` slot 2):

```
loop {
    tick(this)          ; 0x4200a394
    vTaskDelay(10)      ; l32r 0x4200064c -> 0x4038a668
}
```

`configTICK_RATE_HZ` is 1000 (arduino-esp32 requires it, and it is corroborated inside this
image: the OFF recipe's release-wait loop is 40 iterations of `vTaskDelay(50)` — the immediate
`50` at `0x4201c920` — for the ~2 s window `PIN-VERIFICATION.md` §4b describes). So
**`vTaskDelay(10)` = 10 ms = 100 Hz**.

**b. The tick** — `0x4200a394`:

```
if (!this->u8[28]) return;                        ; not enabled
if (!config_ready())      { release_all(this); return; }   ; 0x4200a370
now  = millis();                                  ; 0x4207096c = esp_timer_get_time()/1000
s    = read();                                    ; 0x42009434  (§3)
maybe_post_io_event(this, s.mag, s.deflected, now);        ; 0x42009590
km   = keymapper_config::instance();              ; 0x42009774  (singleton @ 0x3fcabe50)
lay  = active_layer(km);                          ; 0x4203e398
type = lay->i32[72];
if (type == 1)  vendor_emit(this, s.angle, s.mag, s.deflected, now);   ; 0x42009e98
else            { sector = find_sector(this, s.angle, s.engaged);      ; 0x420097ac
                  kb_emit(this, s.angle, s.mag, sector, …, s.engaged, now); }  ; 0x4200a038
```

Switching between the two modes releases whatever the other mode was holding
(`0x4200a230` for the keyboard path, `0x42009de8` for the vendor path), keyed off the
`this->u8[29]` / `u8[31]` / `u8[32]` edge-state bytes that `0x420093fc` resets to zero.

**c. The io event poster** — `0x42009590`:

```
if (!active) { this->u8[30] = active; return; }               ; nothing to post
if (this->u8[30] && (now - this->u32[36]) <= 199) { … return; } ; 200 ms rate limit
payload[0..3] = magnitude                                    ; 4 bytes, one float
esp_event_post(WORKLOUDER_IO_EVENTS, /*id=*/3, &payload, 4, /*ticks=*/0)
this->u32[36] = now
this->u8[30]  = active
```

`movi.n a11, 3` at `0x420095b5` is the event id; the base comes from
`l32r a8, 0x42000424` → `0x3c0fff8c` → `"WORKLOUDER_IO_EVENTS"` (DROM `0x3c0e0920`) — the same
literal every other `wl_io` poster uses. **This fills in the one gap in
`PIN-VERIFICATION.md`'s event table**, which lists ids 0, 1, 2, 4, 5, 6 and no 3.

| id | Signal | Payload |
|---|---|---|
| 0 | encoder rotate | `{ 0, int8 delta }` |
| 1 | encoder switch | `{ 0, uint8 pressed }` |
| 2 | touch pad | `{ 0, uint8 active }` |
| **3** | **joystick** | **`{ float magnitude }`** |
| 4 | USB connected | — |
| 5 | USB disconnected | — |
| 6 | rear button | `{ uint8 pressed }` |

**d. The consumer independently confirms the payload is a float.** The activity predicate at
`0x4200aff4` (one of a three-way chain tried by `0x4200b068`) is:

```
if (base != WORKLOUDER_IO_EVENTS) return false;
switch (id) {
  case 0: return data[1] != 0;            case 1: return data[1];
  case 2: return data[0];                 case 6: return data[0];
  case 3: return *(float*)data > 0.0f;    ;  <-- 0x4200b040: lsi f0,[a4]; olt.s vs 0.0f
  default: return false;
}
```

Two independently compiled sites agreeing on "id 3 carries a float at offset 0" is what makes
this **very high** confidence. Note the consequence: **any deflection past the deadzone counts
as user activity**, so the joystick will keep the pad awake and reset the idle timer.

**e. The sector lookup** — `0x420097ac`:

```
int find_sector(this, float angle, bool engaged)
{
    if (!engaged) return -1;
    auto& v = active_layer(keymapper_config::instance())->sectors;  // vector at layer+76
    if (v.empty()) return -1;
    for (i = 0; i < v.size(); ++i) {
        float a1 = v[i].a1;                  // element +4
        float a2 = v[i].a2;                  // element +8
        if (a1 <= a2) { if (a1 <= angle && angle < a2) return i; }   // normal
        else          { …wrap-around case…   return i; }             // a1 > a2
    }
    return -1;
}
```

Element layout is `{ keycode @+0, float a1 @+4, float a2 @+8 }`, matching the JSON's
`{"k", "a1", "a2"}`. The `a1 <= a2` test is the wrap-around branch, which is exactly what the
default map's last entry (`0.9375 → 0.0625`) needs. **Sector count is data, not code** — the
loop is over `v.size()`.

**f. Emit with hysteresis** — `0x4200a038`. It compares, against the object's stored
previous values, all of: `engaged` (`this->u8[29]`), the sector index
(`this->i32[52]`, reset to **−1** = "none"), a 3-state phase byte (`this->u8[58]`;
`0` = idle, `1` = engaged with no sector, `2` = sector valid), and whether the angle moved
more than **0.0025 turns (0.9°)** (`l32r 0x4200097c` = `0x3b23d70a`) or the magnitude by more
than **0.01** (`0x42000980` = `0x3c23d70a`). Only then does it emit. That is a
send-on-significant-change model, not a fixed-rate stream.

**g. The action route.** The emit path builds a `{ length, const char* }` string view and
hands it to `0x420362b4`:

```
4200a1fd:  movi.n a11, 9              ; strlen("kb.radial") == 9
4200a1ff:  s32i.n a11, a1, 0
4200a201:  l32r   a12, 0x420009ac     ; -> DROM 0x3c0e1110 = "kb.radial"
4200a204:  s32i.n a12, a1, 4
4200a209:  call8  0x420362b4
```

`"kb.radial"` sits in the same namespace family as `"kb.cs.show"`, `"kb.sa.exec"`,
`"v.oai.hid"` — i.e. it is a **comms route name**, and the joystick's actions are submitted
through the same executor as every other action source, tagged with their origin. The vendor
mode uses `"v.oai.rad"` (DROM `0x3c0e1104`) the same way. Neither is a GPIO or a pin symbol.

**h. Mode enum.** The `keymap.json` parser at `0x42044c9c` compares the `"type"` string
against `"RADIAL"` (`0x3c0e8510`) then `"VENDOR"` (`0x3c0e8518`) and stores the result to
`layer+72`:

| `"type"` | stored value | tick behaviour |
|---|---|---|
| `"RADIAL"` | 0 | sector table → `kb.radial` |
| `"VENDOR"` | 1 | `v.oai.rad` (the OpenAI-device bridge) |
| `"JOYSTICK"` | 0 (unrecognised → default) | same as RADIAL; the default map pairs it with `"sectors": []`, i.e. a no-op |

So **`"JOYSTICK"` is not an implemented mode** despite appearing in the JSON. There is no
gamepad-axis / HID-analog output path in this firmware.

**i. No click.** The sampler produces only `angle`, `magnitude` and two derived booleans;
nothing reads a third pin. The eight `gpio_config` sites are all accounted for (§2c) and none
is a joystick button. The 4×4 grid is 13 keys + encoder + joystick + touch = 16, so the
joystick occupies a grid slot but has **no matrix switch under it**. If the physical part has
a centre push, stock does not read it.

**j. Not in the selftest.** The vendor selftest string is
`"Selftest: Waiting for %d keys, encoder rotation/press, touch button, rear button"`
(DROM `0x3c0e18b8`) and its handlers cover io event ids 0, 1, 2 and 6 only. The joystick is
absent — so a vendor selftest pass proves nothing about it, and conversely a dead joystick
would never have failed the vendor's own factory test.

### How to confirm on hardware

There is **no stock RPC that exposes raw joystick or ADC values** — the method table is
`sys.version`, `sys.bootloader`, `sys.selftest`, `sys.charger_diagnostic`,
`power.max77972.summary`, `fs.*`, `kb.*`, `v.oai.*`, `lights.preview`, `device.status`,
`sentry.*`. So the event model cannot be observed from stock without custom firmware; it has
to be validated by reimplementing it and checking the numbers behave (§9).

---

## 5. Things that would bite an implementation

### 5.1 GPIO 9 is already claimed by our own I²C fallback — fix this first

**Conclusion: a live, in-tree conflict.** `firmware/src/main.c` defines

```c
#define LM_I2C_SCL_GPIO   18      // vendor-attested; docs/HARDWARE.md says 9
#define LM_I2C_SCL_ALT    9       // the doc's value, tried second if 18 won't ACK
```

and `batt_try_bus()` brings up an I²C master with `scl_io_num = 9` if the gauge does not ACK
on 18, calling `gpio_reset_pin(9)` on teardown.

**Confidence: very high** — this is our own source, and §2 establishes GPIO 9 is the joystick
X axis.

Consequences, in order of nastiness:

1. If the fallback ever *wins* (the gauge ACKs on 9), the I²C driver keeps GPIO 9 as a
   push-pull 100 kHz clock output for the rest of the boot. Joystick X is then dead, and the
   ADC would be sampling a driven digital line.
2. Even when the fallback *loses*, it has driven GPIO 9 and then called `gpio_reset_pin(9)`,
   which leaves the pad as a digital input **with an internal pull-up**. The joystick's
   `adc_oneshot` init must run *after* that and will re-take the pad — but if the ordering is
   ever the other way round, X reads pinned high.
3. The whole reason the fallback exists is that `HARDWARE.md` once recorded SCL as 9. This
   work explains that record: **9 was a guess** (it is the arduino-esp32 default SCL pair with
   8), and it was wrong because 9 was already spoken for. SCL 18 is now doubly attested.

**Recommended action:** delete the `LM_I2C_SCL_ALT` fallback, or gate it behind a build flag
that is off whenever the joystick is enabled, and correct the "docs/HARDWARE.md says 9" note.
Whatever you do, do not let an I²C bus and an ADC channel race for GPIO 9 at boot.

### 5.2 Both channels are on ADC1 — no WiFi conflict, and keep it that way

**Conclusion:** GPIO 9 = ADC1_CH8, GPIO 10 = ADC1_CH9. ADC2 on the ESP32-S3 is the one that
is arbitrated with the WiFi/BLE controller and can return `ESP_ERR_TIMEOUT`; **ADC1 is not
affected.** Confidence: **very high** (channel mapping is fixed silicon).

Corollary: nothing else in LibreMicro should claim ADC2, and there is no need for the
`adc_lock_*` dance.

### 5.3 Both pads are RTC-capable, so an inherited hold is possible

**Conclusion:** GPIO 9 and 10 are inside the ESP32-S3 RTC domain (RTC GPIO covers GPIO 0–21).
Stock's power-off path calls `gpio_deep_sleep_hold_en()`, which latches **all** digital pads
into the RTC domain, and `HARDWARE.md` already documents that those latches survive a
flash/reset. That is the same failure class as the LED rail and as GPIO 2's
`rtc_gpio_hold_en`.

**Confidence: medium-high on the hazard, unproven on whether it actually breaks an ADC read.**
A pad hold latches the output driver and pull configuration; whether it also blocks the
analog input path once `adc_oneshot` re-initialises the pad is not something I can settle from
the image, and I did not find any stock code that holds 9 or 10 specifically (the OFF
recipe's explicit RTC pin array at DROM `0x3c201058` is `{2, 20}`, plus 19 handled separately).

**Cheap and free mitigation:** in the boot hold-release block that already exists, add
`gpio_hold_dis(9/10)`, `rtc_gpio_hold_dis(9/10)` and `rtc_gpio_deinit(9/10)` before ADC init.
It costs nothing and removes the whole question. `main.c` already reasons this way for the
I²C pads (see its comment at line ~416).

### 5.4 Do not port the Arduino call — use `adc_oneshot` directly

LibreMicro is a plain ESP-IDF project; `analogRead` does not exist there. The equivalent of
what stock does is:

```c
adc_oneshot_unit_init_cfg_t init = { .unit_id = ADC_UNIT_1 };     // default clk, ULP disabled
adc_oneshot_new_unit(&init, &h);
adc_oneshot_chan_cfg_t ch = { .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_12 };
adc_oneshot_config_channel(h, ADC_CHANNEL_8, &ch);   // GPIO 9  -> X
adc_oneshot_config_channel(h, ADC_CHANNEL_9, &ch);   // GPIO 10 -> Y
adc_oneshot_read(h, ADC_CHANNEL_8, &raw_x);
adc_oneshot_read(h, ADC_CHANNEL_9, &raw_y);
```

No calibration handle is needed; stock works entirely in raw counts.

### 5.5 Stock's fixed 2047.5 centre is a liability worth improving on

There is no calibration anywhere — not in NVS, not in the littlefs `keymap.json`, not in the
module's reset path. A unit whose stick rests off-centre relies entirely on the 0.15 deadzone
to hide it. Measuring the rest point over the first ~100 ms after boot (while nobody is
touching it) and storing it in RAM is a two-line improvement that makes the deadzone smaller
and the response better. Do it behind a config toggle so the stock-compatible behaviour stays
reproducible.

### 5.6 No filtering at all

Stock samples once per axis per 10 ms tick, with no averaging, no oversampling and no IIR.
The only smoothing is downstream: the 0.9° / 0.01 change hysteresis in the emitter and the
200 ms event throttle. If the raw signal turns out noisy on real hardware, that will show up
as sector flapping near a boundary, and the fix belongs in the sampler (a 4- or 8-sample mean
costs nothing at 100 Hz), not in the emitter.

---

## 6. What remains unproven

Stated plainly, because a confidently wrong pin map that gets flashed is worse than an honest
gap.

1. **Axis orientation and angle direction.** Which of GPIO 9 / 10 is physically left-right,
   and whether the normalised angle increases clockwise or counter-clockwise seen from above.
   Not in the firmware; it is PCB wiring. *Test:* §2 "How to confirm on hardware".
2. **The true rest point and full-scale swing per axis.** Stock assumes 2047.5 and a
   reachable radius of 1.0; neither is verifiable statically. This determines whether the
   0.15 deadzone and the 0.32 engage threshold are usable numbers on real hardware. *Test:*
   §3 items 1 and 2.
3. **Whether the physical part has a centre click.** Stock reads none, and there is no free
   GPIO or matrix slot for one, so almost certainly there is nothing to read — but "the
   vendor did not wire it" and "the part has no switch" are different statements and only the
   first is proven. *Test:* press straight down and watch all 16 matrix slots plus both ADC
   channels; if a real click exists and is unwired, nothing will move.
4. **Whether an inherited RTC pad hold actually breaks an ADC read on 9/10.** §5.3. *Test:*
   after a stock power-off, flash and read both channels *before* calling any `hold_dis`, then
   again after. If the readings differ, the hold matters.
5. **How many sectors the physical gate actually supports.** The faceplate is drawn with
   **four** radiating marks, but the firmware's format and its own default map use **eight**
   45° sectors. Whether the part is a 4-way gate (in which case 8 sectors means the diagonals
   are only reachable by cheating the gate) or a free 360° stick is a mechanical fact.
   *Test:* sweep the stick around its full travel and plot `(nx, ny)`; a 4-way gate traces a
   cross, a free stick traces a disc or a rounded square.
6. **The finer structure of `0x4200a038` (the emit function)** — the exact press/release
   sequencing when the sector changes while still engaged. I read its state comparison and
   hysteresis constants but did not fully decode its branch tree. It does not affect the pin
   map or the sampler, and a reimplementation is free to choose its own edge policy.
7. **Whether `"JOYSTICK"` mode was meant to be HID analog axes.** The string exists, the
   parser folds it to the same value as `RADIAL`, and no analog-HID path exists in this build.
   Reading intent from a dead enum value is speculation.

---

## 7. Proposed serial protocol

Consistent with [`PROTOCOL.md`](PROTOCOL.md)'s `key <i> down|up` / `enc cw|ccw` /
`touch down|up` style: one line per event, unambiguous first token, no state the host has to
infer.

The joystick is the first input whose natural output is **continuous**, so the grammar needs
both a discrete form (bindable) and a continuous form (usable for lighting and for
diagnostics), and the continuous one must be opt-in or it will eat the 115200 link.

| Event | Meaning |
|---|---|
| `joy <dir> down` / `joy <dir> up` | the discrete, bindable form. `<dir>` is one of `n ne e se s sw w nw` (or a subset — see below). Emitted on sector entry/exit, so `hold` and `double` bindings work exactly as they do for `touch` and `rear`. |
| `joy center` | left the engaged region entirely (magnitude fell back under the deadzone) without entering another sector. Lets the host close out a `hold` without guessing. |
| `joy raw <angle> <mag>` | **opt-in**, off by default. `<angle>` in whole degrees `0`–`359`, `<mag>` in percent `0`–`100`. Rate-limited on-device and only sent on significant change. |

Direction naming, not sector indices, because the host and the web UI both need to draw this
and `joy ne down` survives a config that changes the sector count. Firmware owns the
angle→name mapping, exactly as `PROTOCOL.md` insists firmware owns matrix→logical key index.
Default to **4 directions** (`n e s w`), matching the four faceplate marks, with 8 available
once §6 item 5 is settled.

Two new commands, both cheap:

| Command | Meaning |
|---|---|
| `joy stream <0\|1>` | enable/disable `joy raw` lines |
| `joy cal` | print raw centre, per-axis min/max seen so far, and the live raw pair — the diagnostic counterpart to `dump` and `batt`, and the thing that answers §6 items 1, 2 and 5 in one command |

And extend `ver`: `events=key,joy,batt`, so the host detects joystick support rather than
assuming it — the pattern `PROTOCOL.md` already establishes for `frames=` and `batt=`.

**Rate budget.** `joy raw` at stock's own change thresholds is worst-case ~100 lines/s of
about 20 bytes = 2 KB/s, on top of the ~7.0 KB/s a full-pad 30 fps animation already measured.
That does not fit, which is exactly why streaming is off by default and why the discrete form
is the one bindings use.

Following `PROTOCOL.md`'s existing rule, ship this behind `LM_ENABLE_UNVERIFIED_INPUTS` until
§6 items 1 and 2 are measured — the pin map is solid but the *orientation* is not, and a
joystick whose `n` is actually `e` is worse than one that is silent.

## 8. Proposed config surface

The schema (`host/config/schema.json`) has no joystick triggers at all today. The smallest
addition that fits the existing shape:

**A new `$defs/joystick`,** added to `$defs/profile` next to `encoder`, `touch` and `rear`,
and to `$defs/mode` next to `encoder`:

```json
"joystick": {
  "description": "Radial joystick bindings. Directions fire on sector entry/exit.",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "n":  { "$ref": "#/$defs/triggers" },
    "ne": { "$ref": "#/$defs/triggers" },
    "e":  { "$ref": "#/$defs/triggers" },
    "se": { "$ref": "#/$defs/triggers" },
    "s":  { "$ref": "#/$defs/triggers" },
    "sw": { "$ref": "#/$defs/triggers" },
    "w":  { "$ref": "#/$defs/triggers" },
    "nw": { "$ref": "#/$defs/triggers" }
  }
}
```

Reusing `$defs/triggers` verbatim is the point: `press` / `release` / `hold` / `double` then
work on a direction with no new binding semantics, no new host code paths, and no new
documentation. It also means `device.hold_ms` and `device.double_ms` already apply.

**Plus a small tuning block under `device`,** because §5.5 and §6.2 mean the thresholds must
be adjustable per unit rather than hard-coded from the vendor:

```json
"joystick_deadzone":  { "type": "number", "minimum": 0, "maximum": 0.9, "default": 0.15 },
"joystick_engage":    { "type": "number", "minimum": 0, "maximum": 1.0, "default": 0.20 },
"joystick_dirs":      { "type": "integer", "enum": [4, 8], "default": 4 },
"joystick_rotate":    { "type": "integer", "minimum": -180, "maximum": 180, "default": 0 },
"joystick_invert_x":  { "type": "boolean", "default": false },
"joystick_invert_y":  { "type": "boolean", "default": false }
```

`joystick_rotate` and the two inverts are the config-level escape hatch for §6 item 1 — the
same role `LM_TOUCH_ACTIVE_HIGH` and "swap `LM_PIN_ENC_A`/`LM_PIN_ENC_B`" play for the other
two unproven facts in `HARDWARE.md`, but resolvable without a rebuild.

Deliberately **not** proposed: an `angle` or `magnitude` trigger kind. A continuous value is
not a trigger, and binding one would need a whole new action model. If someone wants the raw
angle for a lighting effect, that belongs in `$defs/effect` (as a new effect input), driven by
`joy raw` — a separate piece of work.

---

## 9. The one test to run first

If you run only one thing on the device, run this. It settles the pin map, the orientation and
the usable range in a single pass, and it is safe — two ADC reads, no writes, no pins driven.

```
Initialise ADC1 oneshot, ADC_ATTEN_DB_12, ADC_BITWIDTH_12,
channels 8 (GPIO 9) and 9 (GPIO 10).
Print "joy <raw9> <raw10>" at 20 Hz.

1. Hands off, 3 seconds        -> the rest pair. Expect both near 2047.
2. Push to each of the four faceplate marks in turn, hold each 1 s.
   -> which pin moves, in which direction, and how far.
3. Sweep the stick all the way round its travel.
   -> cross = 4-way gate, disc/rounded square = free stick.
```

Expected if this document is right: two smoothly varying values, both resting near mid-scale,
each swinging a few hundred counts or more when deflected, and *both* changing on a diagonal.

Expected if it is wrong: one or both channels pinned at 0 or 4095 regardless of the stick, or
both moving together identically (which would mean one of 9/10 is not the joystick at all). In
that case **stop and re-derive** rather than guessing the neighbouring pins — and check first
that the I²C SCL fallback of §5.1 has not claimed GPIO 9 before the ADC did.

Note that `analogRead`'s per-pin lazy init is what stock relies on, so if you see
`"Pin 9 is not ADC pin!"`-style failures in an IDF port, the cause is the pad being held or
already owned by another driver, not the channel mapping.
