// lmvol — system volume for LibreMicro, done the way a rotary dial needs it.
//
// The two existing volume paths each gave up half of what the dial wants:
//
//   * the real media key (lmkey media vol_up) shows the native macOS slider, but only
//     moves on macOS's 16-step grid — ~6.25% a detent, chunky under a dial;
//   * `set volume output volume N` via osascript takes any step size, but macOS shows
//     no overlay for a programmatic set, and each osascript spawn costs tens of
//     milliseconds — a fast spin queues them and the level arrives out of order.
//
// This tool owns the first path done properly: the level is set directly through CoreAudio
// (kAudioHardwareServiceDeviceProperty_VirtualMainVolume on the default output device —
// synchronous, sub-millisecond, no AppleScript), and reads take ~10 ms where osascript
// takes ~60. On top of that it *attempts* the native HUD through the private OSD framework,
// the MonitorControl/Lunar technique. Know the limit: on current macOS the volume HUD is a
// ControlCenter system banner presented only by ControlCenter's own media-key observer, and
// OSDManager no longer draws it — confirmed by log tracing on 26A5388g, where this call
// completes and nothing appears. On older systems the OSD call works; here it is
// best-effort. If the native banner is a requirement, press the real media key (lmkey) and
// accept macOS's 16-step grid — that is what the daemon's "coarse" volume mode does.
//
//   usage:
//     lmvol get                 print "<level> muted|unmuted" and exit
//     lmvol set <0-100>         set the level exactly
//     lmvol up [step]           raise by step percent (default 2); unmutes
//     lmvol down [step]         lower by step percent (default 2)
//     lmvol mute [on|off|toggle]   default toggle
//     lmvol osd                 show the HUD at the current level without changing it
//
//   options:
//     --no-osd     change the volume but show no overlay
//     --unmute     with `set`: also clear mute (what `up` does implicitly)
//
//   Every state-changing subcommand prints the resulting "<level> muted|unmuted" on
//   stdout, so the caller can update its own feedback (the pad's underglow bar) without
//   a second read.
//
//   exit: 0 ok · 2 bad usage · 5 CoreAudio failure
//   The OSD is best-effort: if the private framework is missing or its ABI moved (it is
//   private; Apple may change it), the volume still changes, "osd: unavailable" goes to
//   stderr once, and the exit code stays 0. The pad's underglow bar is the fallback UI.
//
//   No Accessibility permission is needed — nothing here synthesises input events.
//
//   build:  swiftc -O -o lmvol lmvol.swift
//   (the binary is gitignored; build it from source)

import AudioToolbox   // kAudioHardwareServiceDeviceProperty_VirtualMainVolume
import CoreAudio
import CoreGraphics
import Foundation

let programName = "lmvol"
let exitOK: Int32 = 0
let exitUsage: Int32 = 2
let exitAudio: Int32 = 5

func warn(_ message: String) {
    FileHandle.standardError.write(Data(("\(programName): " + message + "\n").utf8))
}

func die(_ message: String, _ code: Int32 = exitUsage) -> Never {
    warn(message)
    exit(code)
}

let usage = """
usage: \(programName) get | set <0-100> | up [step] | down [step] | mute [on|off|toggle] | osd
options: --no-osd   change the volume but show no overlay
         --unmute   with `set`: also clear mute (what `up` does implicitly)
"""

// MARK: - CoreAudio

func defaultOutputDevice() -> AudioDeviceID? {
    var deviceID = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                            &addr, 0, nil, &size, &deviceID)
    return status == noErr && deviceID != 0 ? deviceID : nil
}

/// The virtual main volume is the same control the volume keys move: it abstracts over
/// devices with per-channel but no master control, which is why it is preferred over
/// kAudioDevicePropertyVolumeScalar here.
var volumeAddr = AudioObjectPropertyAddress(
    mSelector: kAudioHardwareServiceDeviceProperty_VirtualMainVolume,
    mScope: kAudioDevicePropertyScopeOutput,
    mElement: kAudioObjectPropertyElementMain)

var muteAddr = AudioObjectPropertyAddress(
    mSelector: kAudioDevicePropertyMute,
    mScope: kAudioDevicePropertyScopeOutput,
    mElement: kAudioObjectPropertyElementMain)

func readVolume(_ device: AudioDeviceID) -> Float32? {
    var value = Float32(0)
    var size = UInt32(MemoryLayout<Float32>.size)
    guard AudioObjectGetPropertyData(device, &volumeAddr, 0, nil, &size, &value) == noErr
    else { return nil }
    return max(0, min(1, value))
}

func writeVolume(_ device: AudioDeviceID, _ value: Float32) -> Bool {
    var v = max(0, min(1, value))
    let size = UInt32(MemoryLayout<Float32>.size)
    return AudioObjectSetPropertyData(device, &volumeAddr, 0, nil, size, &v) == noErr
}

func readMuted(_ device: AudioDeviceID) -> Bool? {
    guard AudioObjectHasProperty(device, &muteAddr) else { return false }
    var value = UInt32(0)
    var size = UInt32(MemoryLayout<UInt32>.size)
    guard AudioObjectGetPropertyData(device, &muteAddr, 0, nil, &size, &value) == noErr
    else { return nil }
    return value != 0
}

func writeMuted(_ device: AudioDeviceID, _ muted: Bool) -> Bool {
    guard AudioObjectHasProperty(device, &muteAddr) else { return !muted }
    var v: UInt32 = muted ? 1 : 0
    let size = UInt32(MemoryLayout<UInt32>.size)
    return AudioObjectSetPropertyData(device, &muteAddr, 0, nil, size, &v) == noErr
}

