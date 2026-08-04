// lmkey — synthesise macOS keyboard input for LibreMicro.
//
// The host daemon needs three things a macropad key can do to the focused app:
// press a chord, press a media key, and type text. `osascript`/System Events can do
// the first and (badly) the third, but it cannot press media keys at all: play/pause,
// next/prev track, volume and brightness are not virtual keycodes, they are NX
// "system-defined" aux-control events (subtype 8) carrying an NX_KEYTYPE_* code. Those
// have to be built as NSEvents and posted as CGEvents, so we need a native helper.
//
//   usage:
//     lmkey chord <spec> [<spec> ...]   post a chord, e.g. 'cmd+shift+4', 'f13', 'ctrl+opt+space'
//     lmkey media <token> [<token> ...] post a media/volume/brightness key, e.g. 'play_pause'
//     lmkey text [--] <string>          type a UTF-8 string at the cursor
//     lmkey text --stdin                type UTF-8 read from stdin (for text with awkward quoting)
//     lmkey check [--json] [--prompt]   report Accessibility trust; --prompt opens the pane
//     lmkey keys                        list accepted key and modifier names ('key x' / 'mod x')
//     lmkey --version | --help
//
//   options:
//     --delay-ms <n>   gap between posted events for chord/media (default 2)
//     --dry-run        parse the argument and build the events, but post nothing. Validates a
//                      spec (and this tool) without typing into whatever app has focus.
//
//   chord spec: modifiers joined to one key by '+', any order, case-insensitive.
//     modifiers: cmd/command/⌘, ctrl/control/⌃, opt/option/alt/⌥, shift/⇧, fn
//     keys:      letters, digits, f1-f20, arrows, escape, tab, return, space, delete,
//                home/end/pgup/pgdn, punctuation, keypad. `lmkey keys` prints them all.
//     A literal '+' is written as the last character ('cmd++') or as 'plus'.
//
//   exit: 0 ok · 2 bad usage/unknown name · 3 not trusted for Accessibility · 4 post failed
//
//   Accessibility: CGEvent posting is silently ignored — no error, no keystroke — unless the
//   process is trusted for Accessibility. That is by far the most common reason this "does
//   nothing", so every posting subcommand checks AXIsProcessTrusted() first and exits 3 with
//   an explanation rather than pretending to succeed. Trust is granted to the *responsible*
//   process, which for a helper spawned by the daemon means whatever launched the daemon
//   (Terminal.app, iTerm, launchd job, ...) — that is the entry to tick in
//   System Settings > Privacy & Security > Accessibility.
//
//   build:  swiftc -O -o lmkey lmkey.swift
//   (the binary is gitignored; build it from source)

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

let programName = "lmkey"
let programVersion = "0.1.0"

/// Set by --dry-run: build every event as normal, post none of them. Declared here rather
/// than next to the argument parsing so the post helpers below can see it.
var dryRun = false

let exitOK: Int32 = 0
let exitUsage: Int32 = 2
let exitNoAccessibility: Int32 = 3
let exitPostFailed: Int32 = 4

// MARK: - output helpers

func warn(_ message: String) {
    FileHandle.standardError.write(Data(("\(programName): " + message + "\n").utf8))
}

func die(_ message: String, _ code: Int32 = exitUsage) -> Never {
    warn(message)
    exit(code)
}

let usage = """
usage: \(programName) chord <spec> [<spec> ...]
       \(programName) media <token> [<token> ...]
       \(programName) text [--] <string> | text --stdin
       \(programName) check [--json] [--prompt]
       \(programName) keys
       \(programName) --version | --help

options: --delay-ms <n>   gap between posted events (default 2)
         --dry-run        build the events but post nothing (validate a spec safely)

chord spec: 'cmd+shift+4', 'ctrl+opt+space', 'f13'. Modifiers cmd/ctrl/opt/shift/fn
            (also command/control/alt/option and ⌘⌃⌥⇧), any order, case-insensitive.
            A literal '+' is the last character ('cmd++') or the name 'plus'.
media token: vol_up vol_down mute play_pause next_track prev_track bright_up bright_down
             (also eject, fast_forward, rewind, illum_up, illum_down, illum_toggle)
"""

