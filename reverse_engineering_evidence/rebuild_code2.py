import struct, ctypes, ctypes.wintypes, subprocess, time, sys
k32=ctypes.windll.kernel32
PROCESS_QUERY_INFORMATION=0x0400; PROCESS_VM_READ=0x0010
TH32CS_SNAPMODULE=0x00000008; TH32CS_SNAPMODULE32=0x00000010
class MODULEENTRY32W(ctypes.Structure):
    _fields_=[("dwSize",ctypes.wintypes.DWORD),("th32ModuleID",ctypes.wintypes.DWORD),
              ("th32ProcessID",ctypes.wintypes.DWORD),("GlblcntUsage",ctypes.wintypes.DWORD),
              ("GlblcntRef",ctypes.wintypes.DWORD),("modBaseAddr",ctypes.POINTER(ctypes.c_byte)),
              ("modBaseSize",ctypes.wintypes.DWORD),("hModule",ctypes.wintypes.HMODULE),
              ("szModule",ctypes.wintypes.WCHAR*256),("szExePath",ctypes.wintypes.WCHAR*260)]
proc = subprocess.Popen([r"C:\Program Files (x86)\GSA SEO Indexer\SEO_Indexer.exe"])
pid=proc.pid; print("pid",pid,flush=True); time.sleep(14)
h=k32.OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,False,pid)
k32.CreateToolhelp32Snapshot.restype=ctypes.wintypes.HANDLE
snap=k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid)
me=MODULEENTRY32W(); me.dwSize=ctypes.sizeof(MODULEENTRY32W)
base=size=0
if k32.Module32FirstW(snap, ctypes.byref(me)):
    while True:
        if me.szModule.lower()=="seo_indexer.exe":
            base=int(ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value or 0); size=int(me.modBaseSize); break
        if not k32.Module32NextW(snap, ctypes.byref(me)): break
k32.CloseHandle(snap)
print("base",hex(base),"size",hex(size),flush=True)
img = (ctypes.c_char*size)(); read=ctypes.c_size_t(0)
k32.ReadProcessMemory(h, ctypes.c_void_p(base), img, size, ctypes.byref(read))
mem = bytes(img[:read.value])
open("unpacked_image.bin","wb").write(mem)
code = mem[0x1000:0x1000+0x381474]
open("code_section.bin","wb").write(code)
print("code_section.bin", len(code), "bytes; websnapr at", hex(code.find(b'websnapr.com')))
proc.terminate()
