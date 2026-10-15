Bundled DVD subtitle OCR (vobsubocr-compatible).

Expected layout:
- vobsubocr.exe — built from subtile-ocr (maintained fork of elizagamedev/vobsubocr)
- *.dll — runtime dependencies from vcpkg (tesseract, leptonica, etc.)
- tessdata/eng.traineddata — English OCR model

Rebuild everything with:

```powershell
.\scripts\bundle-vobsubocr.ps1
```

Prerequisites for building:
- Rust (`cargo`)
- Visual Studio 2022 C++ build tools
- vcpkg with `tesseract:x64-windows` and `leptonica:x64-windows`
- LLVM (`winget install LLVM.LLVM`) for bindgen/libclang
- Tesseract OCR installed (provides eng.traineddata to copy)

`vobsubocr.exe` is tracked even though the repo ignores other `*.exe` files,
so the tag build can check it out. Rebuild it with the script above when the
OCR tool changes, and commit the new executable with the DLLs.

To add more languages, copy additional `*.traineddata` files into `tessdata/`.