// MARK: - key table
//
// Apple virtual keycodes, from Carbon HIToolbox Events.h (kVK_*). They are positional on a
// US ANSI layout: the *name* here describes the US legend, and on another layout the same
// code produces whatever that layout puts in that position — the same thing that happens
// when you press the physical key. For characters rather than keys, use `text`.

let keyCodes: [String: CGKeyCode] = [
    // letters
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34,
    "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12,
    "r": 15, "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
    // digits
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26,
    "8": 28, "9": 25,
    // function keys
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105,
    "f14": 107, "f15": 113, "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
    // editing / navigation
    "escape": 53, "tab": 48, "return": 36, "space": 49, "delete": 51,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126, "help": 114, "capslock": 57,
    // punctuation (US legends)
    "minus": 27, "equal": 24, "leftbracket": 33, "rightbracket": 30,
    "backslash": 42, "semicolon": 41, "quote": 39, "comma": 43, "period": 47,
    "slash": 44, "grave": 50,
    // keypad
    "kp0": 82, "kp1": 83, "kp2": 84, "kp3": 85, "kp4": 86, "kp5": 87, "kp6": 88,
    "kp7": 89, "kp8": 91, "kp9": 92, "kpdecimal": 65, "kpplus": 69, "kpminus": 78,
    "kpmultiply": 67, "kpdivide": 75, "kpequals": 81, "kpenter": 76, "kpclear": 71,
]

/// Spellings that resolve to a canonical name in `keyCodes`.
let keyAliases: [String: String] = [
    "esc": "escape",
    "enter": "return", "ret": "return", "cr": "return",
    "spc": "space", "spacebar": "space",
    "backspace": "delete", "bksp": "delete", "bs": "delete",
    "fwddelete": "forwarddelete", "forward_delete": "forwarddelete", "fdel": "forwarddelete",
    "pgup": "pageup", "page_up": "pageup", "pgdn": "pagedown", "pagedn": "pagedown",
    "page_down": "pagedown",
    "uparrow": "up", "arrowup": "up", "downarrow": "down", "arrowdown": "down",
    "leftarrow": "left", "arrowleft": "left", "rightarrow": "right", "arrowright": "right",
    "caps": "capslock", "caps_lock": "capslock",
    "-": "minus", "dash": "minus", "hyphen": "minus",
    "=": "equal", "equals": "equal",
    "[": "leftbracket", "lbracket": "leftbracket", "left_bracket": "leftbracket",
    "]": "rightbracket", "rbracket": "rightbracket", "right_bracket": "rightbracket",
    "\\": "backslash",
    ";": "semicolon", "'": "quote", "apostrophe": "quote",
    ",": "comma", ".": "period", "/": "slash",
    "`": "grave", "backtick": "grave", "backquote": "grave", "tilde": "grave",
    "num_enter": "kpenter", "clear": "kpclear",
]

/// Key names that are the *shifted* legend of their key, so naming them implies shift.
/// Deliberately tiny: '+' has to be nameable because it is also the chord separator.
/// For any other shifted character, `text` is the correct tool — it doesn't depend on layout.
let shiftedKeyNames: [String: String] = ["+": "equal", "plus": "equal"]

let modifierFlags: [String: CGEventFlags] = [
    "cmd": .maskCommand,
    "ctrl": .maskControl,
    "opt": .maskAlternate,
    "shift": .maskShift,
    "fn": .maskSecondaryFn,
]

let modifierAliases: [String: String] = [
    "command": "cmd", "⌘": "cmd", "meta": "cmd", "super": "cmd", "win": "cmd",
    "control": "ctrl", "ctl": "ctrl", "⌃": "ctrl",
    "option": "opt", "alt": "opt", "⌥": "opt",
    "⇧": "shift", "shft": "shift",
    "function": "fn",
]

