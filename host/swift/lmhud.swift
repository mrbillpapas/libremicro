// lmhud — the on-screen cheat sheet: what every control on the pad is bound to, right now.
//
// The pad has 13 unlabelled keycaps. Any layout worth building is a layout you can't remember,
// which is why Work Louder's own app ships this and why it's worth having: a translucent panel
// in the corner of the screen showing the live binding for every key, the encoder, the joystick
// and the touch pad.
//
// **Why a helper binary.** The daemon is Python and the web UI is a browser tab; neither can put
// a borderless, non-activating panel above a full-screen app. That needs AppKit:
//
//   * `.nonactivatingPanel` + `orderFrontRegardless()` — appears without stealing focus, so the
//     app you were working in keeps the keyboard. A HUD that steals focus is worse than none.
//   * `level = .popUpMenu` — above normal windows *and* above full-screen app content.
//   * `collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]` — shows on whatever Space
//     you're on, including over a full-screened window, rather than yanking you to another Space.
//
// **Mouse.** Drag it anywhere by its background (`NSWindow.performDrag`), close it with the ×
// in its top-right corner. Both work without activating this process, so the app you were
// working in never loses the keyboard — that is what `.nonactivatingPanel` buys, and it is why
// clicks are handled here rather than passed through with `ignoresMouseEvents`. The cost of
// taking clicks is real: the panel's own footprint no longer clicks through to what is beneath
// it. That is the trade the × button pays for.
//
// **Where it sits is remembered.** A dragged panel that jumped back to the corner on the next
// mode change would be worse than one you cannot move at all, and the sheet re-renders by
// respawning. So the position is written to `~/.cache/libremicro/hud-position.json` on exit and
// restored on launch — by this process, not the daemon, which therefore needs to know nothing
// about it. A saved position that no longer lands on a connected display is discarded rather
// than honoured, so unplugging a monitor cannot strand the sheet off-screen.
//
// **Protocol.** One JSON document on stdin, read to EOF, then the panel is shown and this process
// stays alive holding it. There is no hide command and no IPC: the daemon shows the sheet by
// spawning this and hides it by terminating it, which means a crashed daemon cannot leave an
// orphaned panel pinned over the user's screen — the process dies with its parent's SIGTERM, and
// `--timeout` is a second belt for the case where nothing sends one.
//
// Sending a fresh document means respawning, which costs ~50 ms and is how a mode change
// re-renders. That is cheap enough to make streaming updates not worth the run-loop complexity.
//
//   usage:
//     lmhud show [--timeout <seconds>] [--corner bottom-left|bottom-right|top-left|top-right]
//                [--reset-position]     ignore the remembered position and use --corner
//     lmhud probe          exit 0 if AppKit can open a window here, 3 if it cannot
//
//   stdin (for `show`):
//     {
//       "title":    "coding",                       // profile name, drawn as the heading
//       "mode":     "media",                        // optional; drawn as a pill next to it
//       "rows":     [[cell, cell, cell, cell], …],  // 4 rows x 4 cells, laid out as the pad is
//       "timeout_s": 0
//     }
//     A cell is one of:
//       {"kind":"key",      "label":"Slack",   "detail":"launch"}
//       {"kind":"encoder",  "label":"Volume",  "detail":"press: mute"}
//       {"kind":"joystick", "label":"Arrows"}
//       {"kind":"touch",    "label":"Play"}
//       {"kind":"empty"}
//     `label` is what the key does; `detail` is the smaller second line and may be omitted.
//     An unbound key is `{"kind":"key"}` with no label, drawn as a dimmed empty tile.
//
//   build: cd host/swift && swiftc -O -o lmhud lmhud.swift

import AppKit
import Foundation

// Geometry, matching the pad's real proportions: a 4x4 grid of square tiles. The numbers are
// the vendor's (52 pt tiles, 2 pt gutters, 214 pt total), because they were arrived at against
// the same physical device and there is no reason to redraw that judgement.
let TILE: CGFloat = 52
let GAP: CGFloat = 2
let PAD: CGFloat = 12
let HEADER: CGFloat = 34
let SCREEN_MARGIN: CGFloat = 16
let CLOSE: CGFloat = 17          // the × button's box, big enough to hit without aiming
let GRID = 4

let EXIT_NO_WINDOW: Int32 = 3

/// How much of the panel has to remain on a connected display for a remembered position to be
/// reused. A sliver poking onto the screen is not something you could grab to move back.
let MIN_ONSCREEN: CGFloat = 0.5

// ---- model

struct Cell: Decodable {
    var kind: String = "empty"
    var label: String?
    var detail: String?
}

