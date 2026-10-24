import ctypes, ctypes.wintypes, struct, subprocess, time, sys
k32=ctypes.windll.kernel32
PROCESS_QUERY_INFORMATION=0x0400; PROCESS_VM_READ=0x0010
TH32CS_SNAPMODULE=0x00000008; TH32CS_SNAPMODULE32=0x00000010
MAX_PATH=260
class MODULEENTRY32W(ctypes.Structure):
    _fields_=[("dwSize",ctypes.wintypes.DWORD),("th32ModuleID",ctypes.wintypes.DWORD),
              ("th32ProcessID",ctypes.wintypes.DWORD),("GlblcntUsage",ctypes.wintypes.DWORD),
              ("GlblcntRef",ctypes.wintypes.DWORD),("modBaseAddr",ctypes.POINTER(ctypes.c_byte)),
              ("modBaseSize",ctypes.wintypes.DWORD),("hModule",ctypes.wintypes.HMODULE),
              ("szModule",ctypes.wintypes.WCHAR*256),("szExePath",ctypes.wintypes.WCHAR*260)]
proc=subprocess.Popen([r"C:\Program Files (x86)\GSA SEO Indexer\SEO_Indexer.exe"])
pid=proc.pid; print("pid",pid,flush=True)
time.sleep(14)
h=k32.OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,False,pid)
# re-enumerate for the new pid
snap=k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid)
me=MODULEENTRY32W(); me.dwSize=ctypes.sizeof(MODULEENTRY32W)
base=size=0
if k32.Module32FirstW(snap, ctypes.byref(me)):
    while True:
        if me.szModule.lower()=="seo_indexer.exe":
            base=int(ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value or 0)
            size=int(me.modBaseSize); break
        if not k32.Module32NextW(snap, ctypes.byref(me)): break
k32.CloseHandle(snap)
print("NEW pid base/size", hex(base), hex(size), flush=True)
if not base: sys.exit(1)
buf=(ctypes.c_char*size)(); read=ctypes.c_size_t(0)
if not k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size, ctypes.byref(read)):
    print("RPM fail", k32.GetLastError()); sys.exit(1)
img=bytes(buf[:read.value])
open(r"C:\tools\GSA INDEXER\work\unpacked_image.bin","wb").write(img)
print("dumped", read.value, flush=True)
