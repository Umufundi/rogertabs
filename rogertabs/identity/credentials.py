"""Read a secret from the operating system's own store, never from a dotfile.

    Windows   Credential Manager, generic credential   (advapi32 CredReadW)
    macOS     login keychain, generic password         (`security`)
    Linux     Secret Service via libsecret             (`secret-tool`)

Every path returns None on any failure. A missing key is an ordinary state:
Jev is optional, and the meeting carries on by asking the human instead.

On Windows the key can be stored once with the built-in tool:

    cmdkey /generic:"TypeSafe API Key" /user:typesafe /pass:<key>
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _windows(service: str) -> str | None:
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    CRED_TYPE_GENERIC = 1
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    pointer = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(service, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return None
    try:
        cred = pointer.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        advapi32.CredFree(pointer)
    return decode_blob(blob)


def decode_blob(blob: bytes) -> str | None:
    """Credential Manager stores bytes, not text.

    `cmdkey` and the Control Panel write UTF-16LE; some other tools write
    UTF-8. "Does it decode as UTF-16" is no test, since any even-length byte
    string does (b"sk-abc" becomes three CJK characters). API keys are ASCII,
    and ASCII in UTF-16LE always has a zero high byte, so test for that.
    """
    if not blob:
        return None
    if len(blob) % 2 == 0 and not any(blob[1::2]):
        return blob.decode("utf-16-le").strip() or None
    try:
        return blob.decode("utf-8").strip() or None
    except UnicodeDecodeError:
        return None


def _run(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def lookup(service: str, *, platform: str | None = None) -> str | None:
    """The secret stored under `service`, or None."""
    platform = platform or sys.platform
    try:
        if platform == "win32":
            return _windows(service)
        if platform == "darwin":
            return _run(["security", "find-generic-password", "-s", service, "-w"])
        if shutil.which("secret-tool"):
            return _run(["secret-tool", "lookup", "service", service])
    except Exception:
        return None
    return None


def _write_windows(service: str, secret: str, account: str) -> bool:
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    blob = secret.encode("utf-16-le")  # what cmdkey writes, so every reader agrees
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL()
    cred.Type = 1  # CRED_TYPE_GENERIC
    cred.TargetName = service
    cred.UserName = account
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    return bool(advapi32.CredWriteW(ctypes.byref(cred), 0))


def store(service: str, secret: str, *, platform: str | None = None, account: str = "rogertabs") -> bool:
    """Save `secret` under `service` in the OS store. False on any failure."""
    platform = platform or sys.platform
    try:
        if platform == "win32":
            return _write_windows(service, secret, account)
        if platform == "darwin":
            argv = ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", secret]
            return subprocess.run(argv, capture_output=True, timeout=5).returncode == 0
        if shutil.which("secret-tool"):
            argv = ["secret-tool", "store", f"--label={service}", "service", service]
            return subprocess.run(argv, input=secret, text=True, capture_output=True,
                                  timeout=5).returncode == 0
    except Exception:
        return False
    return False


def lookup_migrating(
    service: str, legacy_service: str | None = None, *, platform: str | None = None
) -> str | None:
    """Read `service`; if it is absent, fall back to the name a previous version
    used and copy the secret forward under the new name, so the next read finds
    it there. The old entry is left in place: a stored credential is never lost,
    and an older install on the same machine keeps working."""
    extra = {"platform": platform} if platform else {}
    value = lookup(service, **extra)
    if value or not legacy_service or legacy_service == service:
        return value
    value = lookup(legacy_service, **extra)
    if value:
        store(service, value, **extra)
    return value


def where(platform: str | None = None) -> str:
    """How to store the key on this OS, for `rogertabs doctor`."""
    platform = platform or sys.platform
    if platform == "win32":
        return 'cmdkey /generic:"TypeSafe API Key" /user:typesafe /pass:<key>'
    if platform == "darwin":
        return 'security add-generic-password -s "TypeSafe API Key" -a typesafe -w <key>'
    return 'secret-tool store --label="TypeSafe API Key" service "TypeSafe API Key"'
