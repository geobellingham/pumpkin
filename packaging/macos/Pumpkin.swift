// Pumpkin's own window on a Mac: WebKit around the Pumpkin page, like the GTK window on Linux.
// Starts Pumpkin in the background if needed, signs in, and shows it. Closing the window leaves Pumpkin
// running (tasks and phone access keep working); quit Pumpkin itself from Settings or `pumpkin stop`.
//
// Build: swiftc -O -target arm64-apple-macos12 Pumpkin.swift -o Pumpkin   (CI builds arm64 + x86_64)
import AppKit
import WebKit

let appName = "Pumpkin"

/// The `pumpkin` command, written into Info.plist by the installer.
func pumpkinCommand() -> String {
    if let p = Bundle.main.object(forInfoDictionaryKey: "PumpkinCommand") as? String, !p.isEmpty { return p }
    return NSHomeDirectory() + "/.local/bin/pumpkin"
}

/// Runs `pumpkin <args>` and returns what it printed.
func run(_ args: [String]) -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: pumpkinCommand())
    p.arguments = args
    let out = Pipe()
    p.standardOutput = out
    p.standardError = out
    do { try p.run() } catch { return (-1, "\(error.localizedDescription)") }
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

func isLocal(_ url: URL?) -> Bool {
    guard let host = url?.host else { return true }      // about:blank, data:
    return host == "127.0.0.1" || host == "localhost" || host == "::1"
}

