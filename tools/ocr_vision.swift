// Apple Vision OCR helper. Usage: ocr_vision <image_path>
// Prints one JSON object per recognized text line: {"t":text,"x":..,"y":..,"w":..,"h":..,"c":conf}
// Coordinates are normalized (0..1), origin bottom-left (Vision convention).
import Foundation
import Vision
import CoreGraphics
import ImageIO

guard CommandLine.arguments.count >= 2 else { FileHandle.standardError.write("usage: ocr_vision <image>\n".data(using:.utf8)!); exit(2) }
let path = CommandLine.arguments[1]
guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
      let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    FileHandle.standardError.write("cannot load image\n".data(using:.utf8)!); exit(1)
}

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
// broad language set so unicode names survive
req.recognitionLanguages = ["en-US","de-DE","fr-FR","es-ES","pt-BR","it-IT","pl-PL","tr-TR"]

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([req]) } catch {
    FileHandle.standardError.write("vision failed: \(error)\n".data(using:.utf8)!); exit(1)
}

func esc(_ s: String) -> String {
    var o = ""
    for ch in s.unicodeScalars {
        switch ch {
        case "\"": o += "\\\""
        case "\\": o += "\\\\"
        case "\n": o += "\\n"
        case "\t": o += "\\t"
        default:
            if ch.value < 0x20 { o += String(format:"\\u%04x", ch.value) } else { o.unicodeScalars.append(ch) }
        }
    }
    return o
}

for obs in (req.results ?? []) {
    guard let top = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox
    let line = "{\"t\":\"\(esc(top.string))\",\"x\":\(b.origin.x),\"y\":\(b.origin.y),\"w\":\(b.size.width),\"h\":\(b.size.height),\"c\":\(top.confidence)}"
    print(line)
}
