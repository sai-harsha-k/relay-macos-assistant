from pathlib import Path


def test_py2app_keeps_playwright_native_driver_out_of_python_zip() -> None:
    setup_source = (Path(__file__).resolve().parents[1] / "setup_app.py").read_text()
    assert '"packages": ["rumps", "playwright"]' in setup_source
    assert '"playwright._impl.__pyinstaller"' in setup_source
    assert (
        "compute_driver_executable"
        in (Path(__file__).resolve().parents[1] / "src/local_assistant/macos_app.py").read_text()
    )