/// Keycodes of the modifiers we press for real (fn has no reliable key event — flag only).
let modifierKeyCodes: [String: CGKeyCode] = ["ctrl": 59, "opt": 58, "shift": 56, "cmd": 55]

/// Order modifiers are pressed in: Apple's display order, ⌃⌥⇧⌘.
let modifierOrder = ["ctrl", "opt", "shift", "cmd"]

// MARK: - media table
//
// NX_KEYTYPE_* from IOKit's ev_keymap.h. These are *not* virtual keycodes: they ride in an
// NSSystemDefined event of subtype 8 (NX_SUBTYPE_AUX_CONTROL_BUTTONS), with the key code in
// the high half of data1 and 0xA/0xB (down/up) in the low half.

let nxSoundUp: Int32 = 0
let nxSoundDown: Int32 = 1
let nxBrightnessUp: Int32 = 2
let nxBrightnessDown: Int32 = 3
let nxMute: Int32 = 7
let nxEject: Int32 = 14
let nxPlay: Int32 = 16
let nxNext: Int32 = 17
let nxPrevious: Int32 = 18
let nxFast: Int32 = 19
let nxRewind: Int32 = 20
let nxIllumUp: Int32 = 21
let nxIllumDown: Int32 = 22
let nxIllumToggle: Int32 = 23

/// Canonical tokens match the `action` enum in host/config/schema.json.
let mediaCodes: [String: Int32] = [
    "vol_up": nxSoundUp,
    "vol_down": nxSoundDown,
    "mute": nxMute,
    "play_pause": nxPlay,
    "next_track": nxNext,
    "prev_track": nxPrevious,
    "bright_up": nxBrightnessUp,
    "bright_down": nxBrightnessDown,
    // extras the same mechanism gives us for free
    "eject": nxEject,
    "fast_forward": nxFast,
    "rewind": nxRewind,
    "illum_up": nxIllumUp,
    "illum_down": nxIllumDown,
    "illum_toggle": nxIllumToggle,
]

let mediaAliases: [String: String] = [
    "volup": "vol_up", "volume_up": "vol_up", "volumeup": "vol_up",
    "voldown": "vol_down", "volume_down": "vol_down", "volumedown": "vol_down",
    "vol_mute": "mute", "volume_mute": "mute",
    "play": "play_pause", "pause": "play_pause", "playpause": "play_pause",
    "next": "next_track", "nexttrack": "next_track",
    "prev": "prev_track", "previous": "prev_track", "prevtrack": "prev_track",
    "previous_track": "prev_track",
    "brightup": "bright_up", "brightness_up": "bright_up", "brightnessup": "bright_up",
    "brightdown": "bright_down", "brightness_down": "bright_down",
    "brightnessdown": "bright_down",
    "ff": "fast_forward", "rew": "rewind",
]

// MARK: - Accessibility

func accessibilityGranted(prompt: Bool = false) -> Bool {
    if !prompt { return AXIsProcessTrusted() }
    let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue()
    return AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
}

let accessibilityHelp = """
not trusted for Accessibility, so synthesised keys would be silently discarded.
       Grant it in System Settings > Privacy & Security > Accessibility to the app that
       launched this process (Terminal, iTerm, or whatever runs the LibreMicro daemon) —
       macOS attributes the permission to that responsible process, not to \(programName)
       itself. Re-launch it afterwards; the trust state is read at process start.
"""

func requireAccessibility() {
    if dryRun { return }        // nothing is posted, so trust is irrelevant
    if !accessibilityGranted() {
        die(accessibilityHelp, exitNoAccessibility)
    }
}

// MARK: - event source

/// One shared source for every event we post, so a whole chord looks like it came from one
/// keyboard. `.hidSystemState` is the state real hardware feeds, which is what apps watching
/// modifier state expect; nil is an acceptable fallback (the flags travel on the event).
let eventSource = CGEventSource(stateID: .hidSystemState)

