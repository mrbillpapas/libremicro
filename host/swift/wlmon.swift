// wlmon — passive monitor for Work Louder devices. Opens non-exclusively and
// prints everything the device emits on the vendor HID channel, so you can watch
// the firmware narrate itself while ANOTHER app (Input, ChatGPT/Codex) drives it.
//
// Channels (from wl_device_comm_impl.ts): 1 = debug log, 2 = RPC.
// Anything on another channel is dumped as hex — that's how we'd spot a binary
// per-key LED frame path that has no JSON-RPC method.
//
// Works over USB or BLE — it matches VID 0x303A on whichever transport is live.
//
// usage:  wlmon [seconds]        (default 300; Ctrl-C to stop early)
// build:  swiftc -Onone wlmon.swift -o wlmon

import Foundation
import IOKit
import IOKit.hid

let VID = 0x303A
let CH_DEBUG: UInt8 = 1
let CH_RPC: UInt8 = 2

final class Mon {
    var buffers: [UInt8: String] = [:]
    var reports = 0
    var start = Date()
    var seenChannels = Set<UInt8>()
}
let mon = Mon()

func stamp() -> String {
    String(format: "%7.3f", Date().timeIntervalSince(mon.start))
}

let cb: IOHIDReportCallback = { _, _, _, _, _, report, reportLength in
    guard reportLength >= 3 else { return }
    let b = Array(UnsafeBufferPointer(start: report, count: Int(reportLength)))
    let channel = b[1]          // b[0] is the report ID
    let len = Int(b[2])
    mon.reports += 1

    if !mon.seenChannels.contains(channel) {
        mon.seenChannels.insert(channel)
        print("\(stamp())  ** new channel \(channel) **")
    }

    guard len > 0, 3 + len <= b.count else {
        let hex = b.prefix(16).map { String(format: "%02x", $0) }.joined(separator: " ")
        print("\(stamp())  ch\(channel) len=\(len) (odd) \(hex)")
        return
    }
    let payload = Array(b[3..<(3 + len)])

    // Channels 1 and 2 are UTF-8 line streams; anything else is likely binary.
    guard channel == CH_DEBUG || channel == CH_RPC,
          let text = String(bytes: payload, encoding: .utf8) else {
        let hex = payload.map { String(format: "%02x", $0) }.joined(separator: " ")
        print("\(stamp())  ch\(channel) BINARY len=\(len)  \(hex)")
        return
    }

    mon.buffers[channel, default: ""] += text
    let buf = mon.buffers[channel]!
    guard buf.rangeOfCharacter(from: CharacterSet(charactersIn: "\r\n")) != nil else { return }
    var parts = buf.components(separatedBy: CharacterSet(charactersIn: "\r\n"))
    mon.buffers[channel] = parts.removeLast()
    let tag = channel == CH_DEBUG ? "LOG" : "RPC"
    for line in parts where !line.trimmingCharacters(in: .whitespaces).isEmpty {
        print("\(stamp())  [\(tag)] \(line)")
    }
}

let secs = CommandLine.arguments.count > 1 ? (Double(CommandLine.arguments[1]) ?? 300) : 300

let mgr = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
IOHIDManagerSetDeviceMatching(mgr, [kIOHIDVendorIDKey: VID] as CFDictionary)
guard let set = IOHIDManagerCopyDevices(mgr) as? Set<IOHIDDevice>, let dev = set.first else {
    print("no Work Louder device (VID 0x303A) found on USB or BLE"); exit(1)
}
let product = IOHIDDeviceGetProperty(dev, kIOHIDProductKey as CFString) as? String ?? "?"
let transport = IOHIDDeviceGetProperty(dev, kIOHIDTransportKey as CFString) as? String ?? "?"
guard IOHIDDeviceOpen(dev, IOOptionBits(kIOHIDOptionsTypeNone)) == kIOReturnSuccess else {
    print("open failed"); exit(1)
}
print("monitoring \(product) over \(transport) for \(Int(secs))s — drive the device from another app now\n")

let maxIn = IOHIDDeviceGetProperty(dev, kIOHIDMaxInputReportSizeKey as CFString) as? Int ?? 64
var buf = [UInt8](repeating: 0, count: max(maxIn, 64))
IOHIDDeviceRegisterInputReportCallback(dev, &buf, buf.count, cb, nil)
IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)

mon.start = Date()
let deadline = Date().addingTimeInterval(secs)
while Date() < deadline { CFRunLoopRunInMode(.defaultMode, 0.25, true) }

print("\n--- done: \(mon.reports) reports, channels seen: \(mon.seenChannels.sorted())")
IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
