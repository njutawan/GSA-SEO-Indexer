import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import java.io.*;
public class Quick extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out=new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/quick.txt"));
    for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
      if (!b.isInitialized()) continue;
      long sz=b.getSize(); if(sz>30*1024*1024) continue;
      byte[] buf=new byte[(int)sz]; int n=0;
      try{n=currentProgram.getMemory().getBytes(b.getStart(),buf);}catch(Exception e){continue;}
      String s=new String(buf,0,n,"ISO-8859-1");
      StringBuilder sb=new StringBuilder(); for(int i=0;i<Math.min(16,n);i++) sb.append(String.format("%02x ",buf[i]));
      out.println("block "+b.getName()+" @"+b.getStart()+" n="+n+" first:"+sb);
      for(String nd: new String[]{"websnapr.com","cycounter","/siteinfo/","Submitting to Indexing","pr_cache.dat"}){
        int i=s.indexOf(nd);
        out.println("    "+(i>=0?("FOUND "+nd+" @"+b.getStart().add(i)):"miss  "+nd));
      }
    }
    out.close(); println("QUICK DONE");
  }
}
