// Raw HID report dumper: send one RPC request, print every input report verbatim.
// Diagnostic only — no parsing assumptions. build: swiftc -O rpc_raw.swift -o rpc_raw

import Foundation
import IOKit
import IOKit.hid

let VID = 0x303A
let REPORT_ID: CFIndex = 6
let PAYLOAD = 63
let MAX_CHUNK = 61
let CHANNEL_RPC: UInt8 = 2

let method = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "sys.version"
let waitSecs = CommandLine.arguments.count > 2 ? (Double(CommandLine.arguments[2]) ?? 4.0) : 4.0

var reportCount = 0

let cb: IOHIDReportCallback = { _, _, _, _, reportID, report, reportLength in
    reportCount += 1
    let b = Array(UnsafeBufferPointer(start: report, count: Int(reportLength)))
    let hex = b.map { String(format: "%02x", $0) }.joined(separator: " ")
    let ascii = String(b.map { (32...126).contains($0) ? Character(UnicodeScalar($0)) : "." })
    print("[\(reportCount)] cbReportID=\(reportID) len=\(reportLength)")
    print("    hex   \(hex)")
    print("    ascii \(ascii)")
}

let mgr = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
IOHIDManagerSetDeviceMatching(mgr, [kIOHIDVendorIDKey: VID] as CFDictionary)
guard let set = IOHIDManagerCopyDevices(mgr) as? Set<IOHIDDevice>, let dev = set.first else {
    print("device not found"); exit(1)
}
guard IOHIDDeviceOpen(dev, IOOptionBits(kIOHIDOptionsTypeNone)) == kIOReturnSuccess else {
    print("open failed"); exit(1)
}

let maxIn = IOHIDDeviceGetProperty(dev, kIOHIDMaxInputReportSizeKey as CFString) as? Int ?? 64
var buf = [UInt8](repeating: 0, count: max(maxIn, 64))
IOHIDDeviceRegisterInputReportCallback(dev, &buf, buf.count, cb, nil)
IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)

let json = "{\"method\":\"\(method)\",\"params\":null,\"id\":7}"
print("sending: \(json)  (\(json.utf8.count)B)\n")

let body = Array(json.utf8)
var off = 0
while off < body.count {
    let n = min(MAX_CHUNK, body.count - off)
    var pkt = [UInt8](repeating: 0, count: PAYLOAD)
    pkt[0] = CHANNEL_RPC
    pkt[1] = UInt8(n)
    for i in 0..<n { pkt[2 + i] = body[off + i] }
    let r = IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, REPORT_ID, pkt, pkt.count)
    print("SetReport chunk \(n)B -> \(r == kIOReturnSuccess ? "ok" : String(format: "0x%08X", UInt32(bitPattern: r)))")
    off += n
}
print()

let deadline = Date().addingTimeInterval(waitSecs)
while Date() < deadline { CFRunLoopRunInMode(.defaultMode, 0.05, true) }

print("\ntotal input reports: \(reportCount)")
IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
