# The vendor HID RPC

The **stock** Creator Micro 2 firmware exposes a JSON-RPC service over a vendor-defined HID
collection. LibreMicro's own firmware does not implement any of this — it speaks the serial
protocol in [`PROTOCOL.md`](PROTOCOL.md) instead. This document exists for three reasons:

1. The `host/swift/` tools (`wlrpc`, `wllights`, `wllist`, `wlmon`, `rpc_raw`) talk to a
   **stock-running** pad, which is useful for probing a device before you convert it and for
   the restore workflow in [`RECOVERY.md`](RECOVERY.md).
2. It records *why* custom firmware was necessary: the only vendor colour path,
   `lights.preview`, does whole-zone colours and nothing finer.
3. The framing cost real time to crack, and the two gotchas below (the IOKit offset asymmetry
   and the non-exclusive open) are not documented anywhere by the vendor.

Recovered from **`@worklouder/wl-device-kit@0.1.29`**, which ships *unpacked with source maps*
inside `input.app` — 110 original TypeScript files, of which
`src/wl_device_comm/wl_device_comm_impl.ts` documents the wire format in comments. Reproduce
the extraction with `tools/extract_sources.py`. Those files are vendor IP and are not committed
here.

## Transport

Vendor HID collection: **usage page `0xFF00`, usage `0x01`, report ID 6**, with a 63-byte Input
and a 63-byte Output report. Over BLE the device additionally exposes a Feature report; over USB
it does not.

```
64-byte HID report (node-hid layout):
  [0] 0x06        report ID
  [1] channel     1 = debug log, 2 = RPC
  [2] length      payload bytes in this packet (max 61)
  [3..] UTF-8 JSON payload, split across packets if longer
```

Rules that are not optional:

- Requests are raw JSON with **no trailing newline**.
- Responses are `\r\n` terminated, and the terminator usually arrives as **its own follow-up
  packet** — literally `06 02 02 0d 0a`. A reader must accumulate per channel until it sees a
  newline rather than treating one report as one message.
- **50 ms inter-request cooldown.** Faster than that and the device drops calls.
- The call `id` must be in `[0, 999)`.

### The IOKit offset asymmetry

This is the one that costs an hour if you don't know it. `IOHIDDeviceSetReport` takes the report
ID as a **separate argument** and a buffer *without* it — so you send 63 bytes,
`[channel, len, payload…]`. But the input-report callback delivers the **full** report
*including* the leading `0x06`. Receive offsets are therefore `channel = b[1]`, `len = b[2]`,
`payload = b[3..]` — shifted by one relative to what you wrote.

### macOS access

- The device must be opened with **`kIOHIDOptionsTypeNone`** (non-exclusive). It presents a
  keyboard collection alongside the vendor one, and macOS refuses an exclusive seize of that.
- **cython-hidapi cannot do this.** It always seizes and exposes no toggle for it. That is the
  entire reason the tools in `host/swift/` are Swift/IOKit rather than Python.
- The calling terminal needs **Input Monitoring** permission. Symptom when it's missing:
  `IOHIDDeviceOpen` succeeds but `SetReport` returns `kIOReturnNotPermitted` (`0xE00002E2`) —
  i.e. it fails late and unhelpfully.

## Method surface

The complete set, enumerated from strings in the stock v0.6.1 image:

```
sys.version  sys.bootloader  sys.selftest  sys.charger_diagnostic
device.status
fs.list  fs.read  fs.write  fs.readbin  fs.writebin  fs.delete  fs.chksm  fs.format
host.focused_app
lights.preview
kb.cs.show  kb.cs.hide  kb.cs.toggle  kb.radial
kb.sa.exec  kb.sa.inserttext  kb.sa.openapp  kb.sa.openurl
sentry.get  sentry.crash  sentry.coredump  sentry.coredump_erase
power.max77972.summary  power.max77972.register_dump
v.oai.hid  v.oai.rad  v.oai.rgbcfg  v.oai.thstatus
```

A legacy string form also still works for a few of them: `#<rpc>#<args>#\r\n` (`version`,
`bootloader`, `selftest`).

`sys.bootloader` is the one used in anger — [`RECOVERY.md`](RECOVERY.md) and
`scripts/enter_bootloader.sh` call it to reboot a stock pad into the ROM bootloader, and its
reply `{"rescue":"rear_button_via_ulp"}` is what led to decoding the ULP rear-button watcher
described in [`HARDWARE.md`](HARDWARE.md).

## Lighting: `lights.preview` is the whole story

`lights.preview` works and is the **only** functional colour path in the vendor RPC. Two zones,
one colour each:

```json
{"backlight":{"effect":"solid","brightness":1,"speed":0.5,"magic":1,"color":16711680},
 "underglow":{"effect":"solid","brightness":1,"speed":0.5,"magic":1,"color":255}}
```

Effects the firmware actually implements:

```
solid   snake   rainbow   gradient   shallow_breath   off
```

Note the disagreement: the **vendor SDK's** enum says `breath`, the **firmware's** string pool
says `shallow_breath`. The vendor's own client and its own device don't agree on the name of one
of six effects, which is itself evidence that this surface is unstable and not a contract worth
building on.

There is **no acquire/release primitive** anywhere in the SDK or the firmware — lighting is
last-writer-wins between the vendor app, any RPC client, and the device's own effect engine.

And there are no per-key indicators anywhere in the 2.3 MB image: zero hits for `per_key`,
`perkey`, `rgb_matrix`, `set_key`, `key_color`, `led_index`, or `colors`. Per-pixel control
exists (it has to — there are 13 addressable LEDs, see [`HARDWARE.md`](HARDWARE.md)) but the
firmware keeps it strictly internal. **That is why LibreMicro's firmware exists.**

## `v.oai.*` — a falsification matrix

The `v.oai.*` methods look like the missing per-key surface. They are not. Registration is
variant-gated, so on this board:

| method | result |
|---|---|
| `v.oai.thstatus` | `{"ok":1}` — registered |
| `v.oai.rgbcfg` | `{"ok":1}` — registered |
| `v.oai.hid` | `404 Method not found` |
| `v.oai.rad` | `404 Method not found` |
| `nope.notreal` (control) | `404` — which is what proves 404 means *unregistered* |

**`v.oai.rgbcfg` accepts and acks anything, and sets nothing.** It returns `ok:1` even for
`params:null`, so `ok:1` from it proves nothing at all. Everything below was sent and
**visually confirmed to be a no-op** on real hardware:

| payload tried | result |
|---|---|
| `{keys:{effect,color,…}}` | `ok:1`, no visible change |
| `{keys:{…},ambient:{…}}` with the full 5-field inner shape | `ok:1`, no visible change |
| a 13-element `keys` colour array | `ok:1`, no visible change |
| the same payloads via `lights.preview` | rejected / no change |
| after "arming" with `{"syncKeysLighting":true,"syncAmbientLighting":true,"act":1}` | `ok:1`, no visible change |

Those three camelCase identifiers are not guesses — they appear in the handler's own string pool
alongside the source path `src/oai/wl_oai_bridge.cpp`. The conclusion is that `v.oai.rgbcfg` is
a **control/ownership config method, not a colour setter**. Firmware logs
`OAI BRIDGE: init, v.oai.thstatus registered on all variants`.

The general lesson, recorded in [`REVERSE-ENGINEERING.md`](REVERSE-ENGINEERING.md) as well: on
this device an `ok` response is not evidence of an effect. Only the LEDs are evidence.
