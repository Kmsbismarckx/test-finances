# -*- coding: utf-8 -*-
"""Executable probe scenarios, not a production test suite. Uses scratch DBs only."""
from pathlib import Path
import hashlib
import html
import json
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SCRATCH = Path(tempfile.mkdtemp(prefix="PROTOTYPE-finance-", dir=ROOT / "output"))
events = []
MARKER = b"PROBE_SECRET_MARKER_91af"


def run(name, filename, *args, expected_exit=0):
    result = subprocess.run([str(ROOT / "build/probe"), name, str(SCRATCH / filename), *map(str, args)], capture_output=True, text=True, timeout=20)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{name} failed ({result.returncode}): {result.stderr[-2000:]}")
    output = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    event = output[-1] if output else {"scenario": name, "exit": result.returncode, "failpoint": args[-1] if args else None}
    event["file"] = filename
    if expected_exit:
        event["processExitWithoutDestructors"] = True
    events.append(event)
    print(json.dumps(event, ensure_ascii=False), flush=True)
    return event


def require(ok, detail):
    if not ok:
        raise RuntimeError(detail)


def balances(e, first, second, entries):
    require((e["firstMinor"], e["secondMinor"], e["entries"], e["queued"], e["receipts"]) == (first, second, entries, entries, entries), f"state mismatch: {e}")


seed = run("seed", "restart.db")
balances(seed, 99300, 10000, 1)
require(seed["cipher"] and seed["tempStore"] == 2 and seed["synchronous"] == 2, "codec/temp/durability not enabled")
balances(run("read", "restart.db"), 99300, 10000, 1)
balances(run("seed", "restart.db"), 99300, 10000, 1)
balances(run("transfer", "restart.db"), 90300, 19000, 2)

run("crash", "crash-before.db", 1, expected_exit=77)
balances(run("read", "crash-before.db"), 100000, 10000, 0)
run("crash", "crash-after.db", 2, expected_exit=78)
balances(run("read", "crash-after.db"), 91000, 19000, 1)

for gate in [1, 2, 3]:
    e = run("cancel", f"cancel-{gate}.db", gate)
    balances(e, 91000 if gate == 3 else 100000, 19000 if gate == 3 else 10000, 1 if gate == 3 else 0)
    if gate == 3:
        require(e["result"] == "committed_after_cancel" and e["retry"] == "committed_duplicate", "late cancellation lost outcome")
run("task-cancel", "swift-task-cancel.db", 1)
balances(run("close", "close.db"), 100000, 10000, 0)

run("legacy", "source-v1.db")
source = SCRATCH / "source-v1.db"
before = hashlib.sha256(source.read_bytes()).hexdigest()
for stop in [1, 2, 0]:
    target = f"candidate-{stop}.db"
    run("migrate", "source-v1.db", SCRATCH / target, stop, expected_exit={1: 81, 2: 82, 0: 0}[stop])
    observed = run("read", target)
    balances(observed, 99300, 10000, 1)
    require(observed["schema"] == (1 if stop == 1 else 2), "migration schema mismatch")
    require(hashlib.sha256(source.read_bytes()).hexdigest() == before, "migration changed source")
events.append({"scenario": "migration-source", "byteIdentical": True, "activation": "verified separately below"})

for stop in [1, 2, 0]:
    name = f"activation-{stop}.db"
    target = f"activation-candidate-{stop}.db"
    run("legacy", name)
    original_hash = hashlib.sha256((SCRATCH / name).read_bytes()).hexdigest()
    run("migrate", name, SCRATCH / target, 0)
    run("activate", name, SCRATCH / target, stop, expected_exit={1: 83, 2: 84, 0: 0}[stop])
    run("recover-activation", name)
    require(hashlib.sha256((SCRATCH / f"{name}.previous").read_bytes()).hexdigest() == original_hash, "encrypted previous source lost")
    # Recovery is idempotent before ordinary reads switch the fixture to WAL mode.
    require(run("recover-activation", name)["result"] == "nothing_to_recover", "recovery not idempotent")
    observed = run("read", name)
    balances(observed, 99300, 10000, 1)
    require(observed["schema"] == (1 if stop == 1 else 2), "active schema wrong")
events.append({"scenario": "activation-summary", "sourceOrValidatedCandidateOnRestart": True, "encryptedPreviousRetained": True, "powerLossTested": False})

for mode in ["wal", "journal"]:
    e = run(mode, f"files-{mode}.db")
    suffix = "-wal:" if mode == "wal" else "-journal:"
    require(any(item.startswith(suffix) for item in e["observedFiles"]), f"{suffix} not exercised")
before_wrong_key = hashlib.sha256(source.read_bytes()).hexdigest()
run("wrong-key", "source-v1.db")
require(hashlib.sha256(source.read_bytes()).hexdigest() == before_wrong_key, "wrong key changed source")
run("unknown-field", "unused.db")

scanned = []
for file in SCRATCH.iterdir():
    if file.is_file():
        require(MARKER not in file.read_bytes(), f"plaintext marker found in {file.name}")
        scanned.append(file.name)
events.append({"scenario": "file-scan", "markerAbsent": True, "files": sorted(scanned), "cryptographicProof": False})

environment = {}
for name, cmd in [("rust", ["rustc", "--version"]), ("swift", ["swift", "--version"]), ("xcode", ["xcodebuild", "-version"]), ("simulators", ["xcrun", "simctl", "list", "devices", "available"])]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    environment[name] = {"exit": p.returncode, "output": (p.stdout + p.stderr).strip()}
environment["host"] = {"system": platform.system(), "machine": platform.machine()}
report = {"scope": "macOS real Swift/UniFFI/Rust/SQLCipher probe", "events": events, "environment": environment, "remaining": ["iOS evidence is recorded separately in ios-observations.json", "real iPhone Keychain and lock/background events", "actual filesystem full/I/O and power-loss faults", "complete wire/crypto protocol and compatibility matrix", "absolute erasure of Swift/FFI/SQLCipher copies is NOT claimed"]}
for extra, filename in [("ios", "ios-observations.json"), ("iosBuild", "build-observations.json")]:
    file = ROOT / "results" / filename
    if file.exists():
        report[extra] = json.loads(file.read_text())
(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results/observations.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

subprocess.run([__import__("sys").executable, str(ROOT / "render-report.py")], check=True)
print(f"REPORT: {ROOT / 'results/report.html'}")
