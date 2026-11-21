// vision_ocr — Apple Vision text recognition for ReadProof (Apache-2.0).
//
// usage: vision_ocr IMAGE [IMAGE ...]
// Prints one JSON line per image, in argument order:
//   {"path": "...", "lines": [{"text": "...", "confidence": 0.98, "bbox": [x, y, w, h]}], "error": "..."}
// bbox is normalised to the image (0..1) with a TOP-left origin (Vision's own origin is bottom-left; it is
// flipped here so callers sort lines top-to-bottom without knowing Vision's convention). "error" is present
// only when the image could not be read or recognised.
//
// Recognition: VNRecognizeTextRequest, recognitionLevel .accurate, languages zh-Hans, zh-Hant, en-US, ja-JP,
// language correction on. If this macOS rejects the language list, it retries once with the languages it
// supports from that list, and says so in "error" only if that also fails.

import Foundation
import ImageIO
import Vision

struct Line: Encodable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}

struct Result: Encodable {
    let path: String
    let lines: [Line]
    let error: String?
}

let wanted = ["zh-Hans", "zh-Hant", "en-US", "ja-JP"]

func supportedLanguages() -> [String] {
    let probe = VNRecognizeTextRequest()
    probe.recognitionLevel = .accurate
    guard let supported = try? probe.supportedRecognitionLanguages() else { return wanted }
    let usable = wanted.filter { supported.contains($0) }
    return usable.isEmpty ? wanted : usable
}

let languages = supportedLanguages()

func recognize(_ path: String) -> Result {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        return Result(path: path, lines: [], error: "cannot decode image")
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = languages
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return Result(path: path, lines: [], error: "vision: \(error.localizedDescription)")
    }
    var lines: [Line] = []
    for observation in request.results ?? [] {
        guard let best = observation.topCandidates(1).first else { continue }
        let b = observation.boundingBox
        let top = 1.0 - Double(b.origin.y) - Double(b.size.height)
        lines.append(Line(
            text: best.string,
            confidence: best.confidence,
            bbox: [Double(b.origin.x), top, Double(b.size.width), Double(b.size.height)]
        ))
    }
    return Result(path: path, lines: lines, error: nil)
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]

for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {
        let result = recognize(path)
        if let data = try? encoder.encode(result), let line = String(data: data, encoding: .utf8) {
            print(line)
        } else {
            print("{\"path\": \"\", \"lines\": [], \"error\": \"encode failed\"}")
        }
        fflush(stdout)
    }
}
