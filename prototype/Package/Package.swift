// swift-tools-version: 6.0
// THROWAWAY local package. Generated binary/Swift are produced by build-ios.sh.
import PackageDescription

let package = Package(
    name: "FinanceProbe",
    platforms: [.iOS(.v17)],
    products: [.library(name: "FinanceProbe", targets: ["FinanceProbe"])],
    targets: [
        .binaryTarget(name: "finance_probeFFI", path: "Artifacts/FinanceProbeFFI.xcframework"),
        .target(name: "FinanceProbe", dependencies: ["finance_probeFFI"], linkerSettings: [
            .linkedFramework("Security"), .linkedFramework("SystemConfiguration"),
            .linkedLibrary("resolv"), .linkedLibrary("iconv")
        ])
    ]
)
