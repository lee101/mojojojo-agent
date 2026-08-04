from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UNIX_INSTALLER = ROOT / "install" / "install.sh"
WINDOWS_INSTALLER = ROOT / "install" / "install.ps1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unix_assets(root: Path, *, extra_entry: bool = False) -> Path:
    assets = root / "assets"
    assets.mkdir()
    payload = root / "mjj"
    payload.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then echo 'mjj test'; exit 0; fi\n"
        "exit 2\n"
    )
    payload.chmod(0o755)
    archive = assets / "mjj-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname="mjj")
        if extra_entry:
            extra = root / "extra"
            extra.write_text("unexpected")
            bundle.add(extra, arcname="extra")
    (assets / "SHA256SUMS").write_text(f"{_sha256(archive)}  {archive.name}\n")
    return assets


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_unix_installer_verifies_smokes_and_replaces_atomically(tmp_path: Path) -> None:
    assets = _unix_assets(tmp_path)
    destination = tmp_path / "bin"
    destination.mkdir()
    (destination / "mjj").write_text("old install")

    result = subprocess.run(
        [
            "/bin/sh",
            str(UNIX_INSTALLER),
            "--version",
            "0.3.0",
            "--base-url",
            assets.as_uri(),
            "--install-dir",
            str(destination),
        ],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "MJJ_UNAME_S": "Linux",
            "MJJ_UNAME_M": "x86_64",
        },
    )

    assert result.returncode == 0, result.stderr
    installed = destination / "mjj"
    assert installed.is_file()
    assert subprocess.check_output([installed, "--version"], text=True).strip() == "mjj test"
    assert not list(destination.glob(".mjj.install.*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_unix_installer_rejects_extra_archive_entries(tmp_path: Path) -> None:
    assets = _unix_assets(tmp_path, extra_entry=True)
    destination = tmp_path / "bin"
    destination.mkdir()
    installed = destination / "mjj"
    installed.write_text("existing")

    result = subprocess.run(
        ["/bin/sh", str(UNIX_INSTALLER)],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "MJJ_UNAME_S": "Linux",
            "MJJ_UNAME_M": "x86_64",
            "MJJ_BASE_URL": assets.as_uri(),
            "MJJ_INSTALL_DIR": str(destination),
        },
    )

    assert result.returncode != 0
    assert "exactly one root file" in result.stderr
    assert installed.read_text() == "existing"


def test_installer_sources_expose_matching_configuration() -> None:
    unix = UNIX_INSTALLER.read_text()
    windows = WINDOWS_INSTALLER.read_text()

    for setting in ("MJJ_VERSION", "MJJ_INSTALL_DIR", "MJJ_REPO", "MJJ_BASE_URL"):
        assert setting in unix
        assert setting in windows
    assert "RuntimeInformation,mscorlib" in windows
    assert "PROCESSOR_ARCHITECTURE" in windows
    assert "Get-FileHash -Algorithm SHA256" in windows
    assert "failed its smoke test" in unix
    assert "failed its smoke test" in windows


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_unix_installer_help_does_not_require_home() -> None:
    environment = dict(os.environ)
    environment.pop("HOME", None)
    result = subprocess.run(
        ["/bin/sh", str(UNIX_INSTALLER), "--help"],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0
    assert "--install-dir" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer")
def test_unix_installer_rejects_malformed_repository_before_download() -> None:
    result = subprocess.run(
        ["/bin/sh", str(UNIX_INSTALLER), "--repo", "owner/repo/extra"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "OWNER/REPO" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell installer")
def test_windows_installer_verifies_and_installs_local_asset(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    source = assets / "mjj.exe"
    curl = Path(os.environ["SystemRoot"]) / "System32" / "curl.exe"
    assert curl.is_file()
    shutil.copy2(curl, source)
    archive = assets / "mjj-windows-x86_64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(source, "mjj.exe")
    (assets / "SHA256SUMS").write_text(
        f"{_sha256(archive)}  {archive.name}\n", encoding="ascii"
    )
    destination = tmp_path / "bin"
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_INSTALLER),
            "-BaseUrl",
            assets.as_uri(),
            "-InstallDir",
            str(destination),
            "-NoPathUpdate",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    installed = destination / "mjj.exe"
    assert installed.is_file()
    subprocess.run([installed, "--version"], check=True, capture_output=True)
    assert not list(destination.glob(".mjj.install.*"))
