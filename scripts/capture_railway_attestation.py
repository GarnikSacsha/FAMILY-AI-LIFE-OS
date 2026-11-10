"""Read production hashes over SSH; never import or start the remote app."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "release-validation" / "runtime-attestation.json"
CLI = Path.home() / "AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe"


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit("An attestation already exists; refusing to overwrite it.")
    source = (ROOT / "scripts/attest_runtime.py").read_bytes()
    command = "python -B -c 'exec(bytes.fromhex(\"" + source.hex() + "\"))'"
    args = [
        str(CLI),
        "ssh",
        "--project",
        "4f4d4272-acd8-4a4b-8664-80b6655c5d59",
        "--service",
        "80edccfd-c32b-451b-8c58-c9b339374267",
        "--environment",
        "9dcd22fc-1740-44c6-9635-57c3f894fd60",
        command,
    ]
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=60)  # noqa: S603
    except subprocess.TimeoutExpired:
        raise SystemExit("Railway SSH timed out; no attestation was saved and no service was changed.") from None
    if result.returncode:
        raise SystemExit("Railway SSH failed; no attestation was saved and no service was changed.")
    data = json.loads(result.stdout)
    if not isinstance(data.get("files"), dict) or not isinstance(data.get("packages"), dict):
        raise SystemExit("Unexpected attestation; refusing to save it.")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    print(f"Saved {len(data['files'])} source hashes and {len(data['packages'])} package versions to {OUTPUT}")


if __name__ == "__main__":
    main()
