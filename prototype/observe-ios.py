"""Run the actual Swift Package/XCFramework consumer app on a dedicated simulator."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent
BUNDLE = "dev.finance.throwawayprobe"
APP = ROOT / "build/ios-simulator/Build/Products/Debug-iphonesimulator/ProbeApp.app"
env = dict(os.environ, DEVELOPER_DIR=os.environ.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"))


def sim(*args, timeout=60, check=True):
    p = subprocess.run(["xcrun", "simctl", *map(str, args)], env=env, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"simctl {args[0]} failed: {p.stderr[-2000:]}")
    return p


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


runtimes = json.loads(sim("list", "runtimes", "-j").stdout)["runtimes"]
ios = [r for r in runtimes if r.get("isAvailable") and r["name"].startswith("iOS")]
require(ios, "Install an iOS Simulator runtime in Xcode first")
runtime = max(ios, key=lambda r: (tuple(map(int, r["version"].split("."))), len(r.get("supportedDeviceTypes", []))))
types = runtime.get("supportedDeviceTypes") or json.loads(sim("list", "devicetypes", "-j").stdout)["devicetypes"]
preferred = [t for t in types if t["identifier"].endswith("iPhone-17-Pro")]
device_type = preferred[0] if preferred else next(t for t in reversed(types) if "iPhone" in t["name"])
devices = json.loads(sim("list", "devices", "available", "-j").stdout)["devices"].get(runtime["identifier"], [])
owned = next((d for d in devices if d["name"] == "FinancePrototype-Throwaway"), None)
device = owned["udid"] if owned else sim("create", "FinancePrototype-Throwaway", device_type["identifier"], runtime["identifier"]).stdout.strip()
sim("boot", device, check=False)
sim("bootstatus", device, "-b", timeout=180)
sim("install", device, APP)
container = Path(sim("get_app_container", device, BUNDLE, "data").stdout.strip())
documents = container / "Documents"
events = []
run_prefix = uuid.uuid4().hex[:10]


def launch(mode, db, crash=False):
    run_id = uuid.uuid4().hex
    launch = sim("launch", "--terminate-running-process", device, BUNDLE,
                 "--scenario", mode, "--database", f"{run_prefix}_{db}", "--run", run_id)
    if crash:
        pid_match = re.search(r": (\d+)\s*$", launch.stdout)
        require(pid_match, "simctl did not return application pid")
        pid = pid_match.group(1)
        for _ in range(500):
            p = subprocess.run(["ps", "-p", pid, "-o", "stat="], capture_output=True, text=True)
            if p.returncode or p.stdout.strip().startswith("Z"):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("expected failpoint process exit did not occur")
        result = {"scenario": mode, "database": db, "processEndedAtFailpoint": True}
    else:
        file = documents / f"result-{run_id}.json"
        for _ in range(600):
            if file.exists():
                result = json.loads(file.read_text())
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"no report from {mode}; app launch output: {launch.stdout}")
        require(result.get("status") == "ok", f"app scenario failed: {result}")
        # IDs are only execution bookkeeping, not useful evidence.
        result.pop("run", None)
        result["database"] = db
    events.append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def balances(result, first, second, count):
    require((result["firstMinor"], result["secondMinor"], result["entries"], result["queued"], result["receipts"]) == (first, second, count, count, count), f"inconsistent state: {result}")


balances(launch("seed", "restart"), 99300, 10000, 1)
balances(launch("read", "restart"), 99300, 10000, 1)
require(launch("seed", "restart")["outcome"] == "committed_duplicate", "retry duplicated expense")
balances(launch("transfer", "restart"), 90300, 19000, 2)
balances(launch("read", "restart"), 90300, 19000, 2)
closed = launch("close", "restart")
require(closed["controlledKeyBufferCleared"] and closed["closedReadRejected"], "close did not close core")
launch("crash-before", "crash_before", crash=True)
balances(launch("read", "crash_before"), 100000, 10000, 0)
launch("crash-after", "crash_after", crash=True)
balances(launch("read", "crash_after"), 91000, 19000, 1)
before_commit = launch("cancel-before", "cancel_before")
balances(before_commit, 100000, 10000, 0)
require(before_commit["outcome"] == "cancelled_before_commit", "early cancellation did not roll back")
after_commit = launch("cancel-after", "cancel_after")
balances(after_commit, 91000, 19000, 1)
require(after_commit["outcome"] == "committed_after_cancel" and after_commit["receipt"] == "committed", "late cancellation lost durable outcome")
balances(launch("seed", "migration"), 99300, 10000, 1)
migrated = launch("migration", "migration")
balances(migrated, 99300, 10000, 1)
require(migrated["schema"] == 2 and migrated["activationRecovery"] == "nothing_to_recover", "migration activation failed")
balances(launch("read", "migration"), 99300, 10000, 1)

# Cause a real scene transition in the simulator. This does not model hardware lock.
lifecycle = documents / "lifecycle.jsonl"
launch("read", "restart")
before = len(lifecycle.read_text().splitlines()) if lifecycle.exists() else 0
sim("launch", device, "com.apple.Preferences")
for _ in range(200):
    lines = lifecycle.read_text().splitlines()[before:] if lifecycle.exists() else []
    transition = [json.loads(line) for line in lines]
    if any(e["controlledKeyBufferCleared"] for e in transition):
        break
    time.sleep(0.05)
else:
    raise RuntimeError("scene transition did not close an open core")
events.append({"scenario": "scene-transition", "events": transition, "hardwareLockTested": False})
sim("terminate", device, BUNDLE, check=False)
scanned = []
for file in documents.glob(f"PROTOTYPE-{run_prefix}_*"):
    if file.is_file():
        require(b"PROBE_SECRET_MARKER_91af" not in file.read_bytes(), f"plaintext marker in {file.name}")
        scanned.append(file.name.replace(run_prefix, "<run>"))
events.append({"scenario": "file-scan", "files": sorted(scanned), "markerAbsent": True, "cryptographicProof": False})
report = {"scope": "iOS Simulator actual local Swift Package + XCFramework app", "runtime": runtime["name"],
          "deviceName": "FinancePrototype-Throwaway", "architecture": "arm64", "buildDeploymentTarget": "17.0 (probe floor only)",
          "events": events, "physicalDeviceTested": False,
          "remaining": ["physical iPhone signing, passcode-protected Keychain, Face ID and hardware lock", "device-level power-loss/full-disk and complete protocol compatibility"]}
(ROOT / "results/ios-observations.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print("IOS REPORT:", ROOT / "results/ios-observations.json")
