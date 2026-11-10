"""Print runtime source hashes and package versions without importing the app."""

import hashlib
import importlib.metadata
import json
import sys
import sysconfig
from pathlib import Path

sys.dont_write_bytecode = True


def collect() -> dict:
    application_root = Path.cwd()
    candidates = [application_root / "app", Path(sysconfig.get_path("purelib")) / "app"]
    package = next((path for path in candidates if (path / "__init__.py").is_file()), None)
    if package is None:
        raise RuntimeError("Application package not found; no files were changed.")
    files = [(path, "app/" + path.relative_to(package).as_posix()) for path in package.rglob("*.py")]
    migration_root = application_root / "alembic"
    if migration_root.is_dir():
        files.extend(
            (path, "alembic/" + path.relative_to(migration_root).as_posix())
            for path in migration_root.rglob("*")
            if path.suffix in {".py", ".mako"}
        )
    files.extend((application_root / name, name) for name in ("main.py", "alembic.ini"))
    hashes = {}
    for path, relative in sorted(files, key=lambda item: item[1]):
        if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
            continue
        data = path.read_bytes()
        hashes[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "sha256_lf": hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
        }
    return {
        "python": sys.version.split()[0],
        "application_directory": str(application_root),
        "package_directory": str(package),
        "files": hashes,
        "packages": dict(sorted((dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions())),
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=True, indent=2))