// MARK: - chord

struct Chord {
    var key: CGKeyCode
    var mods: Set<String>
    var flags: CGEventFlags {
        var f: CGEventFlags = []
        for m in mods { f.insert(modifierFlags[m] ?? []) }
        return f
    }
}

func canonicalKeyName(_ raw: String) -> (name: String, impliesShift: Bool)? {
    if let shifted = shiftedKeyNames[raw] { return (shifted, true) }
    if keyCodes[raw] != nil { return (raw, false) }
    if let alias = keyAliases[raw], keyCodes[alias] != nil { return (alias, false) }
    return nil
}

func canonicalModifierName(_ raw: String) -> String? {
    if modifierFlags[raw] != nil { return raw }
    return modifierAliases[raw]
}

/// Split a chord spec into components. '+' separates, except as the final character where it
/// is the key itself: "cmd++" is command-plus, "+" is bare plus.
func chordComponents(_ spec: String) throws -> [String] {
    if spec.isEmpty { throw LMError.bad("empty chord") }
    if spec == "+" { return ["+"] }
    if spec.hasSuffix("++") {
        // The last '+' is the key, the one before it the separator: "cmd++" is command-plus.
        let head = String(spec.dropLast())
        let mods = head.split(separator: "+", omittingEmptySubsequences: true).map(String.init)
        if mods.isEmpty { throw LMError.bad("chord '\(spec)' has no key") }
        return mods + ["+"]
    }
    if spec.hasSuffix("+") {
        throw LMError.bad("chord '\(spec)' has no key after the last '+' "
                          + "(for a literal plus write '\(spec)+' or '\(spec)plus')")
    }
    let parts = spec.split(separator: "+", omittingEmptySubsequences: false).map(String.init)
    if parts.contains(where: { $0.isEmpty }) {
        throw LMError.bad("chord '\(spec)' has an empty component "
                          + "(write a literal plus last, as in 'cmd++', or use 'plus')")
    }
    return parts
}

enum LMError: Error {
    case bad(String)
}

func parseChord(_ spec: String) throws -> Chord {
    let comps = try chordComponents(spec.trimmingCharacters(in: .whitespaces).lowercased())
    guard let last = comps.last else { throw LMError.bad("empty chord") }

    var mods = Set<String>()
    for raw in comps.dropLast() {
        guard let m = canonicalModifierName(raw) else {
            // A key name in a modifier slot is the likely mistake, so say which one.
            if canonicalKeyName(raw) != nil {
                throw LMError.bad("'\(raw)' in '\(spec)' is a key, not a modifier — "
                                  + "a chord has exactly one key, written last")
            }
            throw LMError.bad("unknown modifier '\(raw)' in '\(spec)' "
                              + "(cmd, ctrl, opt, shift, fn)")
        }
        mods.insert(m)
    }

    guard let resolved = canonicalKeyName(last) else {
        if canonicalModifierName(last) != nil {
            throw LMError.bad("'\(last)' in '\(spec)' is a modifier — a chord needs a key too")
        }
        throw LMError.bad("unknown key '\(last)' in '\(spec)' (see `\(programName) keys`)")
    }
    if resolved.impliesShift { mods.insert("shift") }
    guard let code = keyCodes[resolved.name] else {
        throw LMError.bad("unknown key '\(last)' in '\(spec)'")
    }
    return Chord(key: code, mods: mods)
}

func post(_ code: CGKeyCode, down: Bool, flags: CGEventFlags) -> Bool {
    guard let ev = CGEvent(keyboardEventSource: eventSource, virtualKey: code, keyDown: down)
    else { return false }
    ev.flags = flags
    if !dryRun { ev.post(tap: .cghidEventTap) }
    return true
}

