"""Fail if the narrowly scoped pip/setuptools VEX assessment stops applying."""

import importlib.metadata
import importlib.util
import json
import sysconfig
from pathlib import Path


def main() -> None:
    root = Path(sysconfig.get_path("purelib"))
    vendor = root / "pip" / "_vendor"
    assert importlib.metadata.version("pip") == "26.3", "Review VEX after changing pip."
    assert importlib.util.find_spec("setuptools") is None, "Installed setuptools needs an independent assessment."
    assert not list(root.rglob("package_index.py")), "The vulnerable PackageIndex code is present."
    assert (vendor / "pkg_resources" / "__init__.py").is_file(), "Unexpected pip vendoring layout."
    assert "setuptools==70.3.0" in (vendor / "vendor.txt").read_text(encoding="utf-8"), (
        "Review changed vendor metadata."
    )
    print(json.dumps({"pip": "26.3", "setuptools_installed": False, "vulnerable_code_present": False}))


if __name__ == "__main__":
    main()
