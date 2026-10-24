import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import ghidra.program.model.listing.*;
import java.io.*;
public class Layout extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/layout.txt"));
    out.println("imageBase="+currentProgram.getImageBase());
    for (MemoryBlock b : currentProgram.getMemory().getBlocks())
      out.println("block "+b.getName()+" "+b.getStart()+"-"+b.getEnd()+" init="+b.isInitialized()+" r="+b.isRead()+" x="+b.isExecute());
    // try reading bytes at 0x723860
    try { byte[] b = new byte[32]; int n=currentProgram.getMemory().getBytes(toAddr(0x723860L), b);
      out.println("read at 0x723860 n="+n+" : "+new String(b,0,n,"ISO-8859-1"));
    } catch(Exception e){ out.println("read failed: "+e); }
    FunctionManager fm=currentProgram.getFunctionManager();
    out.println("functions="+fm.getFunctionCount());
    out.close(); println("LAYOUT DONE");
  }
}
