import re
import tomllib
from pathlib import Path

import sentinel

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", sentinel.__version__)


def test_version_matches_pyproject() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == sentinel.__version__