/// Press the modifiers for real, then the key, then release everything in reverse.
///
/// Setting the flags on the key event alone is enough for most apps, but not for anything
/// that tracks modifier state itself (Electron apps, terminals in some modes, games). Real
/// flagsChanged events cost a few extra microseconds and behave the same everywhere.
func sendChord(_ chord: Chord, delayUS: UInt32) -> Bool {
    let base: CGEventFlags = chord.mods.contains("fn") ? .maskSecondaryFn : []
    let all = chord.flags
    var accumulated = base
    var pressed: [String] = []
    var ok = true

    for name in modifierOrder where chord.mods.contains(name) {
        accumulated.insert(modifierFlags[name] ?? [])
        guard let code = modifierKeyCodes[name] else { continue }
        ok = post(code, down: true, flags: accumulated) && ok
        pressed.append(name)
        usleep(delayUS)
    }

    ok = post(chord.key, down: true, flags: all) && ok
    usleep(delayUS)
    ok = post(chord.key, down: false, flags: all) && ok

    for name in pressed.reversed() {
        accumulated.remove(modifierFlags[name] ?? [])
        usleep(delayUS)
        if let code = modifierKeyCodes[name] {
            ok = post(code, down: false, flags: accumulated) && ok
        }
    }
    return ok
}

// MARK: - media

func postAux(_ code: Int32, down: Bool, extraFlags: CGEventFlags = []) -> Bool {
    let state: Int32 = down ? 0xA : 0xB
    let data1 = Int((code << 16) | (state << 8))
    // 0xA00 is the standard "aux control" marker the system expects in an NX event.
    var raw: UInt = 0xA00
    // macOS reads the modifier state to decide the volume STEP SIZE: shift+option gives
    // quarter increments. The flags have to be on the NX event itself, so they get folded
    // into both the NSEvent modifier mask and the resulting CGEvent's flags.
    if extraFlags.contains(.maskShift)      { raw |= UInt(NSEvent.ModifierFlags.shift.rawValue) }
    if extraFlags.contains(.maskAlternate)  { raw |= UInt(NSEvent.ModifierFlags.option.rawValue) }
    if extraFlags.contains(.maskControl)    { raw |= UInt(NSEvent.ModifierFlags.control.rawValue) }
    if extraFlags.contains(.maskCommand)    { raw |= UInt(NSEvent.ModifierFlags.command.rawValue) }
    guard let ev = NSEvent.otherEvent(with: .systemDefined,
                                      location: .zero,
                                      modifierFlags: NSEvent.ModifierFlags(rawValue: raw),
                                      timestamp: 0,
                                      windowNumber: 0,
                                      context: nil,
                                      subtype: 8,
                                      data1: data1,
                                      data2: -1),
          let cg = ev.cgEvent
    else { return false }
    if !extraFlags.isEmpty { cg.flags = cg.flags.union(extraFlags) }
    if !dryRun { cg.post(tap: .cghidEventTap) }
    return true
}

func sendMedia(_ token: String, delayUS: UInt32, fine: Bool = false) throws -> Bool {
    var t = token.trimmingCharacters(in: .whitespaces).lowercased()
    // `vol_up:fine` / `vol_down:fine` — quarter-step volume, which is what a rotary dial
    // wants. Holding shift+option while pressing a volume key is a documented macOS
    // behaviour; synthesising the same modifier state gets the same quarter steps AND keeps
    // the on-screen volume overlay, which setting the level via AppleScript does not.
    var wantFine = fine
    if t.hasSuffix(":fine") { wantFine = true; t = String(t.dropLast(5)) }
    let canonical = mediaCodes[t] != nil ? t : mediaAliases[t]
    guard let name = canonical, let code = mediaCodes[name] else {
        throw LMError.bad("unknown media action '\(token)' "
                          + "(vol_up, vol_down, mute, play_pause, next_track, prev_track, "
                          + "bright_up, bright_down; add ':fine' to a volume token)")
    }
    let flags: CGEventFlags = wantFine ? [.maskShift, .maskAlternate] : []
    var ok = postAux(code, down: true, extraFlags: flags)
    usleep(delayUS)
    ok = postAux(code, down: false, extraFlags: flags) && ok
    return ok
}