final class Delegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate,
                      WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var downloads: [ObjectIdentifier: URL] = [:]

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let conf = WKWebViewConfiguration()
        conf.websiteDataStore = .default()                 // keeps the sign-in cookie between launches
        conf.mediaTypesRequiringUserActionForPlayback = []  // spoken replies in voice chat
        conf.preferences.setValue(true, forKey: "developerExtrasEnabled")
        web = WKWebView(frame: .zero, configuration: conf)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = true
        web.loadHTMLString(Self.waitingPage, baseURL: nil)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.contentView = web
        window.minSize = NSSize(width: 420, height: 500)
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("PumpkinWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        DispatchQueue.global(qos: .userInitiated).async {
            let (upCode, upOut) = run(["up"])
            let (code, out) = run(["url"])
            let link = out.split(separator: "\n").last.map(String.init)?.trimmingCharacters(in: .whitespaces) ?? ""
            DispatchQueue.main.async {
                if upCode == 0, code == 0, let url = URL(string: link), link.hasPrefix("http") {
                    self.web.load(URLRequest(url: url))
                } else {
                    self.web.loadHTMLString(Self.errorPage(upOut + "\n" + out), baseURL: nil)
                }
            }
        }
    }

    // closing the window quits this app; the Pumpkin server keeps running in the background
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    // ---- links to other sites open in the normal browser
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload {                // <a download>, e.g. on the Files page
            decisionHandler(.download)
            return
        }
        if let url = action.request.url, !isLocal(url), url.scheme == "http" || url.scheme == "https" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // ---- downloads go to ~/Downloads and are shown in Finder
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let name = (suggestedFilename as NSString).lastPathComponent
        let stem = (name as NSString).deletingPathExtension, ext = (name as NSString).pathExtension
        var url = dir.appendingPathComponent(name)
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {           // never overwrite: file (2).md
            url = dir.appendingPathComponent(ext.isEmpty ? "\(stem) (\(n))" : "\(stem) (\(n)).\(ext)")
            n += 1
        }
        downloads[ObjectIdentifier(download)] = url
        completionHandler(url)
    }

    func downloadDidFinish(_ download: WKDownload) {
        if let url = downloads.removeValue(forKey: ObjectIdentifier(download)) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloads.removeValue(forKey: ObjectIdentifier(download))
        let a = NSAlert()
        a.messageText = "The download did not finish."
        a.informativeText = error.localizedDescription
        a.beginSheetModal(for: window) { _ in }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if isLocal(url) { webView.load(URLRequest(url: url)) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    // ---- the page's alert() and confirm() (used before deleting things)
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert()
        a.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    // ---- attaching files in the chat
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.beginSheetModal(for: window) { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    // ---- the microphone for voice chat, only for Pumpkin's own page
    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(["127.0.0.1", "localhost"].contains(origin.host) ? .grant : .deny)
    }

    // ---- menus: without an Edit menu, copy and paste do not work in a web view
    func buildMenu() {
        let main = NSMenu()
        func item(_ title: String, _ action: Selector?, _ key: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
            i.keyEquivalentModifierMask = mods
            return i
        }
        let app = NSMenu()
        app.addItem(item("About \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""))
        app.addItem(.separator())
        app.addItem(item("Hide \(appName)", #selector(NSApplication.hide(_:)), "h"))
        app.addItem(item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]))
        app.addItem(item("Show All", #selector(NSApplication.unhideAllApplications(_:)), ""))
        app.addItem(.separator())
        app.addItem(item("Quit \(appName)", #selector(NSApplication.terminate(_:)), "q"))
        let edit = NSMenu(title: "Edit")
        edit.addItem(item("Undo", Selector(("undo:")), "z"))
        edit.addItem(item("Redo", Selector(("redo:")), "z", [.command, .shift]))
        edit.addItem(.separator())
        edit.addItem(item("Cut", #selector(NSText.cut(_:)), "x"))
        edit.addItem(item("Copy", #selector(NSText.copy(_:)), "c"))
        edit.addItem(item("Paste", #selector(NSText.paste(_:)), "v"))
        edit.addItem(item("Select All", #selector(NSText.selectAll(_:)), "a"))
        let view = NSMenu(title: "View")
        view.addItem(item("Reload", #selector(reload), "r"))
        view.addItem(item("Actual Size", #selector(zoomReset), "0"))
        view.addItem(item("Zoom In", #selector(zoomIn), "+"))
        view.addItem(item("Zoom Out", #selector(zoomOut), "-"))
        view.addItem(.separator())
        view.addItem(item("Open in Browser", #selector(openInBrowser), "b", [.command, .shift]))
        let win = NSMenu(title: "Window")
        win.addItem(item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"))
        win.addItem(item("Zoom", #selector(NSWindow.performZoom(_:)), ""))
        for (title, menu) in [(appName, app), ("Edit", edit), ("View", view), ("Window", win)] {
            let top = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            top.submenu = menu
            main.addItem(top)
        }
        NSApp.mainMenu = main
        NSApp.windowsMenu = win
    }

    @objc func reload() { web.reload() }
    @objc func zoomIn() { web.pageZoom = min(3, web.pageZoom + 0.1) }
    @objc func zoomOut() { web.pageZoom = max(0.5, web.pageZoom - 0.1) }
    @objc func zoomReset() { web.pageZoom = 1 }
    @objc func openInBrowser() {
        DispatchQueue.global().async {
            let (_, out) = run(["url"])
            if let s = out.split(separator: "\n").last, let url = URL(string: String(s)) {
                DispatchQueue.main.async { NSWorkspace.shared.open(url) }
            }
        }
    }

    static let style = "body{font:15px -apple-system,system-ui;color:#555;display:flex;align-items:center;justify-content:center;height:90vh;margin:0}@media(prefers-color-scheme:dark){body{background:#1c1c1c;color:#aaa}}pre{white-space:pre-wrap;font-size:12px;max-width:640px}"
    static let waitingPage = "<html><head><style>\(style)</style></head><body><p>Starting Pumpkin…</p></body></html>"
    static func errorPage(_ details: String) -> String {
        let safe = details.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
        return "<html><head><style>\(style)</style></head><body><div><p>Pumpkin did not start. Try running <code>pumpkin doctor</code> in Terminal.</p><pre>\(safe)</pre></div></body></html>"
    }
}

if CommandLine.arguments.contains("--check") {          // used by CI: the binary loads and can find pumpkin
    print("\(appName) window, pumpkin command: \(pumpkinCommand())")
    exit(0)
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
