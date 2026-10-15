from corrcut.engine.toolchain import _parse_version, _version_at_least


def test_parse_version() -> None:
    assert _parse_version("ffmpeg version 6.1.1 Copyright") == (6, 1, 1)


def test_version_at_least() -> None:
    assert _version_at_least((6, 0, 0), (6, 0))
    assert not _version_at_least((5, 1, 0), (6, 0))