struct Sheet: Decodable {
    var title: String?
    var mode: String?
    var rows: [[Cell]] = []
    var timeout_s: Double?
}

enum Corner: String {
    case bottomLeft = "bottom-left", bottomRight = "bottom-right"
    case topLeft = "top-left", topRight = "top-right"
}

// ---- drawing

final class SheetView: NSView {
    let sheet: Sheet
    private var closeHot = false

    init(sheet: Sheet, size: NSSize) {
        self.sheet = sheet
        super.init(frame: NSRect(origin: .zero, size: size))
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    override var isFlipped: Bool { true }   // top-down y, so row 0 draws at the top

    /// The × button, in this view's flipped coordinates. Computed rather than stored so drawing
    /// and hit-testing can never disagree about where the button is.
    private var closeRect: NSRect {
        NSRect(x: bounds.width - PAD - CLOSE, y: PAD - 3, width: CLOSE, height: CLOSE)
    }

    // --- mouse ------------------------------------------------------------------

    override func updateTrackingAreas() {
        for area in trackingAreas { removeTrackingArea(area) }
        // .activeAlways, not .activeInActiveApp: this process is never the active app — the
        // whole point is that it doesn't steal focus — so an active-app-only tracking area
        // would never fire and the × would never light up.
        addTrackingArea(NSTrackingArea(rect: bounds,
                                       options: [.mouseMoved, .mouseEnteredAndExited, .activeAlways],
                                       owner: self, userInfo: nil))
        super.updateTrackingAreas()
    }

    override func mouseMoved(with event: NSEvent) {
        let hot = closeRect.contains(convert(event.locationInWindow, from: nil))
        if hot != closeHot { closeHot = hot; needsDisplay = true }
    }

    override func mouseExited(with event: NSEvent) {
        if closeHot { closeHot = false; needsDisplay = true }
    }

    override func mouseDown(with event: NSEvent) {
        if closeRect.contains(convert(event.locationInWindow, from: nil)) {
            NSApplication.shared.terminate(nil)   // saves the position on the way out
            return
        }
        // performDrag runs its own event loop until the mouse comes up, which is both simpler
        // and smoother than tracking mouseDragged by hand, and it snaps nothing.
        window?.performDrag(with: event)
    }

    override func draw(_ dirty: NSRect) {
        // The whole panel gets one rounded translucent slab. Individual tiles sit on top of it
        // rather than floating free, so the sheet reads as one object against a busy desktop.
        let slab = NSBezierPath(roundedRect: bounds, xRadius: 14, yRadius: 14)
        NSColor(calibratedWhite: 0.08, alpha: 0.82).setFill()
        slab.fill()
        NSColor(calibratedWhite: 1.0, alpha: 0.16).setStroke()
        slab.lineWidth = 1
        slab.stroke()

        drawHeader()
        for (r, row) in sheet.rows.enumerated() {
            for (c, cell) in row.enumerated() where c < GRID {
                draw(cell: cell, atRow: r, col: c)
            }
        }
    }

    private func drawHeader() {
        let y = PAD - 2
        attributed(sheet.title ?? "LibreMicro", size: 13, weight: .semibold, alpha: 0.95)
            .draw(at: NSPoint(x: PAD, y: y))

        drawCloseButton()

        guard let mode = sheet.mode, !mode.isEmpty else { return }
        // The mode pill is the one thing on here that changes what every key does, so it gets
        // to be the brightest element on the sheet. It sits left of the × rather than at the
        // edge, so a long mode name can never grow under the button and swallow the click.
        let text = attributed(mode.uppercased(), size: 9, weight: .semibold, alpha: 1.0)
        let w = min(text.size().width + 14, closeRect.minX - PAD - 60)
        guard w > 20 else { return }
        let pill = NSRect(x: closeRect.minX - 8 - w, y: y + 1, width: w, height: 15)
        let path = NSBezierPath(roundedRect: pill, xRadius: 7.5, yRadius: 7.5)
        NSColor(calibratedRed: 0.88, green: 0.12, blue: 0.35, alpha: 0.92).setFill()
        path.fill()
        text.draw(with: NSRect(x: pill.minX + 7, y: pill.minY + 2, width: w - 14, height: 12),
                  options: [.usesLineFragmentOrigin])
    }

    private func drawCloseButton() {
        let r = closeRect
        let disc = NSBezierPath(ovalIn: r)
        NSColor(calibratedWhite: 1.0, alpha: closeHot ? 0.26 : 0.11).setFill()
        disc.fill()
        NSColor(calibratedWhite: 1.0, alpha: closeHot ? 0.55 : 0.22).setStroke()
        disc.lineWidth = 1
        disc.stroke()

        // Drawn as two strokes rather than a glyph: an "×" in a system font is never quite
        // centred in a 17 pt disc, and this is the one control on the sheet you have to hit.
        let inset: CGFloat = 5.5
        let cross = NSBezierPath()
        cross.move(to: NSPoint(x: r.minX + inset, y: r.minY + inset))
        cross.line(to: NSPoint(x: r.maxX - inset, y: r.maxY - inset))
        cross.move(to: NSPoint(x: r.maxX - inset, y: r.minY + inset))
        cross.line(to: NSPoint(x: r.minX + inset, y: r.maxY - inset))
        NSColor(calibratedWhite: 1.0, alpha: closeHot ? 0.95 : 0.6).setStroke()
        cross.lineWidth = 1.4
        cross.lineCapStyle = .round
        cross.stroke()
    }

    private func tileRect(row: Int, col: Int) -> NSRect {
        NSRect(x: PAD + CGFloat(col) * (TILE + GAP),
               y: PAD + HEADER + CGFloat(row) * (TILE + GAP),
               width: TILE, height: TILE)
    }

    private func draw(cell: Cell, atRow row: Int, col: Int) {
        if cell.kind == "empty" { return }
        let rect = tileRect(row: row, col: col)
        let bound = !(cell.label ?? "").isEmpty

        // Round controls for the round hardware (encoder, joystick, touch pad), rounded squares
        // for keycaps. Shape carries the control type so the labels don't have to spend
        // characters saying "encoder".
        let round = (cell.kind != "key")
        let inset = round ? rect.insetBy(dx: 2, dy: 2) : rect
        let radius: CGFloat = round ? inset.width / 2 : 8
        let path = NSBezierPath(roundedRect: inset, xRadius: radius, yRadius: radius)

        NSColor(calibratedWhite: 1.0, alpha: bound ? 0.13 : 0.05).setFill()
        path.fill()
        NSColor(calibratedWhite: 1.0, alpha: bound ? 0.30 : 0.12).setStroke()
        path.lineWidth = 1
        path.stroke()

        guard bound else { return }
        drawLabel(cell.label!, detail: cell.detail, in: inset)
    }

    private func drawLabel(_ label: String, detail: String?, in rect: NSRect) {
        // Two lines at most for the label and one for the detail, wrapped and centred. Long
        // labels shrink one step rather than being clipped, because a truncated app name is
        // usually still unreadable and the whole point is recognition at a glance.
        let size: CGFloat = label.count > 12 ? 9 : 10.5
        let text = attributed(label, size: size, weight: .medium, alpha: 0.96, centered: true)
        let hasDetail = !(detail ?? "").isEmpty
        let avail = NSRect(x: rect.minX + 3, y: rect.minY + 3,
                           width: rect.width - 6, height: rect.height - (hasDetail ? 15 : 6))
        let h = min(text.boundingRect(with: NSSize(width: avail.width, height: 999),
                                      options: [.usesLineFragmentOrigin]).height, avail.height)
        text.draw(with: NSRect(x: avail.minX, y: avail.midY - h / 2, width: avail.width, height: h),
                  options: [.usesLineFragmentOrigin])

        guard hasDetail else { return }
        let sub = attributed(detail!, size: 8, weight: .regular, alpha: 0.55, centered: true)
        sub.draw(with: NSRect(x: rect.minX + 2, y: rect.maxY - 14, width: rect.width - 4, height: 11),
                 options: [.usesLineFragmentOrigin])
    }

    private func attributed(_ s: String, size: CGFloat, weight: NSFont.Weight,
                            alpha: CGFloat, centered: Bool = false) -> NSAttributedString {
        let para = NSMutableParagraphStyle()
        para.alignment = centered ? .center : .left
        para.lineBreakMode = .byWordWrapping
        return NSAttributedString(string: s, attributes: [
            .font: NSFont.systemFont(ofSize: size, weight: weight),
            .foregroundColor: NSColor(calibratedWhite: 1.0, alpha: alpha),
            .paragraphStyle: para,
        ])
    }
}

// ---- remembering where it was put

/// State, not config: it changes every time the panel is dragged, so it does not belong next to
/// the hand-edited config in ~/.config/libremicro.
func positionFile() -> URL {
    FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".cache/libremicro/hud-position.json")
}

