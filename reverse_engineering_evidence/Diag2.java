import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import java.io.*;
public class Diag2 extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/diag2.txt"));
    out.println("imageBase="+currentProgram.getImageBase());
    for (MemoryBlock b : currentProgram.getMemory().getBlocks())
      out.println("block "+b.getName()+" "+b.getStart()+"-"+b.getEnd()+" init="+b.isInitialized()+" sz="+b.getSize());
    for (long va : new long[]{0x723860L, 0x401000L, 0x401010L}) {
      try { byte[] b=new byte[24]; int n=currentProgram.getMemory().getBytes(toAddr(va),b);
        StringBuilder sb=new StringBuilder(); for(int i=0;i<n;i++) sb.append(String.format("%02x ",b[i]));
        StringBuilder t=new StringBuilder(); for(int i=0;i<n;i++) t.append((b[i]>=32&&b[i]<127)?(char)b[i]:'.');
        out.println("mem "+Long.toHexString(va)+" : "+sb+"  |"+t+"|");
      } catch(Exception e){ out.println("mem "+Long.toHexString(va)+" ERR "+e.getMessage()); }
    }
    out.close(); println("DIAG2 DONE");
  }
}
