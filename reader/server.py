"""Local server for the FMMT 27.

    python server.py            then open http://127.0.0.1:8627

GET /                 the tool
GET /api/status       is the game running, which build, in-game date
GET /api/progress     how far the current load has got
GET /api/players      every player with minutes (?scope=competitive|league|all|cups|continental,
                      ?all=1 to also include players with no minutes yet (profile only),
                      ?rescan=1 to rebuild the player list)
POST /api/open        open one of the page's outside links in the default browser
POST /api/open-in-fm  copy a player's name to the clipboard and bring the game window to the front

Listens on 127.0.0.1 only. Reads the game's memory read-only.
"""
import json
import os
import random
import struct
import sys
import threading
import time
import traceback
import webbrowser
import ctypes
from ctypes import wintypes as wt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from export import to_row
from fm27 import SCOPES, Game
from sanity import KNOWN_BUILDS, check

PORT = 8627
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = "FMMT 27.html"
ICON = "FMMT 27.ico"


def bundled(name: str) -> str:
    """A file bundled inside the exe, else next to the reader folder."""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), name)
    return os.path.join(HERE, "..", name)


def page_path() -> str:
    return bundled(PAGE)


class Live:
    """One attached Game, re-created when the game restarts or the save changes."""

    def __init__(self):
        self.g: Game | None = None
        self.lock = threading.Lock()
        self.progress: dict = {}  # read without the lock while a load holds it

    def _attach(self):
        if self.g:
            try:
                self.g.close()
            except OSError:
                pass
        self.g = Game()

    def _healthy(self) -> bool:
        """Cached player addresses still point at players (same process, same save)?"""
        g = self.g
        if not g or g._players is None:
            return False
        try:
            if g.p.read(g.build.module.base, 2) != b"MZ":
                return False
            sample = random.sample(g._players, min(200, len(g._players)))
            ok = sum(g.p.u64(a) in (g.player_vt, g.staff_vt) and g._live(a, g.shift(a)) for a in sample)
            return ok >= 0.98 * len(sample)
        except (OSError, struct.error, TypeError):
            return False

    def status(self):
        with self.lock:
            try:
                if not self.g:
                    self._attach()
                return {"ok": True, "pid": self.g.p.pid, "build": self.g.build.id,
                        "build_known": self.g.build.id in KNOWN_BUILDS}
            except Exception as e:  # game not running, access denied, unknown build
                self.g = None
                return {"ok": False, "error": str(e)}

    def _read_progress(self, done: int, total: int):
        self.progress = {"phase": "read", "done": done, "total": total}

    def players(self, scope: str, rescan: bool, everyone: bool = False):
        with self.lock:
            t0 = time.time()
            for attempt in range(2):
                try:
                    if not self.g:
                        self._attach()
                    self.progress = {"phase": "scan"}
                    if rescan or not self._healthy():
                        self.g.players(refresh=True)
                    today, raw = self.g.read_all(scope, require_minutes=not everyone, progress=self._read_progress)
                    self.progress = {}
                    rows = [to_row(r) for r in raw]
                    rows.sort(key=lambda r: not r.get("Mins"))  # full rows first: the page reads columns from row 0
                    played = sum(1 for r in rows if r.get("Mins"))
                    warnings = check(rows, self.g.build.id, self.g.unlisted)
                    try:
                        mine = self.g.my_clubs()
                    except Exception:
                        traceback.print_exc()
                        mine = []
                    return {"ok": True, "build": self.g.build.id, "warnings": warnings, "my_clubs": mine,
                            "date": today.isoformat() if today else None,
                            "scope": scope, "players": len(self.g.players()), "count": len(rows),
                            "played": played, "everyone": everyone,
                            "seconds": round(time.time() - t0, 1), "rows": rows}
                except Exception as e:
                    traceback.print_exc()
                    self.progress = {}
                    self.g = None  # re-attach once (game restarted?)
                    if attempt:
                        return {"ok": False, "error": str(e)}