func savedPosition() -> NSPoint? {
    guard let data = try? Data(contentsOf: positionFile()),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Double],
          let x = obj["x"], let y = obj["y"] else { return nil }
    return NSPoint(x: x, y: y)
}

func savePosition(_ p: NSPoint) {
    let url = positionFile()
    try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                             withIntermediateDirectories: true)
    if let data = try? JSONSerialization.data(withJSONObject: ["x": p.x, "y": p.y]) {
        try? data.write(to: url, options: .atomic)
    }
}

/// Whether a frame is usably on a connected display. Guards the case where the panel was last
/// closed on a monitor that is no longer attached — restoring that position blind would put the
/// sheet somewhere the user cannot see or grab.
func onScreenEnough(_ frame: NSRect) -> Bool {
    let need = frame.width * frame.height * MIN_ONSCREEN
    for screen in NSScreen.screens {
        let overlap = screen.visibleFrame.intersection(frame)
        if !overlap.isNull, overlap.width * overlap.height >= need { return true }
    }
    return false
}

// ---- cli

func die(_ msg: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else {
    die("usage: lmhud show [--timeout <s>] [--corner bottom-left|bottom-right|top-left|top-right]")
}

if command == "probe" {
    // A window server has to exist. Under ssh or a launchd job in the wrong session it does
    // not, and the daemon wants to say so once rather than spawn a doomed process per keypress.
    exit(NSApplication.shared.setActivationPolicy(.accessory) ? 0 : EXIT_NO_WINDOW)
}
guard command == "show" else { die("unknown command: \(command)") }

var corner = Corner.bottomLeft
var timeoutArg: Double? = nil
var resetPosition = false
var i = 1
while i < args.count {
    switch args[i] {
    case "--reset-position":
        resetPosition = true
    case "--corner":
        i += 1
        guard i < args.count, let c = Corner(rawValue: args[i]) else { die("bad --corner") }
        corner = c
    case "--timeout":
        i += 1
        guard i < args.count, let t = Double(args[i]) else { die("bad --timeout") }
        timeoutArg = t
    default:
        die("unknown option: \(args[i])")
    }
    i += 1
}

let input = FileHandle.standardInput.readDataToEndOfFile()
guard !input.isEmpty else { die("no JSON on stdin") }
let sheet: Sheet
do {
    sheet = try JSONDecoder().decode(Sheet.self, from: input)
} catch {
    die("could not parse the sheet: \(error)")
}

let rows = max(sheet.rows.count, 1)
let size = NSSize(width: PAD * 2 + CGFloat(GRID) * TILE + CGFloat(GRID - 1) * GAP,
                  height: PAD * 2 + HEADER + CGFloat(rows) * TILE + CGFloat(rows - 1) * GAP)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)     // no Dock icon, no menu bar: this is a HUD, not an app

