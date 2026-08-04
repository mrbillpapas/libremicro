// Read-only RPC client for Work Louder devices (Creator Micro 2), via IOKit HID.
//
// Framing recovered from Work Louder's own SDK source
// (@worklouder/wl-device-kit 0.1.29, wl_device_comm_impl.ts):
//
//   64-byte HID report, node-hid convention:
//     byte 0 : 0x06        report ID
//     byte 1 : channel     1 = debug log, 2 = RPC
//     byte 2 : payload length (max 61)
//     byte 3+: UTF-8 payload, split across packets if longer than 61 bytes
//
//   IOHIDDeviceSetReport takes the report ID as a separate argument and the
//   buffer WITHOUT it, so our 63-byte buffer is [channel, length, payload...].
//   Same shift applies to the input callback: reportID arrives separately, so
//   received bytes are [channel, length, payload...].
//
//   Requests are raw JSON with NO trailing newline. Responses ARE newline
//   terminated and must be accumulated per channel until a \r?\n arrives.
//   The SDK enforces a 50 ms inter-request cooldown; matched here.
//
// Opens with kIOHIDOptionsTypeNone (non-exclusive) because macOS refuses an
// exclusive seize of a device presenting a keyboard collection.
//
// Sends ONLY read-only methods. Never fs.write*, never sys.bootloader.
//
// build: swiftc -O rpc_probe.swift -o rpc_probe

import Foundation
import IOKit
import IOKit.hid

let VID = 0x303A
let REPORT_ID: CFIndex = 6
let PAYLOAD = 63          // report size excluding the report ID
let MAX_CHUNK = 61
let CHANNEL_RPC: UInt8 = 2
let CHANNEL_DEBUG: UInt8 = 1

// Read-only subset of JsonRPCMethods from the SDK.
let PROBE: [(String, Any?)] = [
    ("sys.version",          nil),
    ("device.status",        nil),
    ("fs.list",              nil),
    ("appmgr.list_active",   nil),
    ("ui.active_screen",     nil),
]

var lineBuffers: [UInt8: String] = [CHANNEL_RPC: "", CHANNEL_DEBUG: ""]
var rpcLines: [String] = []
var debugLines: [String] = []

func ioReturnName(_ r: IOReturn) -> String {
    switch UInt32(bitPattern: r) {
    case 0x0000_0000: return "success"
    case 0xE000_02C5: return "kIOReturnExclusiveAccess"
    case 0xE000_02E2: return "kIOReturnNotPermitted (TCC / Input Monitoring)"
    case 0xE000_02BC: return "kIOReturnError"
    case 0xE000_02C7: return "kIOReturnBadArgument"
    case 0xE000_02D8: return "kIOReturnNotOpen"
    case 0xE000_02D4: return "kIOReturnNoDevice"
    case 0xE000_02EF: return "kIOReturnUnsupported"
    default: return String(format: "IOReturn 0x%08X", UInt32(bitPattern: r))
    }
}

// NOTE the asymmetry: IOHIDDeviceSetReport takes the report ID as a separate
// argument and a buffer WITHOUT it, but the input-report callback delivers the
// FULL report INCLUDING the leading report-ID byte. So receive offsets match
// node-hid's layout exactly: [0x06, channel, length, payload...].
let inputCallback: IOHIDReportCallback = { _, _, _, _, _, report, reportLength in
    guard reportLength >= 3 else { return }
    let bytes = Array(UnsafeBufferPointer(start: report, count: Int(reportLength)))
    let channel = bytes[1]
    let len = Int(bytes[2])
    guard len > 0, 3 + len <= bytes.count else { return }
    let payload = Array(bytes[3..<(3 + len)])
    guard let text = String(bytes: payload, encoding: .utf8) else { return }

    lineBuffers[channel, default: ""] += text
    let buf = lineBuffers[channel]!
    guard buf.contains("\n") || buf.contains("\r") else { return }
    var parts = buf.components(separatedBy: CharacterSet(charactersIn: "\r\n"))
    let tail = parts.removeLast()
    for line in parts where !line.trimmingCharacters(in: .whitespaces).isEmpty {
        if channel == CHANNEL_RPC { rpcLines.append(line) } else { debugLines.append(line) }
    }
    lineBuffers[channel] = tail
}

