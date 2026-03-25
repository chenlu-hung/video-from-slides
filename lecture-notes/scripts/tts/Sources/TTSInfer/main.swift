import AudioToolbox
import Foundation
import CosyVoiceTTS
import AudioCommon

// MARK: - Argument Parsing

struct Arguments {
    var srtPath: String = ""
    var outputPath: String = ""
    var voiceRefPath: String? = nil
    var embeddingPath: String? = nil
    var saveEmbeddingPath: String? = nil
    var backend: String = "coreml"
    var instruction: String = "Please speak naturally."
    var language: String = "auto"
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
        case "--embedding":
            args.embeddingPath = it.next()
        case "--save-embedding":
            args.saveEmbeddingPath = it.next()
        case "--backend":
            args.backend = it.next() ?? "coreml"
        case "--instruction":
            args.instruction = it.next() ?? ""
        case "--language":
            args.language = it.next() ?? "auto"
        default:
            break
        }
    }

    return args
}

func printUsageAndExit() -> Never {
    fputs("Usage: TTSInfer --srt <path> --output <path> [--voice-ref <path|dir>] [--embedding <path.json>] [--save-embedding <path.json>] [--backend coreml|mlx] [--instruction <text>]\n", stderr)
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

        let text = lines[2...].joined(separator: " ")
        segments.append(SRTSegment(index: index, startMs: startMs, endMs: endMs, text: text))
    }

    return segments.sorted { $0.startMs < $1.startMs }
}

// MARK: - Language Detection

func detectLanguage(_ text: String) -> String {
    var cjkCount = 0
    var latinCount = 0
    for scalar in text.unicodeScalars {
        let v = scalar.value
        // CJK Unified Ideographs + CJK Extension A/B + CJK Compatibility
        if (v >= 0x4E00 && v <= 0x9FFF) || (v >= 0x3400 && v <= 0x4DBF) ||
           (v >= 0x20000 && v <= 0x2A6DF) || (v >= 0xF900 && v <= 0xFAFF) {
            cjkCount += 1
        } else if (v >= 0x41 && v <= 0x5A) || (v >= 0x61 && v <= 0x7A) {
            latinCount += 1
        }
    }
    return cjkCount > latinCount ? "chinese" : "english"
}

// MARK: - WAV Writing (via AudioToolbox ExtAudioFile)
// Note: macOS AudioToolbox does not support MP3 encoding (only decoding).
// We write PCM WAV here; callers that need MP3 should pipe through ffmpeg.

