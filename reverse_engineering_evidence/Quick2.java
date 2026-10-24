import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.*;
import java.io.*;
import java.nio.file.*;
public class Quick2 extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out=new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/quick2.txt"));
    byte[] img=Files.readAllBytes(Paths.get("C:/tools/GSA INDEXER/work/SEO_Indexer_ok.exe"));
    out.println("file size "+img.length);
    for (int off : new int[]{0x1000, 0x323860, 0x1000-0x1000}) {
      if (off<0||off+16>img.length){out.println("off "+off+" oob");continue;}
      StringBuilder sb=new StringBuilder(); for(int i=0;i<16;i++) sb.append(String.format("%02x ",img[off+i]));
      out.println("file "+off+" : "+sb);
    }
    // where does '68 b7 00 00' appear in the file?
    for (int i=0;i<img.length-4;i++){
      if (img[i]==0x68&&img[i+1]==0xb7&&img[i+2]==0x00&&img[i+3]==0x00){ out.println("pattern at file off "+i); if(i>0) break; }
    }
    out.close(); println("Q2 DONE");
  }
}