/// Split a JSON string into framed 63-byte output reports.
func frames(_ message: String) -> [[UInt8]] {
    let body = Array(message.utf8)
    var out: [[UInt8]] = []
    var offset = 0
    while offset < body.count {
        let n = min(MAX_CHUNK, body.count - offset)
        var pkt = [UInt8](repeating: 0, count: PAYLOAD)
        pkt[0] = CHANNEL_RPC
        pkt[1] = UInt8(n)
        for i in 0..<n { pkt[2 + i] = body[offset + i] }
        out.append(pkt)
        offset += n
    }
    return out
}

func pump(_ seconds: Double, until: () -> Bool = { false }) {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        CFRunLoopRunInMode(.defaultMode, 0.03, true)
        if until() { return }
    }
}

// ---- locate device

let manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))
IOHIDManagerSetDeviceMatching(manager, [kIOHIDVendorIDKey: VID] as CFDictionary)

guard let set = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice>, !set.isEmpty else {
    print("no Work Louder device (VID 0x303A) found — is it connected over USB?")
    exit(1)
}
let device = set.first!
let product = IOHIDDeviceGetProperty(device, kIOHIDProductKey as CFString) as? String ?? "?"
let pid = IOHIDDeviceGetProperty(device, kIOHIDProductIDKey as CFString) as? Int ?? 0
let maxIn = IOHIDDeviceGetProperty(device, kIOHIDMaxInputReportSizeKey as CFString) as? Int ?? 64
print(String(format: "device: %@  pid=0x%04X  maxInputReportSize=%d", product, pid, maxIn))

let rc = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
guard rc == kIOReturnSuccess else {
    print("OPEN FAILED: \(ioReturnName(rc))")
    exit(1)
}
print("opened non-exclusively\n")

var inBuf = [UInt8](repeating: 0, count: max(maxIn, PAYLOAD + 1))
IOHIDDeviceRegisterInputReportCallback(device, &inBuf, inBuf.count, inputCallback, nil)
IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)

// ---- probe read-only methods

var callId = 1
for (method, params) in PROBE {
    rpcLines.removeAll()
    lineBuffers[CHANNEL_RPC] = ""

    let paramsJson: String
    if let p = params,
       let d = try? JSONSerialization.data(withJSONObject: p),
       let s = String(data: d, encoding: .utf8) {
        paramsJson = s
    } else {
        paramsJson = "null"
    }
    let json = "{\"method\":\"\(method)\",\"params\":\(paramsJson),\"id\":\(callId)}"
    let pkts = frames(json)

    print("--- \(method)   (\(json.utf8.count)B, \(pkts.count) packet\(pkts.count == 1 ? "" : "s"))")

    var sendOK = true
    for pkt in pkts {
        let r = IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, REPORT_ID, pkt, pkt.count)
        if r != kIOReturnSuccess {
            print("      SetReport failed: \(ioReturnName(r))")
            sendOK = false
            break
        }
    }
    guard sendOK else { callId += 1; continue }

    pump(2.0, until: { !rpcLines.isEmpty })
    pump(0.2)

    if rpcLines.isEmpty {
        print("      no RPC response")
    } else {
        for line in rpcLines {
            if let d = line.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: d),
               let pretty = try? JSONSerialization.data(withJSONObject: obj,
                                                        options: [.prettyPrinted, .sortedKeys]),
               let s = String(data: pretty, encoding: .utf8) {
                print("      \(s.replacingOccurrences(of: "\n", with: "\n      "))")
            } else {
                print("      raw: \(line)")
            }
        }
    }
    if !debugLines.isEmpty {
        for l in debugLines { print("      [LOG] \(l)") }
        debugLines.removeAll()
    }
    print()
    callId += 1
    pump(0.05)   // SDK's inter-request cooldown
}

IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
IOHIDDeviceClose(device, IOOptionBits(kIOHIDOptionsTypeNone))
