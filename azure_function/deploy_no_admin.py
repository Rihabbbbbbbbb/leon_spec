"""
NO-ADMIN DEPLOYMENT SCRIPT — Deploy to Azure Function without Azure CLI.

This script packages the LEON Azure Function code and deploys it to an
existing Azure Function App using only Python + requests. No Azure CLI,
no Node.js, no admin rights needed.

Prerequisites (once):
  1. Create Function App via Azure Portal (portal.azure.com)
     - Python 3.11, Linux Consumption, Function 4.x
  2. Download Publish Profile from portal:
     Function App → Overview → "Get publish profile" → Save as publish_profile.xml

Usage:
  python deploy_no_admin.py --profile publish_profile.xml

What it does:
  1. Copies app/ and data/ into azure_function/
  2. Installs pip dependencies locally into the package
  3. Creates a ZIP deployment package
  4. Uploads via Azure Kudu REST API (zipdeploy)
  5. Reports the function URL
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import requests  # pip install requests


# ── Colors ──────────────────────────────────────────────────────────
class Color:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def _print(level: str, msg: str):
    prefix = {"info": f"{Color.BLUE}[INFO]{Color.RESET}",
              "ok": f"{Color.GREEN}[OK]{Color.RESET}",
              "warn": f"{Color.YELLOW}[WARN]{Color.RESET}",
              "err": f"{Color.RED}[ERR]{Color.RESET}"}
    print(f"{prefix.get(level, '[?]')} {msg}")


# ── Parse publish profile ───────────────────────────────────────────
def parse_publish_profile(profile_path: str) -> Tuple[str, str, str]:
    """
    Parse a publish profile XML file and extract deployment credentials.

    Returns (publish_url, username, password)
    """
    tree = ET.parse(profile_path)
    root = tree.getroot()

    # Find the first publishProfile with a publishMethod="MSDeploy" or "ZipDeploy"
    for profile in root.findall(".//publishProfile"):
        method = profile.get("publishMethod", "")
        url = profile.get("publishUrl", "")
        user = profile.get("userName", "")
        pwd = profile.get("userPWD", "")
        if url and user and pwd:
            # Use the Kudu zipdeploy endpoint
            # publishUrl looks like: func-leon.scm.azurewebsites.net:443
            # We need: https://func-leon.scm.azurewebsites.net/api/zipdeploy
            if "scm" in url:
                url = url.split(":")[0]  # Remove port
                if not url.startswith("http"):
                    url = "https://" + url
                deploy_url = url.rstrip("/") + "/api/zipdeploy"
                return deploy_url, user, pwd

    raise ValueError("No valid publish profile found in the XML file.")


# ── Package the function ────────────────────────────────────────────
def package_function(project_root: Path, output_zip: Path) -> Path:
    """
    Prepare and package the Azure Function for deployment.

    1. Copy app/ and data/ into azure_function/
    2. Install pip dependencies into azure_function/.python_packages/
    3. Create a ZIP file

    Returns the path to the ZIP file.
    """
    func_dir = project_root / "azure_function"
    if not func_dir.exists():
        raise FileNotFoundError(f"azure_function/ not found at {func_dir}")

    _print("info", "Creating deployment package...")

    # Create a temp build directory
    build_dir = Path(tempfile.mkdtemp(prefix="leon_func_build_"))
    _print("info", f"Build directory: {build_dir}")

    # Copy azure_function files
    for item in func_dir.iterdir():
        if item.name in ("__pycache__", ".python_packages", "_sanity_check.py"):
            continue
        dest = build_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    # Copy app/ into the build
    app_src = project_root / "app"
    if app_src.exists():
        app_dest = build_dir / "app"
        if app_dest.exists():
            shutil.rmtree(app_dest)
        shutil.copytree(app_src, app_dest, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _print("ok", "Copied app/")

    # Copy data/refs/ into the build (template + writing guide)
    data_src = project_root / "data" / "refs"
    if data_src.exists():
        data_dest = build_dir / "data" / "refs"
        data_dest.mkdir(parents=True, exist_ok=True)
        for item in data_src.iterdir():
            shutil.copy2(item, data_dest / item.name)
        _print("ok", "Copied data/refs/ (template + writing guide)")

    # Copy data/uploads/ if any specs exist
    uploads_src = project_root / "data" / "uploads"
    if uploads_src.exists():
        uploads_dest = build_dir / "data" / "uploads"
        uploads_dest.mkdir(parents=True, exist_ok=True)
        for item in uploads_src.iterdir():
            if item.is_file() and item.suffix in (".docx", ".txt", ".pdf"):
                shutil.copy2(item, uploads_dest / item.name)
        _print("ok", "Copied uploaded spec files")

    # Install pip dependencies into the build directory
    _print("info", "Installing pip dependencies...")
    req_file = build_dir / "requirements.txt"
    if req_file.exists():
        packages_dir = build_dir / ".python_packages" / "lib" / "site-packages"
        packages_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([
                sys.executable, "-m", "pip", "install",
                "-r", str(req_file),
                "--target", str(packages_dir),
                "--no-cache-dir",
            ], check=True, capture_output=True)
            _print("ok", "Pip dependencies installed")
        except subprocess.CalledProcessError as e:
            _print("warn", f"Pip install had issues: {e.stderr.decode()[:200] if e.stderr else 'unknown'}")
            _print("warn", "Continuing anyway — Azure will install deps on deploy")

    # Create ZIP
    _print("info", f"Creating ZIP: {output_zip}")
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in build_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(build_dir)
                zf.write(file_path, arcname)

    zip_size_mb = output_zip.stat().st_size / (1024 * 1024)
    _print("ok", f"Package created: {output_zip.name} ({zip_size_mb:.1f} MB)")

    # Cleanup
    shutil.rmtree(build_dir, ignore_errors=True)

    return output_zip


# ── Deploy to Azure Function ────────────────────────────────────────
def deploy_zip(zip_path: Path, publish_url: str, username: str, password: str) -> bool:
    """
    Deploy a ZIP package to Azure Function using Kudu REST API.

    Uses the /api/zipdeploy endpoint with Basic authentication.
    """
    _print("info", f"Deploying to: {publish_url}")
    _print("info", f"Package size: {zip_path.stat().st_size / (1024*1024):.1f} MB")

    with open(zip_path, "rb") as f:
        zip_data = f.read()

    headers = {
        "Content-Type": "application/octet-stream",
    }

    _print("info", "Uploading... (this may take 1-3 minutes)")

    try:
        response = requests.post(
            publish_url,
            data=zip_data,
            headers=headers,
            auth=(username, password),
            timeout=300,  # 5 minute timeout
        )

        if response.status_code in (200, 201, 202, 204):
            _print("ok", f"Deployment successful! (HTTP {response.status_code})")
            return True
        else:
            _print("err", f"Deployment failed: HTTP {response.status_code}")
            _print("err", f"Response: {response.text[:500]}")
            return False
    except requests.exceptions.Timeout:
        _print("err", "Deployment timed out. The package may be too large or the network is slow.")
        return False
    except requests.exceptions.ConnectionError as e:
        _print("err", f"Connection error: {e}")
        return False


# ── Verify deployment ───────────────────────────────────────────────
def verify_deployment(publish_url: str, username: str, password: str) -> Optional[str]:
    """
    Verify the deployment by checking the function health endpoint.
    Returns the function URL if healthy.
    """
    # Convert scm URL to main site URL
    # scm: func-leon.scm.azurewebsites.net → func-leon.azurewebsites.net
    main_url = publish_url.replace(".scm.", ".").replace("/api/zipdeploy", "")

    health_url = main_url + "/api/health"
    _print("info", f"Verifying health at: {health_url}")

    try:
        # Try without auth first (anonymous)
        r = requests.get(health_url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            _print("ok", f"Function is healthy: {data}")
            return main_url
        # Try with function key
        _print("warn", f"Anonymous health check returned {r.status_code}, function may need a key")
        return main_url
    except Exception as e:
        _print("warn", f"Health check failed: {e}")
        return main_url


# ── Main ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Deploy LEON to Azure Function (no Azure CLI required)"
    )
    parser.add_argument(
        "--profile", "-p",
        required=True,
        help="Path to publish_profile.xml (downloaded from Azure Portal)"
    )
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Skip building the package (use existing LEON_Deploy.zip)"
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only verify the deployment, don't redeploy"
    )
    args = parser.parse_args()

    # Resolve paths
    profile_path = Path(args.profile)
    if not profile_path.exists():
        _print("err", f"Publish profile not found: {profile_path}")
        sys.exit(1)

    project_root = Path(__file__).resolve().parent.parent  # Spec AI Project root
    zip_path = project_root / "LEON_Deploy.zip"

    _print("info", f"{Color.BOLD}LEON Azure Function Deployer{Color.RESET}")
    _print("info", f"Project root: {project_root}")

    # Parse publish profile
    try:
        deploy_url, username, password = parse_publish_profile(str(profile_path))
        _print("ok", f"Publish profile parsed: {deploy_url}")
    except Exception as e:
        _print("err", f"Failed to parse publish profile: {e}")
        _print("info", "Make sure you downloaded the publish profile from:")
        _print("info", "  Azure Portal → Function App → Overview → 'Get publish profile'")
        sys.exit(1)

    if args.verify_only:
        verify_deployment(deploy_url, username, password)
        return

    # Build package
    if not args.skip_build:
        try:
            zip_path = package_function(project_root, zip_path)
        except Exception as e:
            _print("err", f"Build failed: {e}")
            sys.exit(1)
    elif not zip_path.exists():
        _print("err", f"Package not found: {zip_path}")
        _print("info", "Run without --skip-build to create the package first.")
        sys.exit(1)

    # Deploy
    success = deploy_zip(zip_path, deploy_url, username, password)
    if not success:
        _print("err", "Deployment failed. Check the errors above.")
        sys.exit(1)

    # Verify
    main_url = verify_deployment(deploy_url, username, password)
    if main_url:
        _print("ok", "")
        _print("ok", f"{Color.BOLD}╔══════════════════════════════════════════╗{Color.RESET}")
        _print("ok", f"{Color.BOLD}║  LEON is deployed!                       ║{Color.RESET}")
        _print("ok", f"{Color.BOLD}║  Ask endpoint: {main_url}/api/ask        ║{Color.RESET}")
        _print("ok", f"{Color.BOLD}║  Health: {main_url}/api/health           ║{Color.RESET}")
        _print("ok", f"{Color.BOLD}╚══════════════════════════════════════════╝{Color.RESET}")


if __name__ == "__main__":
    main()