LIVE = Live()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # allow the page when opened straight from disk (file:// sends Origin: null), nothing else
        if self.headers.get("Origin") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            try:
                with open(page_path(), "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"The tool page was not found next to the reader folder", "text/plain")
        elif u.path == "/favicon.ico":
            try:
                with open(bundled(ICON), "rb") as fh:
                    self._send(200, fh.read(), "image/x-icon")
            except FileNotFoundError:
                self._send(404, b"not found", "text/plain")
        elif u.path == "/api/status":
            self._json(LIVE.status())
        elif u.path == "/api/progress":
            self._json(LIVE.progress)
        elif u.path == "/api/players":
            scope = q.get("scope", ["competitive"])[0]
            if scope not in SCOPES:
                return self._json({"ok": False, "error": f"unknown scope {scope}"}, 400)
            res = LIVE.players(scope, q.get("rescan", ["0"])[0] == "1", q.get("all", ["0"])[0] == "1")
            # a failure while building or sending the reply would otherwise just drop the connection,
            # which the page can only report as "can't reach the reader"
            try:
                body = json.dumps(res, ensure_ascii=False, allow_nan=False).encode("utf-8")
                sys.stderr.write(f"players: {res.get('count', 0):,} rows, {len(body) / 1e6:.0f} MB, "
                                 f"{res.get('seconds', '?')}s, build {res.get('build', '?')}\n")
                self._send(200 if res.get("ok") else 503, body, "application/json; charset=utf-8")
            except (ConnectionError, OSError) as e:
                sys.stderr.write(f"players: the window closed the connection before the data was sent ({e})\n")
            except Exception as e:  # MemoryError, ValueError from an odd value, ...
                traceback.print_exc()
                self._json({"ok": False, "error": f"The reader failed while preparing the data: {type(e).__name__}: {e}"}, 500)
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        """Actions for the tool's own page only (checked by Origin):
        /api/open opens one of the page's outside links in the default browser;
        /api/open-in-fm copies a player's name and brings the game to the front, so it can be pasted into
        FM's search. FM has no link scheme and the reader never writes to the game, so this is as far as it goes."""
        path = urlparse(self.path).path
        if path not in ("/api/open", "/api/open-in-fm"):
            return self._send(404, b"not found", "text/plain")
        port = self.server.server_address[1]
        if self.headers.get("Origin") not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self._json({"ok": False, "error": "wrong origin"}, 403)
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if path == "/api/open-in-fm":
            name = body.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                return self._json({"ok": False, "error": "no player name"}, 400)
            name = " ".join(name.split())  # one line, no control characters
            db_id = body.get("db_id")
            if isinstance(db_id, int) and not isinstance(db_id, bool) and db_id >= 0:
                direct_ok, detail = open_player_direct(db_id)
                if direct_ok:
                    focused = focus_game()
                    return self._json({"ok": True, "direct": True, "opened": True, "focused": focused,
                                       "copied": False, "name": name, "detail": detail})
            res = open_player_in_fm(name)
            return self._json(res)
        url = body.get("url")
        if url not in OUTSIDE_LINKS:
            return self._json({"ok": False, "error": "unknown link"}, 400)
        webbrowser.open(url)
        self._json({"ok": True})


# --- clipboard and window focus for /api/open-in-fm (user32 only; nothing touches the game's memory) ---
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.OpenClipboard.argtypes = [wt.HWND]
_u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
_u32.SetClipboardData.restype = wt.HANDLE
_k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_k32.GlobalAlloc.restype = wt.HGLOBAL
_k32.GlobalLock.argtypes = [wt.HGLOBAL]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
_k32.GlobalFree.argtypes = [wt.HGLOBAL]
_u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_u32.IsWindowVisible.argtypes = [wt.HWND]
_u32.GetWindow.argtypes = [wt.HWND, wt.UINT]
_u32.GetWindow.restype = wt.HWND
_u32.IsIconic.argtypes = [wt.HWND]
_u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
_u32.SetForegroundWindow.argtypes = [wt.HWND]
_u32.GetForegroundWindow.restype = wt.HWND
_u32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
_u32.MapVirtualKeyW.restype = wt.UINT
_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
CF_UNICODETEXT, GMEM_MOVEABLE, SW_RESTORE, GW_OWNER = 13, 0x0002, 9, 4
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
VK_CONTROL, VK_RETURN = 0x11, 0x0D
_OPEN_LOCK = threading.Lock()


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]


_u32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_u32.SendInput.restype = wt.UINT


