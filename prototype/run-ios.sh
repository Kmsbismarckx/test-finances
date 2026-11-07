#!/bin/bash
# One-command simulator probe, only dev.finance.throwawayprobe and a dedicated simulator.
set -euo pipefail
cd "$(dirname "$0")"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
bash build-ios.sh
xcodebuild -quiet -project ios/ProbeApp.xcodeproj -scheme ProbeApp -configuration Debug \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath "$PWD/build/ios-device" ARCHS=arm64 CODE_SIGNING_ALLOWED=NO build
xcodebuild -quiet -project ios/ProbeApp.xcodeproj -scheme ProbeApp -configuration Debug \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath "$PWD/build/ios-simulator" ARCHS=arm64 ONLY_ACTIVE_ARCH=YES \
  CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- build
python3 record-build.py
if [[ "${1:-}" != "--build-only" ]]; then
  python3 observe-ios.py
fi
python3 render-report.py
