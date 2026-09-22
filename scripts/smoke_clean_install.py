from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout.strip()


def smoke(repository: Path, cache_dir: Path | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="relay-clean-install-") as temporary:
        project = Path(temporary) / "project"
        project.mkdir()
        for filename in ("pyproject.toml", "uv.lock", "README.md", "LICENSE", ".python-version"):
            shutil.copy2(repository / filename, project / filename)
        shutil.copytree(repository / "src", project / "src")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("VIRTUAL_ENV", None)
        if cache_dir is not None:
            environment["UV_CACHE_DIR"] = str(cache_dir.resolve())

        run(["uv", "sync", "--all-extras"], project, environment)
        site_packages = next((project / ".venv" / "lib").glob("python*/site-packages"))
        editable_files = tuple(site_packages.glob("*_editable*local_voice_assistant*.pth"))
        if not editable_files:
            editable_files = tuple(site_packages.glob("*local_voice_assistant*.pth"))
        if sys.platform == "darwin":
            for editable_file in editable_files:
                run(["chflags", "hidden", str(editable_file)], project, environment)

        imported = run(
            [
                "uv",
                "run",
                "python",
                "-c",
                "import local_assistant; print(local_assistant.__file__)",
            ],
            project,
            environment,
        )
        doctor_output = run(["uv", "run", "local-assistant", "doctor"], project, environment)
        doctor = json.loads(doctor_output)
        if not str(doctor["python"]).startswith("3.12."):
            raise AssertionError(f"Expected Python 3.12, got {doctor['python']}")
        if "site-packages/local_assistant/__init__.py" not in imported:
            raise AssertionError(f"Import did not resolve to installed package files: {imported}")
        return {
            "sync": "passed",
            "import": imported,
            "doctor_python": doctor["python"],
            "hidden_editable_pth_tested": sys.platform == "darwin" and bool(editable_files),
            "pythonpath_present": "PYTHONPATH" in environment,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--cache-dir", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(smoke(arguments.repository.resolve(), arguments.cache_dir), indent=2))


if __name__ == "__main__":
    main()
