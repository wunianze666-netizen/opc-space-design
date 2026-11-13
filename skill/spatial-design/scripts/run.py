"""Use the skill or project virtual environment when it is present."""

from pathlib import Path
import subprocess
import sys


def main():
    script = Path(__file__).resolve()
    root = script.parents[1]
    bases = [root]
    if root.parent.name == "skill":
        bases.append(root.parent.parent)
    for base in bases:
        env = base / ".venv"
        for relative in ("Scripts/python.exe", "bin/python"):
            python = env / relative
            if python.is_file():
                return subprocess.call([str(python), str(script.with_name("opc.py")), *sys.argv[1:]])
    return subprocess.call([sys.executable, str(script.with_name("opc.py")), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
