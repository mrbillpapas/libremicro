#!/usr/bin/env bash
# Reboot the device into the ESP32-S3 ROM bootloader via the vendor RPC, then
# wait for its USB-Serial-JTAG port to appear and print the path on stdout.
#
# Needed before every esptool/espefuse invocation, because esptool's hard reset
# hands control back to the app firmware (which uses USB-OTG/TinyUSB HID, not
# USB-Serial-JTAG), so the serial port disappears again after each command.
#
# Recovery if the app firmware is ever broken: the device reported
#   {"rescue":"rear_button_via_ulp"}
# so the rear button + ULP watcher re-enters the bootloader in hardware.

set -euo pipefail
cd "$(dirname "$0")"

existing=$(ls /dev/cu.usbmodem* 2>/dev/null || true)
if [ -n "$existing" ]; then
    echo "$existing" | head -1
    exit 0
fi

./wlrpc sys.bootloader "" 3 >/dev/null 2>&1 || true

for _ in $(seq 1 25); do
    port=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)
    if [ -n "$port" ]; then
        sleep 0.6            # let the CDC/JTAG endpoint settle
        echo "$port"
        exit 0
    fi
    sleep 0.4
done

echo "ERROR: bootloader serial port never appeared" >&2
exit 1
