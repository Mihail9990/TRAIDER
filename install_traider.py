#!/usr/bin/env python3
"""Download and install TRAIDER using only the Python standard library.

This bootstrap script is intended for environments such as Pydroid 3 where
Git is not necessarily available.  It downloads a GitHub source archive and
then installs the project in editable mode with the current Python interpreter.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


DEFAULT_REPOSITORY = "Mihail9990/TRAIDER"
DEFAULT_REF = "main"


def archive_url(repository: str, ref: str) -> str:
    """Return the GitHub archive URL for a repository and ref."""
    encoded_ref = urllib.parse.quote(ref, safe="/")
    return f"https://github.com/{repository}/archive/refs/heads/{encoded_ref}.zip"


def _safe_extract(archive: Path, destination: Path) -> Path:
    """Extract a GitHub archive and return its single root directory."""
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if not members:
            raise RuntimeError("The downloaded archive is empty")

        roots: set[str] = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("The downloaded archive contains an unsafe path")
            if path.parts:
                roots.add(path.parts[0])

        if len(roots) != 1:
            raise RuntimeError("The downloaded archive has an unexpected layout")
        bundle.extractall(destination)

    return destination / roots.pop()


def install(repository: str, ref: str, destination: Path) -> None:
    """Download *ref*, copy it to *destination*, and install the package."""
    url = archive_url(repository, ref)
    destination = destination.expanduser().resolve()
    print(f"Downloading {url}")

    with tempfile.TemporaryDirectory(prefix="traider-install-") as temp_name:
        temp = Path(temp_name)
        archive = temp / "traider.zip"
        request = urllib.request.Request(url, headers={"User-Agent": "TRAIDER-installer"})
        with urllib.request.urlopen(request, timeout=60) as response:
            archive.write_bytes(response.read())

        source = _safe_extract(archive, temp / "source")
        if not (source / "pyproject.toml").is_file():
            raise RuntimeError(f"No pyproject.toml found in GitHub ref {ref!r}")
        if destination.exists():
            if not destination.is_dir():
                raise RuntimeError(f"Installation path is not a directory: {destination}")
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)

    print(f"Installing from {destination}")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(destination)],
        check=True,
    )
    print("TRAIDER installation completed successfully.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY, help="GitHub owner/repository")
    parser.add_argument("--ref", default=DEFAULT_REF, help="Git branch to install")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.home() / "TRAIDER",
        help="directory for the source checkout (default: ~/TRAIDER)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        install(args.repository, args.ref, args.destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
