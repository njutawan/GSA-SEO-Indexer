import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.io.*;
public class FindAnywhere extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/findany.txt"));
    for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
      if (!b.isInitialized() || b.isExecute()==false && !b.getName().equals("CODE")) continue;
      long sz = b.getSize(); if (sz > 30*1024*1024) continue;
      byte[] buf = new byte[(int)sz]; int n=0;
      try { n = currentProgram.getMemory().getBytes(b.getStart(), buf); } catch(Exception e){ continue; }
      String s = new String(buf,0,n,"ISO-8859-1");
      for (String nd : new String[]{"websnapr.com","cycounter","/siteinfo/","Submitting to Indexing"}) {
        int i = s.indexOf(nd);
        if (i>=0) out.println("FOUND '"+nd+"' in block "+b.getName()+" at "+b.getStart().add(i));
      }
      // also print first bytes to identify block content
      StringBuilder sb=new StringBuilder(); for(int i=0;i<Math.min(24,n);i++) sb.append(String.format("%02x ",buf[i]));
      out.println("block "+b.getName()+" @"+b.getStart()+" n="+n+" first: "+sb);
    }
    out.close(); println("FIND DONE");
  }
}