func writeMP3(samples: [Float], sampleRate: Int, to path: String) throws {
    // Write to a temp WAV file, then convert to MP3 via ffmpeg.
    let wavPath = path + ".tmp.wav"
    let wavURL = URL(fileURLWithPath: wavPath) as CFURL

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

    // Destination format: 16-bit PCM WAV (macOS native encoder)
    var dstFormat = AudioStreamBasicDescription(
        mSampleRate: Float64(sampleRate),
        mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
        mBytesPerPacket: 2,
        mFramesPerPacket: 1,
        mBytesPerFrame: 2,
        mChannelsPerFrame: 1,
        mBitsPerChannel: 16,
        mReserved: 0
    )

    var extFile: ExtAudioFileRef?
    var status = ExtAudioFileCreateWithURL(
        wavURL, kAudioFileWAVEType, &dstFormat, nil,
        AudioFileFlags.eraseFile.rawValue, &extFile
    )
    guard status == noErr, let file = extFile else {
        throw NSError(domain: "TTSInfer", code: Int(status),
                      userInfo: [NSLocalizedDescriptionKey: "Failed to create WAV file (OSStatus \(status))"])
    }

    status = ExtAudioFileSetProperty(
        file, kExtAudioFileProperty_ClientDataFormat,
        UInt32(MemoryLayout<AudioStreamBasicDescription>.size), &srcFormat
    )
    guard status == noErr else {
        ExtAudioFileDispose(file)
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
    ExtAudioFileDispose(file)

    // Convert WAV → MP3 via ffmpeg
    let ffmpeg = Process()
    ffmpeg.executableURL = URL(fileURLWithPath: "/opt/homebrew/bin/ffmpeg")
    ffmpeg.arguments = ["-y", "-i", wavPath, "-codec:a", "libmp3lame", "-q:a", "2", path]
    ffmpeg.standardOutput = FileHandle.nullDevice
    ffmpeg.standardError = FileHandle.nullDevice
    try ffmpeg.run()
    ffmpeg.waitUntilExit()
    try? FileManager.default.removeItem(atPath: wavPath)
    guard ffmpeg.terminationStatus == 0 else {
        throw NSError(domain: "TTSInfer", code: Int(ffmpeg.terminationStatus),
                      userInfo: [NSLocalizedDescriptionKey: "ffmpeg conversion failed (exit \(ffmpeg.terminationStatus))"])
    }
}

// MARK: - Silence Generation

func silenceSamples(durationMs: Int, sampleRate: Int) -> [Float] {
    let count = max(0, durationMs * sampleRate / 1000)
    return [Float](repeating: 0.0, count: count)
}

// MARK: - Main (async)

func run() async throws {
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

    // Initialize TTS model
    fputs("Loading CosyVoice3 model...\n", stderr)
    let model: CosyVoiceTTSModel
    do {
        model = try await CosyVoiceTTSModel.fromPretrained { progress, status in
            fputs("\r  \(status) (\(Int(progress * 100))%)", stderr)
        }
        fputs("\n", stderr)
    } catch {
        fputs("Failed to initialize CosyVoice model: \(error)\n", stderr)
        exit(3)
    }

    // Extract speaker embedding: from pre-computed JSON, or from voice reference audio
    var speakerEmbedding: [Float]? = nil

    if let embPath = args.embeddingPath {
        // Load pre-computed embedding from JSON
        do {
            let data = try Data(contentsOf: URL(fileURLWithPath: embPath))
            let decoded = try JSONDecoder().decode([Float].self, from: data)
            speakerEmbedding = decoded
            fputs("Loaded speaker embedding from '\(embPath)' (dim=\(decoded.count))\n", stderr)
        } catch {
            fputs("Error: failed to load embedding from '\(embPath)': \(error)\n", stderr)
            exit(6)
        }
    }

    #if canImport(CoreML)
    if speakerEmbedding == nil, let refPath = args.voiceRefPath {
        do {
            let speaker = try await CamPlusPlusSpeaker.fromPretrained { progress, status in
                fputs("\r  \(status) (\(Int(progress * 100))%)", stderr)
            }
            fputs("\n", stderr)

            // Collect audio file paths: single file or directory
            var refFiles: [String] = []
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: refPath, isDirectory: &isDir), isDir.boolValue {
                let audioExts: Set<String> = ["wav", "mp3", "m4a", "aac", "flac", "aiff"]
                let items = try FileManager.default.contentsOfDirectory(atPath: refPath)
                    .filter { audioExts.contains(($0 as NSString).pathExtension.lowercased()) }
                    .sorted()
                refFiles = items.map { (refPath as NSString).appendingPathComponent($0) }
            } else {
                refFiles = [refPath]
            }

            guard !refFiles.isEmpty else {
                fputs("Warning: no audio files found in '\(refPath)'. Using default voice.\n", stderr)
                throw NSError(domain: "TTSInfer", code: 0, userInfo: nil)
            }

            // Compute embeddings, filter low-energy clips, average, and re-normalize
            var embeddings: [[Float]] = []
            var norms: [Float] = []
            for file in refFiles {
                // Skip clips with too little energy (likely silence)
                let audioSamples = try AudioFileLoader.load(
                    url: URL(fileURLWithPath: file), targetSampleRate: 16000)
                let rms = sqrtf(audioSamples.reduce(0.0) { $0 + $1 * $1 } / Float(audioSamples.count))
                if rms < 0.01 {
                    fputs("  Skipping \((file as NSString).lastPathComponent) (too quiet, rms=\(String(format: "%.4f", rms)))\n", stderr)
                    continue
                }

                fputs("  Extracting embedding from \((file as NSString).lastPathComponent) (rms=\(String(format: "%.4f", rms)))...\n", stderr)
                let emb = try speaker.embed(audio: audioSamples, sampleRate: 16000)
                embeddings.append(emb)
                let norm = sqrtf(emb.reduce(0.0) { $0 + $1 * $1 })
                norms.append(norm)
            }

            guard !embeddings.isEmpty else {
                fputs("Warning: all clips were too quiet. Using default voice.\n", stderr)
                throw NSError(domain: "TTSInfer", code: 0, userInfo: nil)
            }

            if embeddings.count == 1 {
                speakerEmbedding = embeddings[0]
            } else {
                let dim = embeddings[0].count
                var averaged = [Float](repeating: 0.0, count: dim)
                for emb in embeddings {
                    for i in 0..<dim { averaged[i] += emb[i] }
                }
                let n = Float(embeddings.count)
                for i in 0..<dim { averaged[i] /= n }

                // Re-normalize: scale averaged embedding to the mean L2 norm of individual embeddings
                let meanNorm = norms.reduce(0.0, +) / Float(norms.count)
                let avgNorm = sqrtf(averaged.reduce(0.0) { $0 + $1 * $1 })
                if avgNorm > 1e-6 {
                    let scale = meanNorm / avgNorm
                    for i in 0..<dim { averaged[i] *= scale }
                }
                speakerEmbedding = averaged
                fputs("  Averaged \(embeddings.count) embeddings (dim=\(dim)), re-normalized L2 norm to \(String(format: "%.2f", meanNorm))\n", stderr)
            }

            // Save embedding if requested
            if let savePath = args.saveEmbeddingPath, let emb = speakerEmbedding {
                let jsonData = try JSONEncoder().encode(emb)
                try jsonData.write(to: URL(fileURLWithPath: savePath))
                fputs("Saved speaker embedding to '\(savePath)'\n", stderr)
            }
        } catch {
            fputs("Warning: failed to extract speaker embedding from '\(refPath)': \(error). Using default voice.\n", stderr)
        }
    }
    #endif

    // Synthesize each segment and assemble with silence gaps
    var allSamples: [Float] = []
    var cursorMs: Int = 0

    // Determine language for all segments (from flag or auto-detect from first segment)
    let language: String
    if args.language != "auto" {
        language = args.language
    } else {
        let allText = segments.map { $0.text }.joined()
        language = detectLanguage(allText)
    }
    fputs("  Language: \(language)\n", stderr)

    for segment in segments {
        // Insert silence before this segment if needed
        let gapMs = segment.startMs - cursorMs
        if gapMs > 0 {
            allSamples.append(contentsOf: silenceSamples(durationMs: gapMs, sampleRate: sampleRate))
        }

        // Synthesize
        fputs("  Synthesizing segment \(segment.index)...\n", stderr)
        let audio: [Float]
        if args.instruction.isEmpty {
            if let embedding = speakerEmbedding {
                audio = model.synthesize(text: segment.text, language: language, speakerEmbedding: embedding)
            } else {
                audio = model.synthesize(text: segment.text, language: language)
            }
        } else {
            audio = model.synthesize(text: segment.text, language: language, instruction: args.instruction, speakerEmbedding: speakerEmbedding)
        }

        guard !audio.isEmpty else {
            fputs("Error: synthesis returned empty audio for segment \(segment.index)\n", stderr)
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
}

// Entry point
let semaphore = DispatchSemaphore(value: 0)
Task {
    do {
        try await run()
    } catch {
        fputs("Fatal error: \(error)\n", stderr)
        exit(1)
    }
    semaphore.signal()
}
semaphore.wait()
