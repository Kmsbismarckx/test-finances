"""Record actual packaged slices and build-version commands from the native apps."""
from pathlib import Path
import json
import os
import plistlib
import subprocess

root = Path(__file__).resolve().parent
env = dict(os.environ, DEVELOPER_DIR=os.environ.get("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer"))


def output(*args):
    p = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
    return p.stdout.strip()


info = plistlib.loads((root / "Package/Artifacts/FinanceProbeFFI.xcframework/Info.plist").read_bytes())
apps = {}
for name, sdk in [("device", "iphoneos"), ("simulator", "iphonesimulator")]:
    app = root / f"build/ios-{name}/Build/Products/Debug-{sdk}/ProbeApp.app"
    binary = app / "ProbeApp.debug.dylib"
    if not binary.exists():
        binary = app / "ProbeApp"
    apps[name] = {
        "architecture": output("xcrun", "lipo", "-archs", str(binary)),
        "buildVersion": output("xcrun", "vtool", "-show-build", str(binary)).replace(str(root), "<prototype>"),
        "xcodeCodeSigningAllowed": name == "simulator",
        "signing": "Xcode ad-hoc; simulated iOS entitlements" if name == "simulator" else "unsigned build only",
    }
    if name == "simulator":
        entitlements = output("codesign", "-d", "--entitlements", ":-", str(app))
        apps[name]["hostSignatureEntitlements"] = plistlib.loads(entitlements.encode())
        simulated = root / "build/ios-simulator/Build/Intermediates.noindex/ProbeApp.build/Debug-iphonesimulator/ProbeApp.build/ProbeApp.app-Simulated.xcent"
        apps[name]["simulatedEntitlements"] = plistlib.loads(simulated.read_bytes())
report = {"xcode": output("xcodebuild", "-version"),
          "rust": output("rustc", "--version"), "swift": output("swift", "--version"),
          "slices": info["AvailableLibraries"], "apps": apps,
          "delivery": "local Swift Package consumes XCFramework; no direct app link to target/debug",
          "buildDeploymentTarget": "17.0 is a probe floor, not verified minimum supported runtime"}
(root / "results/build-observations.json").write_text(json.dumps(report, indent=2) + "\n")
print("BUILD REPORT:", root / "results/build-observations.json")
