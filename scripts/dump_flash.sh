#!/usr/bin/env bash
# Back up the USED region of flash (0x000000-0xA40000, 10.75MB) in 512KB chunks.
#
# Layout from the on-device partition table:
#   phy_init 0x00F000 4K | factory 0x010000 8M | nvs 0x810000 128K
#   fs (littlefs) 0x830000 2M | coredump 0xA30000 64K   -> ends 0xA40000
# The remaining ~5.25MB to 0x1000000 is erased and not worth reading.
#
# Two failure modes this works around:
#  1. A single long read-flash over USB-Serial-JTAG dies with
#     "Serial data stream stopped" (~1.4MB in). Hence chunking.
#  2. --after no-reset leaves a stale stub flasher that the NEXT esptool
#     invocation cannot re-sync with (chunk 0 ok, chunk 1 failed 5/5).
#     So each chunk gets a FRESH session: default hard-reset at the end drops
#     the chip back into app firmware, then we re-enter the ROM bootloader over
#     the vendor HID RPC via enter_bootloader.sh.

set -uo pipefail
cd "$(dirname "$0")"

ESPTOOL=./.venv/bin/esptool
OUT=firmware/BACKUP_used_region.bin
CHUNKDIR=firmware/chunks2
CHUNK=$((512 * 1024))
TOTAL=$((0xA40000))
RETRIES=6

mkdir -p "$CHUNKDIR"

wait_for_app_mode() {
    # after hard-reset the app firmware takes over and the JTAG port disappears
    for _ in $(seq 1 12); do
        ls /dev/cu.usbmodem* >/dev/null 2>&1 || return 0
        sleep 0.5
    done
    return 0
}

n=$(( (TOTAL + CHUNK - 1) / CHUNK ))
echo "backing up 0x0-$(printf '0x%X' $TOTAL) in $n chunks of $((CHUNK / 1024))K"

for ((i = 0; i < n; i++)); do
    off=$((i * CHUNK))
    len=$CHUNK
    (( off + len > TOTAL )) && len=$((TOTAL - off))
    f=$(printf "%s/%07x.bin" "$CHUNKDIR" "$off")

    if [ -f "$f" ] && [ "$(stat -f%z "$f")" -eq "$len" ]; then
        echo "chunk $i @ $(printf '0x%07X' $off) already complete"
        continue
    fi

    ok=0
    for ((r = 1; r <= RETRIES; r++)); do
        P=$(./enter_bootloader.sh 2>/dev/null) || { sleep 2; continue; }
        printf "chunk %02d/%02d  0x%07X +%dK  try %d  %s ... " \
            "$i" "$((n - 1))" "$off" "$((len / 1024))" "$r" "$P"
        if $ESPTOOL --port "$P" read-flash "$off" "$len" "$f" >/dev/null 2>&1 \
           && [ -f "$f" ] && [ "$(stat -f%z "$f")" -eq "$len" ]; then
            echo "ok"
            ok=1
            wait_for_app_mode
            break
        fi
        echo "failed"
        rm -f "$f"
        wait_for_app_mode
        sleep 1
    done

    if [ "$ok" -ne 1 ]; then
        echo "ERROR: chunk $i (0x$(printf '%07X' $off)) failed after $RETRIES tries" >&2
        exit 1
    fi
done

echo "concatenating..."
cat $(ls "$CHUNKDIR"/*.bin | sort) > "$OUT"
sz=$(stat -f%z "$OUT")
echo "wrote $OUT ($sz bytes, expected $TOTAL)"
[ "$sz" -eq "$TOTAL" ] && echo "SIZE OK" || echo "SIZE MISMATCH" >&2
shasum -a 256 "$OUT"
