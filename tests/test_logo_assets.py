from pathlib import Path


def test_icon_build_uses_single_replaceable_logo_source() -> None:
    root = Path(__file__).resolve().parents[1]
    logo = root / "assets" / "relay-logo.svg"
    script = (root / "scripts" / "build_icon.sh").read_text(encoding="utf-8")
    assert logo.is_file()
    assert 'logo_source="$assets_dir/relay-logo.svg"' in script
    assert '"$logo_source" "$assets_dir/relay-menubar.png"' in script
