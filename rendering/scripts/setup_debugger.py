"""Install the debugger into a project-local environment, without changing the app."""
from pathlib import Path
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".venv"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if not PYTHON.exists():
    venv.EnvBuilder(with_pip=True).create(ENV)
subprocess.run([str(PYTHON), "-m", "pip", "install", "debugpy==1.8.22"], check=True)
print(f"Debugger ready: {PYTHON}")