// MARK: - text

/// Post `text` as Unicode, which needs no keycode and so works for any character —
/// accented letters, CJK, emoji — regardless of the active keyboard layout.
///
/// Two things are not just "set the string and post":
///  * Newlines and tabs must be real Return/Tab keypresses. A U+000A in a keyboard event's
///    unicode payload is ignored by most text views, so text with line breaks would arrive
///    as one run-on line.
///  * The string is sent in small chunks. Long payloads on a single event get truncated, and
///    a chunk boundary must never split a grapheme (an emoji is 2+ UTF-16 units, a flag or
///    ZWJ sequence many more) or the receiver sees replacement characters.
func sendText(_ text: String, delayUS: UInt32) -> Bool {
    let maxUnits = 16
    var ok = true
    var chunk: [UniChar] = []

    func flush() {
        if chunk.isEmpty { return }
        ok = postUnicode(chunk) && ok
        chunk.removeAll(keepingCapacity: true)
        usleep(delayUS)
    }

    for ch in text {
        switch ch {
        case "\n", "\r\n", "\r":
            flush()
            ok = post(36, down: true, flags: []) && ok      // return
            _ = post(36, down: false, flags: [])
            usleep(delayUS)
        case "\t":
            flush()
            ok = post(48, down: true, flags: []) && ok      // tab
            _ = post(48, down: false, flags: [])
            usleep(delayUS)
        default:
            let units = Array(String(ch).utf16)
            if chunk.count + units.count > maxUnits { flush() }
            chunk.append(contentsOf: units)
        }
    }
    flush()
    return ok
}

func postUnicode(_ units: [UniChar]) -> Bool {
    guard let down = CGEvent(keyboardEventSource: eventSource, virtualKey: 0, keyDown: true),
          let up = CGEvent(keyboardEventSource: eventSource, virtualKey: 0, keyDown: false)
    else { return false }
    // Physical modifiers held while we type would otherwise turn characters into chords.
    down.flags = []
    up.flags = []
    units.withUnsafeBufferPointer { buf in
        down.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
        up.keyboardSetUnicodeString(stringLength: buf.count, unicodeString: buf.baseAddress)
    }
    if !dryRun {
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }
    return true
}

// MARK: - subcommands

func cmdCheck(_ args: [String]) -> Never {
    var json = false
    var prompt = false
    for a in args {
        switch a {
        case "--json": json = true
        case "--prompt": prompt = true
        default: die("check: unexpected argument '\(a)'")
        }
    }
    let granted = accessibilityGranted(prompt: prompt)
    if json {
        print("{\"tool\":\"\(programName)\",\"version\":\"\(programVersion)\","
              + "\"accessibility\":\(granted ? "true" : "false"),"
              + "\"media\":true,\"keys\":\(keyCodes.count)}")
    } else {
        print("\(programName) \(programVersion)")
        print("accessibility: \(granted ? "granted" : "DENIED")")
        print("key names: \(keyCodes.count) canonical, \(keyAliases.count) aliases")
        print("media actions: \(mediaCodes.count)")
    }
    if !granted && !json { warn(accessibilityHelp) }
    exit(granted ? exitOK : exitNoAccessibility)
}

func cmdKeys() -> Never {
    var lines: [String] = []
    for name in keyCodes.keys { lines.append("key \(name)") }
    for name in keyAliases.keys { lines.append("key \(name)") }
    for name in shiftedKeyNames.keys { lines.append("key \(name)") }
    for name in modifierFlags.keys { lines.append("mod \(name)") }
    for name in modifierAliases.keys { lines.append("mod \(name)") }
    for name in mediaCodes.keys { lines.append("media \(name)") }
    for name in mediaAliases.keys { lines.append("media \(name)") }
    print(lines.sorted().joined(separator: "\n"))
    exit(exitOK)
}

// MARK: - main

