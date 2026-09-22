from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.smoke_clean_install import smoke


@pytest.mark.integration
def test_clean_editable_install_survives_hidden_pth() -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    repository = Path(__file__).resolve().parents[2]
    result = smoke(repository, repository / "work" / "uv-cache")
    assert result["sync"] == "passed"
    assert result["pythonpath_present"] is False
    if __import__("sys").platform == "darwin":
        assert result["hidden_editable_pth_tested"] is True
