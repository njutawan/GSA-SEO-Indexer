import ctypes, ctypes.wintypes, sys, time, subprocess

kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", ctypes.wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", ctypes.wintypes.DWORD),
                ("Protect", ctypes.wintypes.DWORD),
                ("Type", ctypes.wintypes.DWORD)]

MEM_COMMIT = 0x1000

proc = subprocess.Popen([r"C:\Program Files (x86)\GSA SEO Indexer\SEO_Indexer.exe"])
pid = proc.pid
print("PID:", pid)
time.sleep(10)

h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
if not h:
    print("OpenProcess failed", kernel32.GetLastError()); sys.exit(1)

out = open(r"C:\tools\GSA INDEXER\work\memdump.bin", "wb")
mbi = MEMORY_BASIC_INFORMATION()
addr = 0
total = 0
regions = 0
while addr < 0x7FFFFFFF:
    n = kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if not n:
        break
    base = mbi.BaseAddress or 0
    if mbi.State == MEM_COMMIT and mbi.RegionSize > 0 and not (mbi.Protect & 0x100):
        size = min(mbi.RegionSize, 64*1024*1024)
        buf = (ctypes.c_char * size)()
        read = ctypes.c_size_t(0)
        if kernel32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size, ctypes.byref(read)):
            data = bytes(buf[:read.value])
            hdr = "===REGION %s size %d prot %x===\n" % (hex(base), read.value, mbi.Protect)
            out.write(hdr.encode())
            out.write(data)
            total += read.value
            regions += 1
    addr = base + mbi.RegionSize

out.close()
kernel32.CloseHandle(h)
print("Dumped", regions, "regions,", total, "bytes")