var argv = Array(CommandLine.arguments.dropFirst())
var delayUS: UInt32 = 2000

// Global options may appear before or after the subcommand.
var scanned: [String] = []
var i = 0
while i < argv.count {
    let a = argv[i]
    if a == "--delay-ms" {
        guard i + 1 < argv.count, let ms = UInt32(argv[i + 1]), ms <= 1000 else {
            die("--delay-ms needs a number of milliseconds, 0-1000")
        }
        delayUS = ms * 1000
        i += 2
        continue
    }
    if a == "--dry-run" || a == "-n" {
        dryRun = true
        i += 1
        continue
    }
    if a == "--" && !scanned.isEmpty {
        // Everything after `--` is an operand; stop option scanning.
        scanned.append(contentsOf: argv[(i + 1)...])
        break
    }
    scanned.append(a)
    i += 1
}
argv = scanned

guard let subcommand = argv.first else {
    FileHandle.standardError.write(Data((usage + "\n").utf8))
    exit(exitUsage)
}
let operands = Array(argv.dropFirst())

switch subcommand {
case "-h", "--help", "help":
    print(usage)
    exit(exitOK)

case "-V", "--version", "version":
    print("\(programName) \(programVersion)")
    exit(exitOK)

case "check":
    cmdCheck(operands)

case "keys":
    cmdKeys()

case "chord", "key", "shortcut":
    if operands.isEmpty { die("chord: needs a spec, e.g. '\(programName) chord cmd+shift+4'") }
    // Parse everything before posting anything, so a typo in the second chord doesn't
    // leave the first one half-applied.
    var chords: [Chord] = []
    for spec in operands {
        do { chords.append(try parseChord(spec)) }
        catch LMError.bad(let m) { die(m) }
        catch { die("chord: \(error)") }
    }
    requireAccessibility()
    var ok = true
    for (n, chord) in chords.enumerated() {
        if n > 0 { usleep(delayUS) }
        ok = sendChord(chord, delayUS: delayUS) && ok
    }
    if !ok { die("failed to create a key event", exitPostFailed) }
    exit(exitOK)

case "media":
    if operands.isEmpty { die("media: needs an action, e.g. '\(programName) media play_pause'") }
    for token in operands {
        var t = token.trimmingCharacters(in: .whitespaces).lowercased()
        // Strip the ':fine' qualifier before validating the action name.
        if t.hasSuffix(":fine") { t = String(t.dropLast(5)) }
        if mediaCodes[t] == nil && mediaAliases[t] == nil {
            die("unknown media action '\(token)' (vol_up, vol_down, mute, play_pause, "
                + "next_track, prev_track, bright_up, bright_down; add ':fine' to a "
                + "volume token for quarter steps)")
        }
    }
    requireAccessibility()
    var ok = true
    for (n, token) in operands.enumerated() {
        if n > 0 { usleep(delayUS) }
        do { ok = try sendMedia(token, delayUS: delayUS) && ok }
        catch LMError.bad(let m) { die(m) }
        catch { die("media: \(error)") }
    }
    if !ok {
        die("failed to create a system-defined event (is a window server session available?)",
            exitPostFailed)
    }
    exit(exitOK)

case "text", "type":
    var body: String
    if operands.first == "--stdin" {
        if operands.count > 1 { die("text --stdin takes no other argument") }
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard let s = String(data: data, encoding: .utf8) else {
            die("text: stdin is not valid UTF-8")
        }
        body = s
    } else {
        if operands.isEmpty { die("text: needs a string (or --stdin)") }
        if operands.count > 1 {
            die("text: takes one string — quote it, or use --stdin "
                + "(got \(operands.count) arguments)")
        }
        body = operands[0]
    }
    if body.isEmpty { exit(exitOK) }
    requireAccessibility()
    if !sendText(body, delayUS: delayUS) {
        die("failed to create a key event", exitPostFailed)
    }
    exit(exitOK)

default:
    die("unknown subcommand '\(subcommand)'\n\n\(usage)")
}
