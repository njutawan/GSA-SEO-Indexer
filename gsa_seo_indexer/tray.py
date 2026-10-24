"""System-tray support for the Tkinter GUI - pure stdlib (ctypes + Win32).

The original GSA SEO Indexer has a "min to tray" option (config key of
the same name): with it on, minimising the window hides it to the
notification area instead of the taskbar.  The clone's constraint is no
third-party packages, and Tkinter has no tray API, so the icon is
created directly with ``Shell_NotifyIconW`` through ctypes.

How it fits together:

* A hidden message-sink window is registered on Tk's own thread, so its
  window procedure runs inside Tk's normal message pump - no extra
  thread, and the restore/quit callbacks fire on the GUI thread.
* Left-click on the icon restores the window; right-click opens a small
  menu (Restore / Quit) via ``TrackPopupMenu`` with ``TPM_RETURNCMD``.
* The icon re-adds itself when Explorer restarts (``TaskbarCreated``).
* Windows-only; ``TrayIcon.__init__`` raises ``OSError`` elsewhere so the
  GUI can fall back to a normal minimise without crashing.
"""

from __future__ import annotations

import ctypes
import sys
import ctypes.wintypes as wintypes

LRESULT = ctypes.c_ssize_t

WM_APP = 0x8000
WM_TRAYICON = WM_APP          # callback message from the tray icon
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_SHARED = 0x8000

MF_STRING = 0x0
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
TPM_RETURNCMD = 0x0100

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", ctypes.c_size_t),          # union uTimeout/uVersion
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", wintypes.BYTE * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


_ID_RESTORE, _ID_QUIT = 1, 2
_CLASS_NAME = "GsaIndexerTraySink"


class TrayIcon:
    """One notification-area icon wired to two GUI callbacks."""

    def __init__(self, on_activate, on_quit, tip: str = "GSA SEO Indexer (clone)"):
        if sys.platform != "win32":
            raise OSError("tray icon is only supported on Windows")
        self._on_activate = on_activate
        self._on_quit = on_quit
        self.active = False

        user32 = ctypes.windll.user32
        self.user32 = user32
        self.shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        self.kernel32 = kernel32

        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        # parameter 2 is LPCWSTR in the header, but a stock icon is passed
        # as MAKEINTRESOURCE(id) - an integer cast to a pointer value
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, ctypes.c_void_p, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self.shell32.Shell_NotifyIconW.argtypes = [wintypes.UINT, ctypes.POINTER(NOTIFYICONDATAW)]

        self.hinst = kernel32.GetModuleHandleW(None)
        self._taskbar_msg = user32.RegisterWindowMessageW("TaskbarCreated")

        # the wndproc reference must outlive the window class
        self._wndproc = WNDPROC(self._proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self.hinst
        wc.lpszClassName = _CLASS_NAME
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())

        self.hwnd = user32.CreateWindowExW(
            0, _CLASS_NAME, "gsa-indexer-tray", 0, 0, 0, 0, 0,
            None, None, self.hinst, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        # stock application icon - zero assets, guaranteed present
        self.hicon = user32.LoadImageW(None, IDI_APPLICATION, IMAGE_ICON, 0, 0, LR_SHARED)
        self.tip = tip[:127]

    # -- Win32 plumbing ------------------------------------------------------
    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == self._taskbar_msg and self.active:
            # Explorer restarted: every tray icon was lost, re-add ours
            self._notify(NIM_ADD)
        elif msg == WM_TRAYICON:
            if lparam == WM_LBUTTONUP:
                self._safe(self._on_activate)
            elif lparam == WM_RBUTTONUP:
                self._menu()
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _safe(self, fn):
        try:
            fn()
        except Exception:  # noqa: BLE001 - a tray click must never kill the pump
            pass

    def _menu(self):
        u = self.user32
        hmenu = u.CreatePopupMenu()
        u.AppendMenuW(hmenu, MF_STRING, _ID_RESTORE, "Restore")
        u.AppendMenuW(hmenu, MF_STRING, _ID_QUIT, "Quit")
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(self.hwnd)
        cmd = u.TrackPopupMenu(
            hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
            pt.x, pt.y, 0, self.hwnd, None,
        )
        u.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        u.DestroyMenu(hmenu)
        if cmd == _ID_RESTORE:
            self._safe(self._on_activate)
        elif cmd == _ID_QUIT:
            self._safe(self._on_quit)

    def _notify(self, code: int) -> bool:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tip
        return bool(self.shell32.Shell_NotifyIconW(code, ctypes.byref(nid)))

    # -- public API ----------------------------------------------------------
    def show(self) -> bool:
        """Add the icon; returns True when it is visible."""
        self.active = self._notify(NIM_ADD)
        return self.active

    def hide(self) -> None:
        if self.active:
            self._notify(NIM_DELETE)
            self.active = False

    def close(self) -> None:
        self.hide()
        if getattr(self, "hwnd", None):
            self.user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        # Register/UnregisterClass both live in user32, not kernel32
        self.user32.UnregisterClassW(_CLASS_NAME, self.hinst)
