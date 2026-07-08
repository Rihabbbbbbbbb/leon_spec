"""Simple test runner for the LEON Spec Validator test suite."""
import sys
import subprocess
from pathlib import Path

project_root = Path(__file__).resolve().parent
python_exe = project_root / ".venv" / "Scripts" / "python.exe"

result = subprocess.run(
    [str(python_exe), "-m", "pytest", "tests/", "-v", "--tb=short"],
    cwd=str(project_root),
    capture_output=True,
    text=True,
)

print(result.stdout)
if result.stderr:
    print(result.stderr, file=sys.stderr)

sys.exit(result.returncode)