def _key_input(vk: int, up: bool = False) -> _INPUT:
    scan = _u32.MapVirtualKeyW(vk, 0) & 0xFFFF
    flags = KEYEVENTF_KEYUP if up else 0
    return _INPUT(type=INPUT_KEYBOARD, u=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)))


def _send_keys(*events: _INPUT) -> bool:
    arr = (_INPUT * len(events))(*events)
    return _u32.SendInput(len(events), arr, ctypes.sizeof(_INPUT)) == len(events)


def _send_combo(mod_vk: int, key_vk: int) -> bool:
    return _send_keys(_key_input(mod_vk), _key_input(key_vk), _key_input(key_vk, True), _key_input(mod_vk, True))


def _send_tap(vk: int) -> bool:
    return _send_keys(_key_input(vk), _key_input(vk, True))


def set_clipboard(text: str) -> bool:
    data = (text + "\0").encode("utf-16-le")  # NUL-terminated
    for _ in range(5):  # another program may hold the clipboard for a moment
        if _u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        _u32.EmptyClipboard()
        h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = _k32.GlobalLock(h)
        if not p:
            _k32.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(CF_UNICODETEXT, h):
            _k32.GlobalFree(h)  # the clipboard owns it only on success
            return False
        return True
    finally:
        _u32.CloseClipboard()


def focus_game_hwnd(exe: str = "fm.exe") -> int | None:
    """Restore and bring forward the game's main window, returning its HWND if foregrounded."""
    from winproc import find_pid
    pid = find_pid(exe)
    if not pid:
        return None
    found = []

    def each(hwnd, _):
        owner = wt.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _u32.IsWindowVisible(hwnd) and not _u32.GetWindow(hwnd, GW_OWNER):
            found.append(hwnd)
            return False
        return True

    _u32.EnumWindows(_ENUM_PROC(each), 0)
    if not found:
        return None
    hwnd = found[0]
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, SW_RESTORE)
    if not _u32.SetForegroundWindow(hwnd):
        fg = _u32.GetForegroundWindow()
        fg_thread = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
        me = _k32.GetCurrentThreadId()
        attached = bool(fg_thread and fg_thread != me and _u32.AttachThreadInput(me, fg_thread, True))
        try:
            _u32.BringWindowToTop(hwnd)
            _u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _u32.AttachThreadInput(me, fg_thread, False)
    for _ in range(6):
        if _u32.GetForegroundWindow() == hwnd:
            return hwnd
        time.sleep(0.05)
    return None


def focus_game(exe: str = "fm.exe") -> bool:
    return focus_game_hwnd(exe) is not None


def _nav_dll_path() -> str | None:
    for p in (bundled("fmmt_nav.dll"), os.path.join(HERE, "fmmt_nav.dll")):
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def _pipe_cmd(pid: int, cmd: str) -> str | None:
    pipe = f"\\\\.\\pipe\\FMMT27Nav2_{pid}"
    try:
        with open(pipe, "r+b", buffering=0) as f:
            f.write((cmd + "\n").encode("ascii"))
            return f.read(512).decode("utf-8", "replace").strip()
    except OSError:
        return None