// MARK: - native HUD, via the private OSD framework
//
// OSDManager lives in /System/Library/PrivateFrameworks/OSD.framework and is what the
// window server's own volume/brightness keys drive. `showImage:onDisplayID:priority:
// msecUntilFade:filledChiclets:totalChiclets:locked:` draws the standard bezel; passing
// filled/total as value*100/100 rather than n/16 gives a continuous fill, which is the
// whole point — the 16-chiclet grid is a rendering choice, not a system limit.
//
// Being private, none of this is linkable: the framework is dlopen'd, the class looked
// up by name, and the method called through its IMP. Every step is checked so an ABI
// change degrades to "no overlay", never to a crash.

let osdImageSpeaker: Int64 = 3
let osdImageSpeakerMuted: Int64 = 4

func showVolumeHUD(level: Float32, muted: Bool) -> Bool {
    guard dlopen("/System/Library/PrivateFrameworks/OSD.framework/OSD", RTLD_LAZY) != nil,
          let cls = NSClassFromString("OSDManager") as? NSObject.Type
    else { return false }

    let sharedSel = NSSelectorFromString("sharedManager")
    guard cls.responds(to: sharedSel),
          let manager = cls.perform(sharedSel)?.takeUnretainedValue()
    else { return false }

    let showSel = NSSelectorFromString(
        "showImage:onDisplayID:priority:msecUntilFade:filledChiclets:totalChiclets:locked:")
    guard let method = class_getInstanceMethod(object_getClass(manager), showSel)
    else { return false }

    typealias ShowImageFn = @convention(c) (
        AnyObject, Selector, Int64, CGDirectDisplayID,
        UInt32, UInt32, UInt32, UInt32, Bool) -> Void
    let fn = unsafeBitCast(method_getImplementation(method), to: ShowImageFn.self)

    let image = (muted || level <= 0) ? osdImageSpeakerMuted : osdImageSpeaker
    fn(manager, showSel, image, CGMainDisplayID(),
       0x1F4, 1000, UInt32((level * 100).rounded()), 100, false)
    // The bezel is drawn by a helper process reached over XPC; give the message a moment
    // to leave before this process exits and the connection is torn down with it.
    Thread.sleep(forTimeInterval: 0.02)
    return true
}

var osdWarned = false
func maybeShowHUD(_ enabled: Bool, level: Float32, muted: Bool) {
    guard enabled else { return }
    if !showVolumeHUD(level: level, muted: muted) && !osdWarned {
        osdWarned = true
        warn("osd: unavailable (private OSD framework missing or changed); "
             + "volume still set")
    }
}

// MARK: - main

var argv = Array(CommandLine.arguments.dropFirst())
var wantOSD = true
var wantUnmute = false
argv.removeAll { a in
    if a == "--no-osd" { wantOSD = false; return true }
    if a == "--unmute" { wantUnmute = true; return true }
    return false
}

guard let subcommand = argv.first else {
    FileHandle.standardError.write(Data((usage + "\n").utf8))
    exit(exitUsage)
}
let operands = Array(argv.dropFirst())

if subcommand == "-h" || subcommand == "--help" || subcommand == "help" {
    print(usage)
    exit(exitOK)
}

guard let device = defaultOutputDevice() else {
    die("no default audio output device", exitAudio)
}

func report(_ device: AudioDeviceID) {
    let level = readVolume(device).map { Int(($0 * 100).rounded()) }
    let muted = readMuted(device) ?? false
    print("\(level.map(String.init) ?? "?") \(muted ? "muted" : "unmuted")")
}

func parseStep(_ operands: [String]) -> Float32 {
    guard let raw = operands.first else { return 2 }
    guard let step = Float32(raw), step > 0, step <= 100 else {
        die("step must be a number of percent, 0-100 (got '\(raw)')")
    }
    return step
}

switch subcommand {
case "get":
    report(device)

case "set":
    guard let raw = operands.first, let pct = Float32(raw), (0...100).contains(pct) else {
        die("set: needs a level 0-100")
    }
    guard writeVolume(device, pct / 100) else { die("could not set volume", exitAudio) }
    var muted = readMuted(device) ?? false
    if wantUnmute && muted {
        _ = writeMuted(device, false)
        muted = false
    }
    maybeShowHUD(wantOSD, level: pct / 100, muted: muted)
    report(device)

case "up", "down":
    let step = parseStep(operands) / 100 * (subcommand == "up" ? 1 : -1)
    guard let current = readVolume(device) else { die("could not read volume", exitAudio) }
    let target = max(0, min(1, current + step))
    guard writeVolume(device, target) else { die("could not set volume", exitAudio) }
    // Raising volume while muted must unmute, or the dial appears dead — the same rule
    // the daemon's AppleScript path applied.
    var muted = readMuted(device) ?? false
    if subcommand == "up" && muted {
        _ = writeMuted(device, false)
        muted = false
    }
    maybeShowHUD(wantOSD, level: target, muted: muted)
    report(device)

case "mute":
    let want = operands.first ?? "toggle"
    let current = readMuted(device) ?? false
    let target: Bool
    switch want {
    case "on": target = true
    case "off": target = false
    case "toggle": target = !current
    default: die("mute: expected on, off or toggle (got '\(want)')")
    }
    guard writeMuted(device, target) else { die("could not set mute", exitAudio) }
    let level = readVolume(device) ?? 0
    maybeShowHUD(wantOSD, level: target ? 0 : level, muted: target)
    report(device)

case "osd":
    let level = readVolume(device) ?? 0
    let muted = readMuted(device) ?? false
    maybeShowHUD(true, level: level, muted: muted)
    report(device)

default:
    die("unknown subcommand '\(subcommand)'\n\n\(usage)")
}
exit(exitOK)
