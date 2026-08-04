// wlrpc — generic JSON-RPC client for Work Louder devices over USB HID.
//
// Protocol recovered from Work Louder's own SDK source
// (@worklouder/wl-device-kit 0.1.29, src/wl_device_comm/wl_device_comm_impl.ts):
//
//   Outgoing 64-byte HID report (node-hid layout):
//     [0] 0x06        report ID
//     [1] channel     1 = debug log, 2 = RPC
//     [2] length      payload bytes in this packet (max 61)
//     [3..] UTF-8 payload; messages > 61 bytes split across packets
//
//   IOHIDDeviceSetReport takes the report ID separately and a buffer WITHOUT
//   it (63 bytes: [channel, length, payload...]). The input-report callback,
//   asymmetrically, delivers the FULL report INCLUDING the report-ID byte.
//
//   Requests are raw JSON, no trailing newline. Responses are \r\n terminated,
//   and the terminator typically arrives in its own follow-up packet.
//
// Opened with kIOHIDOptionsTypeNone — macOS refuses an exclusive seize of a
// device presenting a keyboard collection.
//
// usage:  wlrpc <method> [jsonParams] [timeoutSecs]
// build:  swiftc -Onone wlrpc.swift -o wlrpc
//   (-Onone deliberately: state is mutated from a @convention(c) callback,
//    which the optimizer cannot see.)

import Foundation
import IOKit
import IOKit.hid

let VID = 0x303A
let REPORT_ID: CFIndex = 6
let PAYLOAD = 63
let MAX_CHUNK = 61
let CH_DEBUG: UInt8 = 1
let CH_RPC: UInt8 = 2

final class RPCState {
    var buffers: [UInt8: String] = [CH_RPC: "", CH_DEBUG: ""]
    var rpc: [String] = []
    var debug: [String] = []
    var reports = 0
}
let state = RPCState()

let cb: IOHIDReportCallback = { _, _, _, _, _, report, reportLength in
    guard reportLength >= 3 else { return }
    let b = Array(UnsafeBufferPointer(start: report, count: Int(reportLength)))
    let channel = b[1]                      // b[0] is the report ID
    let len = Int(b[2])
    guard len > 0, 3 + len <= b.count else { return }
    guard let text = String(bytes: b[3..<(3 + len)], encoding: .utf8) else { return }

    state.reports += 1
    state.buffers[channel, default: ""] += text
    let buf = state.buffers[channel]!
    guard buf.rangeOfCharacter(from: CharacterSet(charactersIn: "\r\n")) != nil else { return }

    var parts = buf.components(separatedBy: CharacterSet(charactersIn: "\r\n"))
    state.buffers[channel] = parts.removeLast()
    for line in parts where !line.trimmingCharacters(in: .whitespaces).isEmpty {
        if channel == CH_RPC { state.rpc.append(line) } else { state.debug.append(line) }
    }
}

func die(_ msg: String) -> Never { print(msg); exit(1) }

// ---- args

let args = Array(CommandLine.arguments.dropFirst())
guard !args.isEmpty else {
    die("usage: wlrpc <method> [jsonParams] [timeoutSecs]\n" +
        "  e.g. wlrpc sys.version\n" +
        "       wlrpc fs.list '{\"checksum\":true}'\n")
}
let method = args[0]
let paramsRaw = args.count > 1 && !args[1].isEmpty ? args[1] : "null"
let timeout = args.count > 2 ? (Double(args[2]) ?? 6.0) : 6.0

// ---- device

let mgr = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
IOHIDManagerSetDeviceMatching(mgr, [kIOHIDVendorIDKey: VID] as CFDictionary)
guard let set = IOHIDManagerCopyDevices(mgr) as? Set<IOHIDDevice>, let dev = set.first else {
    die("no Work Louder device (VID 0x303A) on USB")
}
let rc = IOHIDDeviceOpen(dev, IOOptionBits(kIOHIDOptionsTypeNone))
guard rc == kIOReturnSuccess else {
    die(String(format: "open failed: 0x%08X%@", UInt32(bitPattern: rc),
               UInt32(bitPattern: rc) == 0xE000_02E2
                 ? "  (kIOReturnNotPermitted — grant Terminal Input Monitoring)" : ""))
}

let maxIn = IOHIDDeviceGetProperty(dev, kIOHIDMaxInputReportSizeKey as CFString) as? Int ?? 64
var inBuf = [UInt8](repeating: 0, count: max(maxIn, 64))
IOHIDDeviceRegisterInputReportCallback(dev, &inBuf, inBuf.count, cb, nil)
IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)

// ---- send

let callId = Int.random(in: 1..<999)     // firmware constrains ids to [0,999)
let json = "{\"method\":\"\(method)\",\"params\":\(paramsRaw),\"id\":\(callId)}"
FileHandle.standardError.write("-> \(json)\n".data(using: .utf8)!)

let body = Array(json.utf8)
var off = 0
while off < body.count {
    let n = min(MAX_CHUNK, body.count - off)
    var pkt = [UInt8](repeating: 0, count: PAYLOAD)
    pkt[0] = CH_RPC
    pkt[1] = UInt8(n)
    for i in 0..<n { pkt[2 + i] = body[off + i] }
    let r = IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, REPORT_ID, pkt, pkt.count)
    guard r == kIOReturnSuccess else {
        die(String(format: "SetReport failed: 0x%08X", UInt32(bitPattern: r)))
    }
    off += n
}

// ---- await response

let deadline = Date().addingTimeInterval(timeout)
while Date() < deadline && state.rpc.isEmpty {
    CFRunLoopRunInMode(.defaultMode, 0.05, true)
}
CFRunLoopRunInMode(.defaultMode, 0.2, true)

for l in state.debug { print("[LOG] \(l)") }

if state.rpc.isEmpty {
    print("no RPC response (input reports seen: \(state.reports))")
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
    exit(2)
}

for line in state.rpc {
    if let d = line.data(using: .utf8),
       let obj = try? JSONSerialization.jsonObject(with: d),
       let p = try? JSONSerialization.data(withJSONObject: obj,
                                           options: [.prettyPrinted, .sortedKeys]),
       let s = String(data: p, encoding: .utf8) {
        print(s)
    } else {
        print(line)
    }
}

IOHIDDeviceUnscheduleFromRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
