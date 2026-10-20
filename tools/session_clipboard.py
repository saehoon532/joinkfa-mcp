"""One-time Windows UI transfer, with clipboard history/cloud sync excluded.

Only reads the verified, fixed local session path. Never prints its contents.
The caller must clear the clipboard immediately after reading it for the
approved Render Secret File form. This is not an MCP tool or server endpoint.
"""
import ctypes as c
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kfa_lookup import Session

u, k = c.windll.user32, c.windll.kernel32
k.GlobalAlloc.argtypes = [c.c_uint, c.c_size_t]
k.GlobalAlloc.restype = c.c_void_p
k.GlobalLock.argtypes = [c.c_void_p]
k.GlobalLock.restype = c.c_void_p
k.GlobalUnlock.argtypes = [c.c_void_p]
k.GlobalFree.argtypes = [c.c_void_p]
u.SetClipboardData.argtypes = [c.c_uint, c.c_void_p]
u.SetClipboardData.restype = c.c_void_p
u.RegisterClipboardFormatW.argtypes = [c.c_wchar_p]
u.RegisterClipboardFormatW.restype = c.c_uint


def put(fmt, data):
    handle = k.GlobalAlloc(2, len(data))
    if not handle:
        raise RuntimeError("CLIPBOARD_ALLOCATION_FAILED")
    ptr = k.GlobalLock(handle)
    if not ptr:
        k.GlobalFree(handle)
        raise RuntimeError("CLIPBOARD_LOCK_FAILED")
    c.memmove(ptr, data, len(data))
    k.GlobalUnlock(handle)
    if not u.SetClipboardData(fmt, handle):
        k.GlobalFree(handle)
        raise RuntimeError("CLIPBOARD_SET_FAILED")


def main():
    path = Path(__file__).resolve().parents[1] / ".secrets/kfa-session.json"
    clear = sys.argv[1:] == ["--clear"]
    if not clear:
        Session.load(str(path))
        data = (path.read_text(encoding="utf-8") + "\0").encode("utf-16le")
    if not u.OpenClipboard(None):
        raise RuntimeError("CLIPBOARD_BUSY")
    try:
        if not u.EmptyClipboard():
            raise RuntimeError("CLIPBOARD_EMPTY_FAILED")
        if not clear:
            for name in ("ExcludeClipboardContentFromMonitorProcessing",
                         "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                fmt = u.RegisterClipboardFormatW(name)
                if not fmt:
                    raise RuntimeError("CLIPBOARD_FORMAT_FAILED")
                put(fmt, b"\0" * 4)
            put(13, data)
    except Exception:
        u.EmptyClipboard()
        raise
    finally:
        u.CloseClipboard()
    print("CLIPBOARD_CLEARED" if clear else "SECURE_TRANSFER_READY")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("TRANSFER_FAILED_NO_SECRET_OUTPUT")
        sys.exit(1)