// Place on the display holding the pointer, which is the one the user is looking at — not
// necessarily the main display.
let mouse = NSEvent.mouseLocation
let screen = NSScreen.screens.first { $0.frame.contains(mouse) } ?? NSScreen.main
guard let visible = screen?.visibleFrame else { die("no screen", code: EXIT_NO_WINDOW) }

let cornerX: CGFloat = (corner == .bottomRight || corner == .topRight)
    ? visible.maxX - size.width - SCREEN_MARGIN
    : visible.minX + SCREEN_MARGIN
let cornerY: CGFloat = (corner == .topLeft || corner == .topRight)
    ? visible.maxY - size.height - SCREEN_MARGIN
    : visible.minY + SCREEN_MARGIN

var origin = NSPoint(x: cornerX, y: cornerY)
if !resetPosition, let saved = savedPosition(),
   onScreenEnough(NSRect(origin: saved, size: size)) {
    origin = saved
}

let panel = NSPanel(contentRect: NSRect(origin: origin, size: size),
                    styleMask: [.borderless, .nonactivatingPanel],
                    backing: .buffered, defer: false)
panel.isOpaque = false
panel.backgroundColor = .clear
panel.hasShadow = true
panel.level = .popUpMenu
panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
panel.isReleasedWhenClosed = false
panel.isMovableByWindowBackground = true   // belt to performDrag's braces
panel.contentView = SheetView(sheet: sheet, size: size)
panel.orderFrontRegardless()

// Covers every way this exits — the × button, SIGTERM from the daemon, and --timeout — because
// all three route through NSApplication.terminate.
NotificationCenter.default.addObserver(forName: NSApplication.willTerminateNotification,
                                       object: nil, queue: .main) { _ in
    savePosition(panel.frame.origin)
}

// SIGTERM is the normal way this ends — the daemon hiding the sheet. Without a handler the
// default action would still kill us, but going through NSApplication lets AppKit tear the
// window down rather than leaving the window server to reap it.
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
term.setEventHandler { app.terminate(nil) }
term.resume()
signal(SIGTERM, SIG_IGN)

if let t = timeoutArg ?? sheet.timeout_s, t > 0 {
    DispatchQueue.main.asyncAfter(deadline: .now() + t) { app.terminate(nil) }
}

app.run()