def _inject_nav_dll(pid: int, dll_path: str) -> bool:
    PROCESS_INJECT = 0x0002 | 0x0008 | 0x0020 | 0x0400  # CREATE_THREAD | VM_OPERATION | VM_WRITE | QUERY_INFO
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
    _k32.VirtualAllocEx.restype = ctypes.c_void_p
    _k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _k32.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    _k32.CreateRemoteThread.restype = wt.HANDLE
    _k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    _k32.GetModuleHandleW.restype = wt.HMODULE
    _k32.GetProcAddress.argtypes = [wt.HMODULE, ctypes.c_char_p]
    _k32.GetProcAddress.restype = ctypes.c_void_p
    _k32.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

    h = _k32.OpenProcess(PROCESS_INJECT, False, pid)
    if not h:
        return False
    mem = None
    try:
        buf = (dll_path + "\0").encode("utf-16-le")
        mem = _k32.VirtualAllocEx(h, None, len(buf), 0x3000, 0x04)
        if not mem:
            return False
        written = ctypes.c_size_t()
        if not _k32.WriteProcessMemory(h, mem, buf, len(buf), ctypes.byref(written)):
            return False
        llw = _k32.GetProcAddress(_k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        if not llw:
            return False
        tid = wt.DWORD()
        th = _k32.CreateRemoteThread(h, None, 0, llw, mem, 0, ctypes.byref(tid))
        if not th:
            return False
        try:
            _k32.WaitForSingleObject(th, 5000)
        finally:
            _k32.CloseHandle(th)
        return True
    finally:
        if mem:
            _k32.VirtualFreeEx(h, mem, 0, 0x8000)
        _k32.CloseHandle(h)


def open_player_direct(db_id: int) -> tuple[bool, str]:
    """Open player profile directly inside FM27 via fmmt_nav.dll (Unity main thread PanelManager.Open)."""
    from winproc import find_pid
    with _OPEN_LOCK:
        pid = find_pid("fm.exe")
        if not pid:
            return False, "fm.exe not running"
        resp = _pipe_cmd(pid, f"OPEN {db_id}")
        if resp is None:
            dll = _nav_dll_path()
            if not dll:
                return False, "fmmt_nav.dll not found"
            if not _inject_nav_dll(pid, dll):
                return False, "could not load fmmt_nav.dll into fm.exe"
            for _ in range(15):
                time.sleep(0.1)
                if _pipe_cmd(pid, "PING"):
                    break
            resp = _pipe_cmd(pid, f"OPEN {db_id}")
        if resp and resp.startswith("OK"):
            return True, resp[3:].strip()
        return False, resp or "no response from fmmt_nav.dll"


def open_player_in_fm(name: str) -> dict:
    """Copy `name` to clipboard, focus FM27, and drive its Global Search (Ctrl+F -> Ctrl+A -> Ctrl+V -> Enter)
    via Win32 SendInput. Strictly read-only on game memory; aborts immediately if FM27 loses foreground."""
    with _OPEN_LOCK:
        copied = set_clipboard(name)
        if not copied:
            return {"ok": False, "copied": False, "focused": False, "opened": False, "name": name,
                    "error": "could not use the clipboard"}
        hwnd = focus_game_hwnd()
        if not hwnd:
            return {"ok": True, "copied": True, "focused": False, "opened": False, "name": name}
        time.sleep(0.18)
        if _u32.GetForegroundWindow() != hwnd:
            return {"ok": True, "copied": True, "focused": False, "opened": False, "name": name}
        # Open FM27 global search bar (Ctrl+F), clear previous query (Ctrl+A), paste name (Ctrl+V), open top match (Enter)
        ok = _send_combo(VK_CONTROL, 0x46)  # Ctrl+F
        time.sleep(0.26)
        if ok and _u32.GetForegroundWindow() == hwnd:
            ok = _send_combo(VK_CONTROL, 0x41)  # Ctrl+A
            time.sleep(0.05)
        if ok and _u32.GetForegroundWindow() == hwnd:
            ok = _send_combo(VK_CONTROL, 0x56)  # Ctrl+V
            time.sleep(0.55)
        if ok and _u32.GetForegroundWindow() == hwnd:
            ok = _send_tap(VK_RETURN)  # Enter
        else:
            ok = False
        return {"ok": True, "copied": True, "focused": True, "opened": bool(ok), "name": name} or _u32.GetForegroundWindow() == hwnd


# the only addresses /api/open will open: the links on the tool's page
OUTSIDE_LINKS = {
    "https://buymeacoffee.com/fmoreo",
    "https://community.sports-interactive.com/forums/topic/580105-fm24-statistics-spreadsheet-for-those-that-love-moneyball/",
    "https://github.com/jamiebolden/fm-scoring",
    "https://www.youtube.com/@squirrel_plays_fof4318",
}


class Server(ThreadingHTTPServer):
    # Windows lets a second process bind the same port when SO_REUSEADDR is set, and an old copy
    # then keeps answering with old code. Refuse instead, so only one reader ever runs.
    allow_reuse_address = False


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else PORT
    url = f"http://127.0.0.1:{port}"
    try:
        srv = Server(("127.0.0.1", port), Handler)
    except OSError:
        print(f"The reader is already running at {url} - close its other window first if you updated it.")
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        sys.exit(1)
    print(f"FMMT 27 running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
