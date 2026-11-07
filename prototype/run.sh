#!/bin/bash
# One-command host probe. Scratch databases and PUBLIC FIXTURE KEY only.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p generated build output
cargo build --locked --features bindgen-cli
cargo run --locked --features bindgen-cli --bin bindgen -- generate --library target/debug/libfinance_probe.dylib --language swift --out-dir generated
swiftc -swift-version 6 -parse-as-library -module-cache-path build/module-cache \
  -Xcc -fmodule-map-file=generated/finance_probeFFI.modulemap \
  -I generated -L target/debug -lfinance_probe \
  -Xlinker -rpath -Xlinker "$PWD/target/debug" \
  generated/finance_probe.swift Probe.swift -o build/probe
python3 observe.py
