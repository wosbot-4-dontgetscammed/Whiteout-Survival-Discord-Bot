# tools/

## ocr_vision.swift

On-device OCR helper for the profile-screenshot member add (`/add_screenshot`).
Uses Apple's Vision framework (`VNRecognizeTextRequest`) — offline, free, no API
key, and reads unicode/emoji nicknames correctly. macOS only.

Build (once, per host):

```sh
swiftc -O tools/ocr_vision.swift -o bin/ocr_vision
```

The bot calls `bin/ocr_vision <image>` and reads one JSON object per recognised
text line: `{"t":text,"x":..,"y":..,"w":..,"h":..,"c":confidence}` (normalised
coords, origin bottom-left). If `bin/ocr_vision` is missing, the screenshot add
command reports OCR unavailable and the rest of the bot is unaffected.
