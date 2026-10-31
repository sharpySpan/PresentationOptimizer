"""Getting LibreOffice without admin rights, for computers with no slide converter.

Downloads the official build from The Document Foundation, checks its SHA-256 against the
checksum published on download.documentfoundation.org, and unpacks it into Showrunner's own
data folder. Nothing is installed system-wide:
  Windows: MSI 'administrative install' (just extracts the files)
  macOS:   copies LibreOffice.app out of the .dmg
  Linux:   unpacks the .deb packages with Python (works on any distro)
"""
from __future__ import annotations

import hashlib
import io
import lzma
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from .system import IS_LINUX, IS_MAC, IS_WINDOWS, NO_WINDOW

BASE = "https://download.documentfoundation.org/libreoffice/stable/"
USER_AGENT = "Showrunner (+https://github.com/sharpySpan/PresentationOptimizer)"


class InstallError(RuntimeError):
    pass


class Cancelled(InstallError):
    pass


def data_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Showrunner"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Showrunner"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "showrunner"


def install_root() -> Path:
    return data_dir() / "libreoffice"


def installed_soffice(root: Path | None = None) -> Path | None:
    """The soffice binary from our own download, if there is one."""
    root = root or install_root()
    if not root.is_dir():
        return None
    if IS_MAC:
        candidate = root / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
        return candidate if candidate.exists() else None
    name = "soffice.exe" if IS_WINDOWS else "soffice"
    for candidate in sorted(root.rglob(name)):
        if candidate.parent.name == "program":
            return candidate
    return None


def latest_version(listing_html: str) -> str:
    versions = set(re.findall(r'href="(\d+(?:\.\d+)+)/"', listing_html))
    if not versions:
        raise InstallError("Couldn't find a LibreOffice version to download")
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


def package_path(version: str, system: str | None = None, machine: str | None = None) -> str:
    """Path below BASE for this computer, e.g. '26.8.2/win/x86_64/LibreOffice_26.8.2_Win_x86-64.msi'."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    arm = machine in ("arm64", "aarch64")
    if system == "win32":
        if arm:
            return f"{version}/win/aarch64/LibreOffice_{version}_Win_aarch64.msi"
        return f"{version}/win/x86_64/LibreOffice_{version}_Win_x86-64.msi"
    if system == "darwin":
        if arm:
            return f"{version}/mac/aarch64/LibreOffice_{version}_MacOS_aarch64.dmg"
        return f"{version}/mac/x86_64/LibreOffice_{version}_MacOS_x86-64.dmg"
    if arm:
        return f"{version}/deb/aarch64/LibreOffice_{version}_Linux_aarch64_deb.tar.gz"
    return f"{version}/deb/x86_64/LibreOffice_{version}_Linux_x86-64_deb.tar.gz"


def _ssl_context():
    """The computer's own certificate store (works behind school proxies that inspect HTTPS),
    falling back to certifi's list. The installed app can't rely on Python's default paths."""
    import ssl

    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - very old OS, or truststore missing
        import certifi

        return ssl.create_default_context(cafile=certifi.where())


def _open(url: str, timeout: float = 30):
    # urllib picks up the system proxy settings (Windows/macOS settings, http(s)_proxy on Linux).
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())


