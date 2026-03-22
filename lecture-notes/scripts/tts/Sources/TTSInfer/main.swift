import AudioToolbox
import Foundation
import SpeechSwift

// MARK: - Argument Parsing

struct Arguments {
    var srtPath: String = ""
    var outputPath: String = ""
    var voiceRefPath: String? = nil
    var backend: String = "coreml"
}

func parseArguments() -> Arguments {
    var args = Arguments()
    let argv = CommandLine.arguments.dropFirst()
    var it = argv.makeIterator()

    while let flag = it.next() {
        switch flag {
        case "--srt":
            args.srtPath = it.next() ?? ""
        case "--output":
            args.outputPath = it.next() ?? ""
        case "--voice-ref":
            args.voiceRefPath = it.next()
        case "--backend":
            args.backend = it.next() ?? "coreml"
        default:
            break
        }
    }

    return args
}

func printUsageAndExit() -> Never {
    fputs("Usage: TTSInfer --srt <path> --output <path> [--voice-ref <path>] [--backend coreml|mlx]\n", stderr)
    exit(1)
}

// MARK: - SRT Parsing

struct SRTSegment {
    let index: Int
    let startMs: Int
    let endMs: Int
    let text: String
}

func parseTimecode(_ s: String) -> Int? {
    // Expected: HH:MM:SS,mmm
    let parts = s.split(separator: ",")
    guard parts.count == 2, let ms = Int(parts[1]) else { return nil }
    let hms = parts[0].split(separator: ":")
    guard hms.count == 3,
          let h = Int(hms[0]), let m = Int(hms[1]), let sec = Int(hms[2]) else { return nil }
    return ((h * 3600 + m * 60 + sec) * 1000) + ms
}

func parseSRT(at path: String) throws -> [SRTSegment] {
    let content = try String(contentsOfFile: path, encoding: .utf8)
    var segments: [SRTSegment] = []

    let blocks = content.components(separatedBy: "\n\n")
    for block in blocks {
        let lines = block.trimmingCharacters(in: .whitespacesAndNewlines)
            .components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        guard lines.count >= 3,
              let index = Int(lines[0]) else { continue }

        let timeParts = lines[1].components(separatedBy: " --> ")
        guard timeParts.count == 2,
              let startMs = parseTimecode(timeParts[0].trimmingCharacters(in: .whitespaces)),
              let endMs = parseTimecode(timeParts[1].trimmingCharacters(in: .whitespaces)) else { continue }

        let text = lines[2...].joined(separator: "\n")
        segments.append(SRTSegment(index: index, startMs: startMs, endMs: endMs, text: text))
    }

    return segments.sorted { $0.startMs < $1.startMs }
}

// MARK: - MP3 Writing (via AudioToolbox ExtAudioFile)

func writeMP3(samples: [Float], sampleRate: Int, to path: String) throws {
    let url = URL(fileURLWithPath: path) as CFURL

    // Client format: Float32 mono PCM (what we feed in)
    var srcFormat = AudioStreamBasicDescription(
        mSampleRate: Float64(sampleRate),
        mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
        mBytesPerPacket: 4,
        mFramesPerPacket: 1,
        mBytesPerFrame: 4,
        mChannelsPerFrame: 1,
        mBitsPerChannel: 32,
        mReserved: 0
    )

    // Destination format: MP3
    var dstFormat = AudioStreamBasicDescription(
        mSampleRate: Float64(sampleRate),
        mFormatID: kAudioFormatMPEGLayer3,
        mFormatFlags: 0,
        mBytesPerPacket: 0,
        mFramesPerPacket: 0,
        mBytesPerFrame: 0,
        mChannelsPerFrame: 1,
        mBitsPerChannel: 0,
        mReserved: 0
    )

    var extFile: ExtAudioFileRef?
    var status = ExtAudioFileCreateWithURL(
        url, kAudioFileMP3Type, &dstFormat, nil,
        AudioFileFlags.eraseFile.rawValue, &extFile
    )
    guard status == noErr, let file = extFile else {
        throw NSError(domain: "TTSInfer", code: Int(status),
                      userInfo: [NSLocalizedDescriptionKey: "Failed to create MP3 file (OSStatus \(status))"])
    }
    defer { ExtAudioFileDispose(file) }

    status = ExtAudioFileSetProperty(
        file, kExtAudioFileProperty_ClientDataFormat,
        UInt32(MemoryLayout<AudioStreamBasicDescription>.size), &srcFormat
    )
    guard status == noErr else {
        throw NSError(domain: "TTSInfer", code: Int(status),
                      userInfo: [NSLocalizedDescriptionKey: "Failed to set client format (OSStatus \(status))"])
    }

    try samples.withUnsafeBytes { ptr in
        var audioBuffer = AudioBuffer(
            mNumberChannels: 1,
            mDataByteSize: UInt32(samples.count * MemoryLayout<Float>.size),
            mData: UnsafeMutableRawPointer(mutating: ptr.baseAddress!)
        )
        var bufferList = AudioBufferList(mNumberBuffers: 1, mBuffers: audioBuffer)
        let writeStatus = ExtAudioFileWrite(file, UInt32(samples.count), &bufferList)
        guard writeStatus == noErr else {
            throw NSError(domain: "TTSInfer", code: Int(writeStatus),
                          userInfo: [NSLocalizedDescriptionKey: "Failed to write audio (OSStatus \(writeStatus))"])
        }
    }
}

