"""Shipped notices cover redistributed tools and include full license texts."""

from __future__ import annotations

from pathlib import Path

from corrcut.notices import licenses_dir, third_party_notices_path

_REQUIRED_LICENSE_FILES = (
    "GPL-2.0.txt",
    "GPL-3.0.txt",
    "LGPL-2.1.txt",
    "LGPL-3.0.txt",
    "Apache-2.0.txt",
    "OFL-1.1.txt",
    "Python-PSF.txt",
    "README.txt",
)


def test_third_party_notices_cover_redistributed_tools() -> None:
    text = third_party_notices_path().read_text(encoding="utf-8")
    assert "Written offer for corresponding source" in text
    assert "FFmpeg" in text
    assert "MKVToolNix" in text
    assert "subtile-ocr" in text
    assert "libmpv" in text
    assert "PySide6" in text
    assert "Embeddable CPython" in text
    assert "PaddleOCR" in text
    assert "OpenCV" in text
    assert "faster-whisper" in text
    assert "qtawesome" in text
    assert "Font Awesome" in text
    assert "Material Design Icons" in text
    assert "sup2srt" in text
    assert "crc32c" in text
    assert "LGPL-2.1-or-later" in text
    assert "python-mpv" in text
    assert "MIT (library binding" not in text
    assert "GPL-2.0-or-later OR LGPL-2.1-or-later" in text
    assert "certifi" in text
    assert "MPL-2.0" in text
    assert "language_tool_python" in text
    assert "Not redistributed in the Windows release" in text
    assert "PySide6-WebEngine" in text
    assert "licenses\\" in text or "licenses/" in text


def test_license_texts_ship_beside_notices() -> None:
    folder = licenses_dir()
    assert folder.is_dir()
    for name in _REQUIRED_LICENSE_FILES:
        path = folder / name
        assert path.is_file(), name
        assert path.stat().st_size > 500, name


def test_gpl_and_apache_texts_look_authentic() -> None:
    folder = licenses_dir()
    gpl2 = (folder / "GPL-2.0.txt").read_text(encoding="utf-8", errors="replace")
    apache = (folder / "Apache-2.0.txt").read_text(encoding="utf-8", errors="replace")
    ofl = (folder / "OFL-1.1.txt").read_text(encoding="utf-8", errors="replace")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl2
    assert "Version 2" in gpl2
    assert "Apache License" in apache
    assert "SIL OPEN FONT LICENSE" in ofl
    assert "CorrLabs" not in Path(third_party_notices_path()).read_text(encoding="utf-8")