def install(progress=lambda done, total, text: None, cancel: threading.Event | None = None) -> Path:
    """Download, verify and unpack LibreOffice. Returns the path to soffice."""
    cancel = cancel or threading.Event()
    progress(0, 0, "Looking up the latest LibreOffice…")
    with _open(BASE) as response:
        version = latest_version(response.read().decode("utf-8", "replace"))
    rel = package_path(version)
    with _open(BASE + rel + ".sha256") as response:
        expected = response.read().decode().split()[0].lower()

    downloads = data_dir() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    package = downloads / Path(rel).name
    if not (package.exists() and _sha256(package) == expected):
        _download(BASE + rel, package, expected, version, progress, cancel)

    progress(0, 0, "Unpacking LibreOffice…")
    root = install_root()
    staging = root.with_name("libreoffice.new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        if IS_WINDOWS:
            _extract_msi(package, staging)
        elif IS_MAC:
            _extract_dmg(package, staging)
        else:
            _extract_debs(package, staging, cancel)
        if installed_soffice(staging) is None:
            raise InstallError("LibreOffice was unpacked but soffice wasn't found")
        shutil.rmtree(root, ignore_errors=True)
        staging.rename(root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    package.unlink(missing_ok=True)  # ~350 MB we no longer need
    return installed_soffice(root)


DOWNLOAD_ATTEMPTS = 6


def _download(url: str, dest: Path, expected: str, version: str, progress, cancel: threading.Event):
    """download.documentfoundation.org sends every request to a random mirror. Some mirrors are
    slow, break off, or serve an incomplete certificate chain (Linux can't complete those, unlike
    Windows/macOS), so each attempt simply asks for another mirror. Certificates are always checked."""
    errors = []
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            _download_once(url, dest, expected, version, progress, cancel)
            return
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - network trouble of any kind: try another mirror
            errors.append(str(getattr(exc, "reason", exc)) or exc.__class__.__name__)
            progress(0, 0, f"Download interrupted, trying another server ({attempt + 2}/{DOWNLOAD_ATTEMPTS})…")
    raise InstallError(f"Couldn't download LibreOffice ({errors[-1]})")


def _download_once(url: str, dest: Path, expected: str, version: str, progress, cancel: threading.Event):
    partial = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    try:
        with _open(url, timeout=60) as response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel.is_set():
                    raise Cancelled("Download cancelled")
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(done, total, f"Downloading LibreOffice {version}…")
        if digest.hexdigest() != expected:
            raise InstallError("Download was damaged (checksum mismatch)")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(dest)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_msi(msi: Path, target: Path):
    # /a = administrative install: unpacks the files, needs no admin rights, touches nothing else.
    log = target.parent / "msiexec.log"
    result = subprocess.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={target}", "/L*", str(log)],
                            creationflags=NO_WINDOW, timeout=1800)
    if result.returncode != 0:
        raise InstallError(f"Unpacking the LibreOffice installer failed (msiexec code {result.returncode})")
    log.unlink(missing_ok=True)


def _extract_dmg(dmg: Path, target: Path):
    mount = Path(tempfile.mkdtemp(prefix="showrunner-lo-"))
    attach = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount),
                             str(dmg)], input="Y\n", capture_output=True, text=True, timeout=600)
    if attach.returncode != 0:
        raise InstallError("Couldn't open the LibreOffice disk image: " + attach.stderr.strip())
    try:
        app = next(mount.glob("*.app"), None)
        if app is None:
            raise InstallError("LibreOffice.app not found in the disk image")
        subprocess.run(["ditto", str(app), str(target / "LibreOffice.app")], check=True, timeout=1800)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True, timeout=120)
        shutil.rmtree(mount, ignore_errors=True)


def _extract_debs(archive: Path, target: Path, cancel: threading.Event):
    with tarfile.open(archive) as outer:
        debs = [m for m in outer.getmembers() if m.name.endswith(".deb") and "/DEBS/" in m.name]
        if not debs:
            raise InstallError("No packages found in the LibreOffice download")
        for member in debs:
            if cancel.is_set():
                raise Cancelled("Cancelled")
            extract_deb(outer.extractfile(member).read(), target)


def extract_deb(deb: bytes, target: Path):
    """Unpack a .deb's data archive (a .deb is an 'ar' archive holding data.tar.*)."""
    if not deb.startswith(b"!<arch>\n"):
        raise InstallError("Not a .deb package")
    pos = 8
    while pos + 60 <= len(deb):
        header = deb[pos:pos + 60]
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode().strip())
        body = deb[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
        if not name.startswith("data.tar"):
            continue
        if name.endswith(".xz"):
            body = lzma.decompress(body)
        elif name.endswith(".zst"):
            raise InstallError("Unsupported package compression (zstd)")
        with tarfile.open(fileobj=io.BytesIO(body)) as data:
            data.extractall(target, filter="tar" if hasattr(tarfile, "data_filter") else None)
        return
    raise InstallError("Package has no data archive")
