#!/bin/bash
# Build-only: does not install on a phone, publish a package, or require signing.
set -euo pipefail
cd "$(dirname "$0")"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
# A probe build floor, not a product minimum-iOS decision.
export IPHONEOS_DEPLOYMENT_TARGET=17.0
cargo build --locked --features bindgen-cli
cargo run --locked --features bindgen-cli --bin bindgen -- generate \
  --library target/debug/libfinance_probe.dylib --language swift --out-dir generated
cargo build --locked --lib --target aarch64-apple-ios
cargo build --locked --lib --target aarch64-apple-ios-sim
mkdir -p build/ios-headers Package/Artifacts Package/Sources/FinanceProbe
cp generated/finance_probeFFI.h build/ios-headers/
cp generated/finance_probeFFI.modulemap build/ios-headers/module.modulemap
cp generated/finance_probe.swift Package/Sources/FinanceProbe/
output_dir=$(mktemp -d "$PWD/build/xcframework-XXXXXX")
xcodebuild -create-xcframework \
  -library "$PWD/target/aarch64-apple-ios/debug/libfinance_probe.a" -headers "$PWD/build/ios-headers" \
  -library "$PWD/target/aarch64-apple-ios-sim/debug/libfinance_probe.a" -headers "$PWD/build/ios-headers" \
  -output "$output_dir/FinanceProbeFFI.xcframework"
python3 - "$output_dir/FinanceProbeFFI.xcframework" <<'PY'
from pathlib import Path
import shutil, sys
target = Path('Package/Artifacts/FinanceProbeFFI.xcframework')
if target.exists():
    shutil.rmtree(target)  # Only this script's generated artifact, never input data.
shutil.move(sys.argv[1], target)
PY
echo "XCFramework and local Swift package prepared."