// MARK: - Silence Generation

func silenceSamples(durationMs: Int, sampleRate: Int) -> [Float] {
    let count = max(0, durationMs * sampleRate / 1000)
    return [Float](repeating: 0.0, count: count)
}

// MARK: - Main

let args = parseArguments()

guard !args.srtPath.isEmpty, !args.outputPath.isEmpty else {
    printUsageAndExit()
}

// Parse SRT
let segments: [SRTSegment]
do {
    segments = try parseSRT(at: args.srtPath)
} catch {
    fputs("Error reading SRT file '\(args.srtPath)': \(error)\n", stderr)
    exit(2)
}

guard !segments.isEmpty else {
    fputs("No valid SRT segments found in '\(args.srtPath)'\n", stderr)
    exit(2)
}

// Determine sample rate
let sampleRate = 24000  // CosyVoice 3 native output rate

// Initialize TTS engine
let backendType: SpeechSwift.Backend = args.backend == "mlx" ? .mlx : .coreml

let engine: CosyVoiceEngine
do {
    var config = CosyVoiceEngine.Configuration()
    config.backend = backendType
    engine = try CosyVoiceEngine(configuration: config)
} catch {
    fputs("Failed to initialize CosyVoice engine: \(error)\n", stderr)
    exit(3)
}

// Load speaker embedding from voice reference (optional)
var speakerEmbedding: SpeakerEmbedding? = nil
if let refPath = args.voiceRefPath {
    do {
        speakerEmbedding = try engine.extractSpeakerEmbedding(from: URL(fileURLWithPath: refPath))
    } catch {
        fputs("Warning: failed to extract speaker embedding from '\(refPath)': \(error). Using default voice.\n", stderr)
    }
}

// Synthesize each segment and assemble with silence gaps
var allSamples: [Float] = []
var cursorMs: Int = 0

for segment in segments {
    // Insert silence before this segment if needed
    let gapMs = segment.startMs - cursorMs
    if gapMs > 0 {
        allSamples.append(contentsOf: silenceSamples(durationMs: gapMs, sampleRate: sampleRate))
    }

    // Synthesize
    let audio: [Float]
    do {
        if let embedding = speakerEmbedding {
            audio = try engine.synthesize(text: segment.text, speakerEmbedding: embedding)
        } else {
            audio = try engine.synthesize(text: segment.text)
        }
    } catch {
        fputs("Error synthesizing segment \(segment.index): \(error)\n", stderr)
        exit(4)
    }

    allSamples.append(contentsOf: audio)

    // Advance cursor to end of segment
    let synthesizedMs = audio.count * 1000 / sampleRate
    cursorMs = segment.startMs + max(synthesizedMs, segment.endMs - segment.startMs)
}

// Write output MP3
do {
    try writeMP3(samples: allSamples, sampleRate: sampleRate, to: args.outputPath)
} catch {
    fputs("Failed to write output MP3 to '\(args.outputPath)': \(error)\n", stderr)
    exit(5)
}

print("Synthesized \(segments.count) segment(s) → \(args.outputPath)")
exit(0)
