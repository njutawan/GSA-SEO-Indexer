import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import java.io.*;
import java.nio.file.*;
public class Diag extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/diag.txt"));
    byte[] img = Files.readAllBytes(Paths.get("C:/tools/GSA INDEXER/work/SEO_Indexer_flat2.exe"));
    out.println("file size " + img.length);
    for (long va : new long[]{0x723860L, 0x323860L, 0x645CC0L, 0x401000L}) {
      StringBuilder sb = new StringBuilder();
      try { byte[] b = new byte[16]; int n = currentProgram.getMemory().getBytes(toAddr(va), b);
            for (int i=0;i<n;i++) sb.append(String.format("%02x ", b[i])); out.println("mem "+Long.toHexString(va)+" n="+n+" : "+sb); }
      catch (Exception e) { out.println("mem "+Long.toHexString(va)+" ERR "+e); }
      int off = (int)va;
      if (off >= 0 && off+16 <= img.length) {
        sb = new StringBuilder();
        for (int i=0;i<16;i++) sb.append(String.format("%02x ", img[off+i]));
        out.println("file "+Long.toHexString(off)+" : "+sb);
      } else out.println("file "+Long.toHexString(off)+" out of range");
    }
    out.close(); println("DIAG DONE");
  }
}
