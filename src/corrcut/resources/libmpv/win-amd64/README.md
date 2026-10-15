# Optional bundled libmpv (Windows x64)

Place `libmpv-2.dll` (and any DLLs shipped beside it in the mpv-dev archive) in this folder
to use preview without a network download.

The app will otherwise download a pinned mpv-winbuild dev archive on first run into
`~/.corrcut/cache/libmpv/`.

Do not commit large binaries to git unless you intend to ship an offline build.
