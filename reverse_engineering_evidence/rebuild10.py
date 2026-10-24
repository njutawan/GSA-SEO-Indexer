import struct
img = open(r"C:\tools\GSA INDEXER\work\unpacked_image.bin","rb").read()
e = struct.unpack_from("<I", img, 0x3c)[0]
sizeofopt = struct.unpack_from("<H", img, e+20)[0]
sec_off = e+24+sizeofopt
filealign=0x200; sectalign=0x1000
code_va=0x1000; code_vsz=0x381474
code = img[code_va:code_va+code_vsz]
rawsz = (len(code)+filealign-1)//filealign*filealign
RAW = 0x200

hdr = bytearray(img[:sec_off+40])
struct.pack_into("<H", hdr, e+6, 1)
if len(hdr) < filealign: hdr += b'\0'*(filealign-len(hdr))
o=sec_off
hdr[o:o+8]=b"CODE\x00\x00\x00\x00"
struct.pack_into("<I", hdr, o+8,  code_vsz)
struct.pack_into("<I", hdr, o+12, code_va)
struct.pack_into("<I", hdr, o+16, RAW)
struct.pack_into("<I", hdr, o+20, rawsz)
struct.pack_into("<I", hdr, o+24,0); struct.pack_into("<I", hdr, o+28,0)
struct.pack_into("<H", hdr, o+32,0); struct.pack_into("<H", hdr, o+34,0)
struct.pack_into("<I", hdr, o+36,0x60000020)
struct.pack_into("<I", hdr, e+24+56, code_va+code_vsz)
struct.pack_into("<I", hdr, e+24+60, filealign)
struct.pack_into("<I", hdr, e+24+64,0)
struct.pack_into("<I", hdr, e+24+92, 16)

out = bytearray(hdr)
out[RAW:RAW+len(code)] = code
# PAD the file so that total size = RAW + rawsz + extra, making the tail region
# land exactly on our code. Ghidra appears to read the LAST rawsz bytes of the file.
# Simplest robust fix: make the file exactly RAW+rawsz and put code at the END.
out2 = bytearray(hdr)
if len(out2) < filealign: out2 += b'\0'*(filealign-len(out2))
pad_needed = rawsz - len(code)
out2 += b'\0'*pad_needed
out2 += code
# now file = filealign + rawsz; code at end
out = out2
open(r"C:\tools\GSA INDEXER\work\SEO_Indexer_ok3.exe","wb").write(bytes(out))
d=bytes(out); pos=d.find(b"websnapr.com")
print("wrote",hex(len(out)),"websnapr file off",hex(pos))
print("code occupies file", hex(len(out)-len(code)), "..", hex(len(out)))
