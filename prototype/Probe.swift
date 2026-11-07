// THROWAWAY macOS harness. A public constant fixture key, never personal data.
import Foundation

enum HarnessFailure: Error { case message(String) }
func require(_ ok: Bool, _ message: String) throws {
    if !ok { throw HarnessFailure.message(message) }
}
func emit(_ name: String, _ fields: [String: Any]) {
    var event = fields
    event["scenario"] = name
    let bytes = try! JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
    print(String(decoding: bytes, as: UTF8.self))
}
func state(_ s: Snapshot) -> [String: Any] {
    ["firstMinor": s.firstMinor, "secondMinor": s.secondMinor,
     "entries": s.entries, "queued": s.queued, "receipts": s.receipts,
     "schema": s.schema, "cipher": s.cipher, "provider": s.provider,
     "tempStore": s.tempStore, "synchronous": s.synchronous, "journal": s.journal]
}
func waitStage(_ c: Control, _ target: UInt8) async throws {
    for _ in 0..<2000 {
        if c.stage() == target { return }
        try await Task.sleep(nanoseconds: 2_000_000)
    }
    throw HarnessFailure.message("did not reach gate")
}

@main struct Probe {
    static func main() async throws {
        let args = CommandLine.arguments
        guard args.count >= 3 else { throw HarnessFailure.message("usage: probe scenario scratch-path [args]") }
        let mode = args[1], path = args[2]
        let key = Data(repeating: 0x42, count: 32)
        if mode == "activate" {
            let result = try activateCopy(source: path, candidate: args[3], key: key, stop: UInt8(args[4])!)
            emit(mode, ["result": result]); return
        }
        if mode == "recover-activation" {
            emit(mode, ["result": try recoverActivation(source: path, key: key)]); return
        }
        if mode == "migrate" {
            do {
                let result = try migrateCopy(source: path, candidate: args[3], key: key, stop: UInt8(args[4])!)
                emit(mode, ["result": result])
            } catch {
                if args[4] == "0" { throw error }
                emit(mode, ["interruptedAt": args[4], "sourcePreservedForRetry": true])
            }
            return
        }
        if mode == "wrong-key" {
            do { _ = try Engine(path: path, key: Data(repeating: 0x11, count: 32), wal: false) }
            catch { emit(mode, ["rejected": true]); return }
            throw HarnessFailure.message("wrong key accepted")
        }
        if mode == "unknown-field" {
            let payload = "{\"minor\":9007199254740993,\"note\":\"old\",\"future\":{\"flag\":true,\"nested\":[1,2,3]}}"
            let output = try unknownFieldRoundtrip(payload: payload)
            let parsed = try JSONSerialization.jsonObject(with: Data(output.utf8)) as! [String: Any]
            try require(parsed["future"] != nil && output.contains("9007199254740993"), "unknown data lost")
            emit(mode, ["preserved": true, "output": output]); return
        }
        let wal = mode != "legacy" && mode != "journal"
        let engine = try Engine(path: path, key: key, wal: wal)
        if mode == "seed" || mode == "legacy" {
            let result = try await engine.apply(id: "expense-1", amount: 700, transfer: false, control: Control(hold: 0), crash: 0)
            var fields = state(try engine.inspect()); fields["result"] = result
            emit(mode, fields)
        } else if mode == "read" {
            emit(mode, state(try engine.inspect()))
        } else if mode == "transfer" {
            let result = try await engine.apply(id: "transfer-1", amount: 9000, transfer: true, control: Control(hold: 0), crash: 0)
            var fields = state(try engine.inspect()); fields["result"] = result
            emit(mode, fields)
        } else if mode == "crash" {
            _ = try await engine.apply(id: "crash-transfer", amount: 9000, transfer: true, control: Control(hold: 0), crash: UInt8(args[3])!)
            throw HarnessFailure.message("crash failpoint did not exit")
        } else if mode == "cancel" || mode == "task-cancel" {
            let hold = UInt8(args[3])!
            let control = Control(hold: hold)
            let work = Task.detached {
                try await engine.apply(id: "cancel-transfer", amount: 9000, transfer: true, control: control, crash: 0)
            }
            try await waitStage(control, hold)
            if mode == "cancel" { control.cancel() } else { work.cancel() }
            control.release()
            var outcome: String
            do { outcome = try await work.value }
            catch ProbeError.Cancelled { outcome = "cancelled_before_commit" }
            catch { outcome = "ffi_error_\(String(describing: error))" }
            var fields = state(try engine.inspect())
            fields["result"] = outcome
            fields["receipt"] = try engine.outcome(id: "cancel-transfer")
            if mode == "cancel" {
                try require(hold == 3 ? fields["receipt"] as? String == "committed" : fields["receipt"] as? String == "not_committed", "wrong cancellation boundary")
            }
            if fields["receipt"] as? String == "committed" {
                fields["retry"] = try await engine.apply(id: "cancel-transfer", amount: 9000, transfer: true, control: Control(hold: 0), crash: 0)
            }
            emit(mode, fields)
        } else if mode == "close" {
            let control = Control(hold: 1)
            let work = Task.detached {
                try await engine.apply(id: "close-transfer", amount: 9000, transfer: true, control: control, crash: 0)
            }
            try await waitStage(control, 1)
            let closing = Task.detached { try engine.close() }
            for _ in 0..<2000 {
                if engine.isClosing() { break }
                try await Task.sleep(nanoseconds: 2_000_000)
            }
            try require(engine.isClosing(), "close did not start")
            control.release()
            do { _ = try await work.value; throw HarnessFailure.message("closing command committed unexpectedly") }
            catch ProbeError.Cancelled {}
            let cleared = try await closing.value
            do { _ = try engine.inspect(); throw HarnessFailure.message("closed core accepted read") }
            catch ProbeError.Closed {}
            do { _ = try await engine.apply(id: "late", amount: 1, transfer: false, control: Control(hold: 0), crash: 0); throw HarnessFailure.message("closed core accepted command") }
            catch ProbeError.Closed {}
            let reopened = try Engine(path: path, key: key, wal: true)
            var fields = state(try reopened.inspect())
            fields["controlledKeyBufferCleared"] = cleared
            fields["newCommandsRejected"] = true
            fields["hardwareLockEvent"] = false
            emit(mode, fields)
            _ = try reopened.close(); return
        } else if mode == "wal" || mode == "journal" {
            let files = try engine.stressFiles(rollback: mode == "journal")
            var fields = state(try engine.inspect()); fields["observedFiles"] = files
            emit(mode, fields)
        } else { throw HarnessFailure.message("unknown scenario") }
        _ = try engine.close()
    }
}
