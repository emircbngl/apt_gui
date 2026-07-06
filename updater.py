"""Self-update over GitHub for the Thorlabs APT Stage Controller.

Design carried over from the author's Blender Optics Simulator updater:
  * throttled background check (at most once a day, never blocks startup),
  * explicit version comparison against the repo's version.py on main,
  * staged apply — the running process is never modified; downloaded files
    take effect on the next launch,
  * user data (config.json) is never touched by an update,
  * every network/parse failure degrades to "no update info", never an error.

Three install paths, picked automatically:
  git checkout  -> `git pull --ff-only`
  plain source  -> download the repo zip and copy the app files over this dir
  frozen .exe   -> download the zip to a temp folder and show its path;
                   self-replacement is not attempted (rebuild with
                   build_windows.bat or run from source)
"""

import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

REPO = "emircbngl/apt_gui"
BRANCH = "main"
RAW_VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/version.py"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
REPO_PAGE_URL = f"https://github.com/{REPO}"

CHECK_INTERVAL_S = 24 * 3600   # throttle: at most one automatic check per day

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# User data is the invariant an update must never touch — express that directly
# as a denylist. The copy set itself is derived from the downloaded zip (every
# top-level *.py plus the extras below), so a future release that adds a new
# module updates cleanly on already-shipped clients.
PROTECTED_FILES = ("config.json",)
UPDATABLE_EXTRAS = (
    "README.md", "requirements.txt", "INSTALL.bat", "RUN.bat",
    "build_windows.bat",
)


def local_version():
    try:
        from version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def _ver_tuple(v):
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(remote, local):
    return _ver_tuple(remote) > _ver_tuple(local)


def _urlopen(url, timeout):
    """urlopen with a certifi fallback: python.org's macOS Python does not use
    the system certificate store, so the default SSL context fails with
    CERTIFICATE_VERIFY_FAILED until certifi is wired in. Verification is NEVER
    disabled — the fallback just supplies a proper CA bundle."""
    req = urllib.request.Request(url, headers={"User-Agent": "apt-gui-updater"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
            raise
        import certifi  # raises ImportError -> caller treats as failure
        ctx = ssl.create_default_context(cafile=certifi.where())
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def fetch_latest(timeout=10):
    """Version string published on GitHub main, or None on ANY failure."""
    try:
        with _urlopen(RAW_VERSION_URL, timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        return m.group(1) if m else None
    except Exception:
        return None


def should_check(last_check_epoch):
    return (time.time() - float(last_check_epoch or 0)) >= CHECK_INTERVAL_S


def is_frozen():
    """True when running as a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def is_git_checkout():
    return os.path.isdir(os.path.join(APP_DIR, ".git"))


def update_via_git(timeout=60):
    """Fast-forward pull in the app directory. Returns (ok, output).
    GIT_TERMINAL_PROMPT=0 makes an auth-requiring remote FAIL fast instead of
    hanging on a credential prompt no one can see from the GUI."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(["git", "-C", APP_DIR, "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


def download_zip(dest_dir=None, timeout=120):
    """Download the repo zip. Returns the saved path. Raises on failure —
    callers present the error, this is an explicit user action. Call
    cleanup_download() when the zip is no longer needed."""
    dest_dir = dest_dir or tempfile.mkdtemp(prefix="apt_gui_update_")
    path = os.path.join(dest_dir, f"apt_gui-{BRANCH}.zip")
    with _urlopen(ZIP_URL, timeout) as resp, open(path, "wb") as f:
        shutil.copyfileobj(resp, f)
    return path


def cleanup_download(zip_path):
    """Remove a download_zip() result and its temp directory. Never raises."""
    try:
        d = os.path.dirname(zip_path)
        if os.path.basename(d).startswith("apt_gui_update_"):
            shutil.rmtree(d, ignore_errors=True)
        elif os.path.exists(zip_path):
            os.remove(zip_path)
    except Exception:
        pass


def apply_zip(zip_path, app_dir=APP_DIR):
    """Extract the repo zip and copy the app files over app_dir.

    Copy set = every top-level *.py in the zip + UPDATABLE_EXTRAS, minus
    PROTECTED_FILES (user data). Each file is staged next to its target and
    swapped in with os.replace (atomic per file), and version.py goes LAST —
    so if the update dies midway, the local version still reads as old and
    the updater re-offers the update instead of reporting up-to-date.

    The running process keeps executing its already-loaded code — the new
    version takes effect on the next launch. Returns the list of files it
    replaced."""
    updated = []
    with tempfile.TemporaryDirectory(prefix="apt_gui_apply_") as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        roots = [d for d in os.listdir(tmp)
                 if os.path.isdir(os.path.join(tmp, d))]
        if not roots:
            return updated
        src_root = os.path.join(tmp, roots[0])

        names = [n for n in os.listdir(src_root)
                 if n.endswith(".py") and os.path.isfile(os.path.join(src_root, n))]
        names += [n for n in UPDATABLE_EXTRAS
                  if os.path.isfile(os.path.join(src_root, n))]
        names = [n for n in names if n not in PROTECTED_FILES]
        # version.py last: it is the "update complete" marker.
        names.sort(key=lambda n: (n == "version.py", n))

        for name in names:
            src = os.path.join(src_root, name)
            dst = os.path.join(app_dir, name)
            staged = dst + ".new"
            shutil.copy2(src, staged)
            os.replace(staged, dst)
            updated.append(name)
    return updated
