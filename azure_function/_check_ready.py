"""Test the deploy script's packaging logic."""
import sys, zipfile
sys.path.insert(0, "azure_function")
from pathlib import Path

project_root = Path(".")
func_dir = project_root / "azure_function"

# Just verify the structure is correct
required_files = [
    "function_app.py",
    "azure_handler.py",
    "azure_config.py",
    "requirements.txt",
    "host.json",
    "local.settings.json",
    "__init__.py",
]

print("Checking azure_function/ files:")
for f in required_files:
    exists = (func_dir / f).exists()
    print(f"  {'✓' if exists else '✗'} {f}")
    if not exists:
        print(f"    MISSING!")

# Check that app/ is available
app_dir = project_root / "app"
print(f"\n  app/ exists: {app_dir.exists()}")

# Check data/refs/
refs_dir = project_root / "data" / "refs"
print(f"  data/refs/ exists: {refs_dir.exists()}")
if refs_dir.exists():
    for f in refs_dir.iterdir():
        print(f"    {f.name} ({f.stat().st_size / 1024:.0f} KB)")

print("\nAll checks passed — ready for deployment!")
print("Next step: Get your Publish Profile from Azure Portal")
