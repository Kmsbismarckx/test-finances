// THROWAWAY app: native consumer of the delivered XCFramework/Swift Package.
import SwiftUI
import Security
import LocalAuthentication
import FinanceProbe

@MainActor final class ProbeModel: ObservableObject {
    @Published var text = "Прототип · только вымышленные данные"
    @Published var lockApp = false
    var engine: Engine?
    var activeControl: Control?
    private var started = false
    private let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]

    func argument(_ key: String, fallback: String) -> String {
        let a = ProcessInfo.processInfo.arguments
        guard let i = a.firstIndex(of: key), i + 1 < a.count else { return fallback }
        return a[i + 1]
    }
    func key() throws -> Data {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "dev.finance.throwawayprobe", kSecAttrAccount as String: "probe-key"]
        var read = query
        read[kSecReturnData as String] = true
        var value: CFTypeRef?
        let status = SecItemCopyMatching(read as CFDictionary, &value)
        if status == errSecSuccess, let data = value as? Data { return data }
        guard status == errSecItemNotFound else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        var data = Data(count: 32)
        let randomStatus = data.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
        guard randomStatus == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(randomStatus)) }
        var item = query
        item[kSecValueData as String] = data
        #if targetEnvironment(simulator)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        #else
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly
        #endif
        let addStatus = SecItemAdd(item as CFDictionary, nil)
        guard addStatus == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(addStatus)) }
        return data
    }
    func open() throws -> Engine {
        if let engine { return engine }
        let file = argument("--database", fallback: "manual")
        guard file.range(of: "^[a-zA-Z0-9_-]+$", options: .regularExpression) != nil else {
            throw NSError(domain: "probe.badPath", code: 1)
        }
        let path = folder.appendingPathComponent("PROTOTYPE-\(file).db").path
        let opened = try Engine(path: path, key: key(), wal: true)
        engine = opened
        return opened
    }
    func close(_ reason: String) {
        activeControl?.cancel()
        activeControl?.release()
        do {
            let cleared = try engine?.close()
            engine = nil
            text += "\nЗакрытие: \(reason); контролируемый буфер: \(cleared == true ? "очищен" : "ядро уже закрыто")"
            appendLifecycle(reason, cleared: cleared == true)
        } catch { text += "\nОшибка закрытия: \(error)" }
    }
    func appendLifecycle(_ reason: String, cleared: Bool) {
        let line: [String: Any] = ["event": reason, "controlledKeyBufferCleared": cleared,
            "time": ISO8601DateFormatter().string(from: Date()),
            "protectedDataAvailable": UIApplication.shared.isProtectedDataAvailable]
        if let bytes = try? JSONSerialization.data(withJSONObject: line),
           let content = String(data: bytes, encoding: .utf8) {
            let url = folder.appendingPathComponent("lifecycle.jsonl")
            let old = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            try? (old + content + "\n").write(to: url, atomically: true, encoding: .utf8)
        }
    }
    func unlock() async {
        do {
            if lockApp {
                _ = try await LAContext().evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Открыть тестовое хранилище")
            }
            _ = try open()
            text += "\nЯдро открыто после разблокировки."
        } catch { text += "\nОткрытие недоступно: \(error)" }
    }
    func authenticate() async {
        do {
            let ok = try await LAContext().evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Проверить подтверждение действия в прототипе")
            text += "\nСистемное подтверждение: \(ok)"
        } catch { text += "\nСистемное подтверждение недоступно: \(error)" }
    }
    func initialRun() async {
        guard !started else { return }
        started = true
        await run(argument("--scenario", fallback: "read"))
    }
    func run(_ mode: String) async {
        var result: [String: Any] = ["scenario": mode, "run": argument("--run", fallback: "manual"),
            "os": UIDevice.current.systemVersion, "model": UIDevice.current.model]
        #if targetEnvironment(simulator)
        result["environment"] = "simulator"
        result["keyAccessibility"] = "WhenUnlockedThisDeviceOnly (simulator fixture)"
        #else
        result["environment"] = "device"
        result["keyAccessibility"] = "WhenPasscodeSetThisDeviceOnly"
        #endif
        do {
            let core = try open()
            if mode == "seed" {
                result["outcome"] = try await core.apply(id: "ios-expense", amount: 700, transfer: false, control: Control(hold: 0), crash: 0)
            } else if mode == "transfer" {
                result["outcome"] = try await core.apply(id: "ios-transfer", amount: 9000, transfer: true, control: Control(hold: 0), crash: 0)
            } else if mode == "crash-before" || mode == "crash-after" {
                _ = try await core.apply(id: "ios-crash", amount: 9000, transfer: true, control: Control(hold: 0), crash: mode == "crash-before" ? 1 : 2)
            } else if mode == "cancel-before" || mode == "cancel-after" {
                let gate: UInt8 = mode == "cancel-before" ? 1 : 3
                let control = Control(hold: gate)
                activeControl = control
                let work = Task.detached {
                    try await core.apply(id: "ios-cancel", amount: 9000, transfer: true, control: control, crash: 0)
                }
                for _ in 0..<2000 {
                    if control.stage() == gate { break }
                    try await Task.sleep(nanoseconds: 2_000_000)
                }
                control.cancel()
                control.release()
                do { result["outcome"] = try await work.value }
                catch ProbeError.Cancelled { result["outcome"] = "cancelled_before_commit" }
                activeControl = nil
                result["receipt"] = try core.outcome(id: "ios-cancel")
            } else if mode == "migration" {
                _ = try core.close()
                engine = nil
                let source = folder.appendingPathComponent("PROTOTYPE-\(argument("--database", fallback: "manual")).db").path
                let candidate = source + ".candidate-" + UUID().uuidString
                result["migration"] = try migrateCopy(source: source, candidate: candidate, key: key(), stop: 0)
                result["activation"] = try activateCopy(source: source, candidate: candidate, key: key(), stop: 0)
                result["activationRecovery"] = try recoverActivation(source: source, key: key())
            } else if mode == "close" {
                result["controlledKeyBufferCleared"] = try core.close()
                engine = nil
                do { _ = try core.inspect(); throw NSError(domain: "closed core accepted read", code: 1) }
                catch ProbeError.Closed { result["closedReadRejected"] = true }
            }
            if mode != "close" {
                let s = try open().inspect()
                result.merge(["firstMinor": s.firstMinor, "secondMinor": s.secondMinor,
                    "entries": s.entries, "queued": s.queued, "receipts": s.receipts,
                    "cipher": s.cipher, "provider": s.provider, "schema": s.schema,
                    "tempStore": s.tempStore, "synchronous": s.synchronous]) { _, new in new }
            }
            result["status"] = "ok"
        } catch { result["status"] = "error"; result["error"] = String(describing: error) }
        do {
            let bytes = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted])
            text = String(decoding: bytes, as: UTF8.self)
            try bytes.write(to: folder.appendingPathComponent("result-\(argument("--run", fallback: "manual")).json"), options: .atomic)
        } catch { text += "\nНе удалось записать результат: \(error)" }
    }
}

@main struct ProbeApp: App {
    @StateObject private var model = ProbeModel()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Rust–Swift / SQLCipher").font(.title)
                    Text("Одноразовый технический прототип. Реальных финансовых данных здесь нет.").foregroundStyle(.secondary)
                    Text(model.text).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                    Button("Прочитать базу") { Task { await model.run("read") } }
                    Button("Записать тестовый расход") { Task { await model.run("seed") } }
                    Button("Выполнить тестовый перевод") { Task { await model.run("transfer") } }
                    Button("Закрыть ядро") { model.close("manual") }
                    Button("Открыть ядро") { Task { await model.unlock() } }
                    Toggle("Запрашивать системное подтверждение после фона", isOn: $model.lockApp)
                    Button("Проверить Face ID / код устройства") { Task { await model.authenticate() } }
                }.padding()
            }.task { await model.initialRun() }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.protectedDataWillBecomeUnavailableNotification)) { _ in
                    model.close("protectedDataWillBecomeUnavailable")
                }
        }.onChange(of: phase) { _, value in
            if value == .inactive || value == .background { model.close("scene-\(value)") }
        }
    }
}
