#!/usr/bin/env python3
"""Enumerate Work Louder Creator Micro 2 HID interfaces (read-only)."""
import hid

VID = 0x303A

for d in hid.enumerate(VID, 0):
    print(f"path            {d['path'].decode(errors='replace')}")
    print(f"  vid:pid       {d['vendor_id']:#06x}:{d['product_id']:#06x}")
    print(f"  product       {d.get('product_string')}")
    print(f"  manufacturer  {d.get('manufacturer_string')}")
    print(f"  serial        {d.get('serial_number')}")
    print(f"  usage_page    {d.get('usage_page'):#06x}  usage {d.get('usage'):#04x}")
    print(f"  iface         {d.get('interface_number')}")
    print()
