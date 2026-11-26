"""Install local dependencies if needed, build the UI and start the loopback server."""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
FRONTEND = ROOT / "frontend"


def run(args, cwd=ROOT):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def install(marker, inputs, args, cwd=ROOT):
    signature = hashlib.sha256(b"".join(path.read_bytes() for path in inputs)).hexdigest()
    if not marker.exists() or marker.read_text() != signature:
        run(args, cwd)
        signature = hashlib.sha256(b"".join(path.read_bytes() for path in inputs)).hexdigest()
        marker.write_text(signature)


def main():
    if not PYTHON.exists():
        run([sys.executable, "-m", "venv", VENV])
    install(VENV / "buddy-dependencies", [ROOT / "requirements.txt"],
            [PYTHON, "-m", "pip", "install", "-r", "requirements.txt"])
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("Install Node.js 22 or newer and make npm available in PATH.")
    package = FRONTEND / "package.json"
    lock = FRONTEND / "package-lock.json"
    files = [package, lock] if lock.exists() else [package]
    marker = FRONTEND / "node_modules" / ".buddy-dependencies"
    install(marker, files, [npm, "ci" if lock.exists() else "install"], FRONTEND)
    run([npm, "run", "build"], FRONTEND)
    try:
        run([PYTHON, "-m", "backend.launch"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
