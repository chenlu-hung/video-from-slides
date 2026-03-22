// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TTSInfer",
    platforms: [
        .macOS(.v14)
    ],
    dependencies: [
        .package(url: "https://github.com/soniqo/speech-swift", branch: "main"),
    ],
    targets: [
        .executableTarget(
            name: "TTSInfer",
            dependencies: [
                .product(name: "SpeechSwift", package: "speech-swift"),
            ],
            path: "Sources/TTSInfer"
        ),
    ]
)
