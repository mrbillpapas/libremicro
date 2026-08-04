// wllist — enumerate every IOHIDDevice for VID 0x303A with the properties that
// determine whether it is writable. build: swiftc -Onone wllist.swift -o wllist

import Foundation
import IOKit
import IOKit.hid

let VID = 0x303A

let mgr = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
IOHIDManagerSetDeviceMatching(mgr, [kIOHIDVendorIDKey: VID] as CFDictionary)

guard let set = IOHIDManagerCopyDevices(mgr) as? Set<IOHIDDevice> else {
    print("copyDevices returned nil"); exit(1)
}
print("IOHIDManagerCopyDevices -> \(set.count) device(s)\n")

func prop(_ d: IOHIDDevice, _ k: String) -> Any? {
    IOHIDDeviceGetProperty(d, k as CFString)
}

for (i, d) in set.enumerated() {
    let pid = prop(d, kIOHIDProductIDKey) as? Int ?? -1
    let up = prop(d, kIOHIDPrimaryUsagePageKey) as? Int ?? -1
    let u = prop(d, kIOHIDPrimaryUsageKey) as? Int ?? -1
    print("[\(i)] \(prop(d, kIOHIDProductKey) as? String ?? "?")")
    print(String(format: "     pid=0x%04X  primaryUsagePage=0x%04X usage=0x%02X", pid, up, u))
    print("     transport=\(prop(d, kIOHIDTransportKey) as? String ?? "?")")
    print("     locationID=\(prop(d, kIOHIDLocationIDKey) as? Int ?? -1)")
    print("     serial=\(prop(d, kIOHIDSerialNumberKey) as? String ?? "?")")
    print("     maxIn=\(prop(d, kIOHIDMaxInputReportSizeKey) as? Int ?? -1)"
        + "  maxOut=\(prop(d, kIOHIDMaxOutputReportSizeKey) as? Int ?? -1)")

    let rc = IOHIDDeviceOpen(d, IOOptionBits(kIOHIDOptionsTypeNone))
    print(String(format: "     open -> 0x%08X", UInt32(bitPattern: rc)))
    if rc == kIOReturnSuccess {
        // zero-length-safe probe: channel 2, length 0 payload (a no-op frame)
        var pkt = [UInt8](repeating: 0, count: 63)
        pkt[0] = 2
        pkt[1] = 0
        let wr = IOHIDDeviceSetReport(d, kIOHIDReportTypeOutput, 6, pkt, pkt.count)
        print(String(format: "     SetReport(empty) -> 0x%08X%@", UInt32(bitPattern: wr),
                     wr == kIOReturnSuccess ? "  WRITABLE" : ""))
        IOHIDDeviceClose(d, IOOptionBits(kIOHIDOptionsTypeNone))
    }
    print()
}
