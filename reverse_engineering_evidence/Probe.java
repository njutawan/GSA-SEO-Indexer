import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.listing.*;
import java.io.*;

public class Probe extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/probe.txt"));
        out.println("imageBase=" + currentProgram.getImageBase());
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            out.println("block " + b.getName() + " " + b.getStart() + "-" + b.getEnd() + " size=" + b.getSize());
        }
        // scan all memory for "websnapr.com" and "cycounter"
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.isInitialized()) continue;
            try {
                Address a = b.getStart();
                Address end = b.getEnd();
                byte[] buf = new byte[(int)Math.min(b.getSize(), 40*1024*1024)];
                int n = currentProgram.getMemory().getBytes(a, buf);
                String s = new String(buf, 0, n, "ISO-8859-1");
                int i = s.indexOf("websnapr.com");
                while (i != -1) {
                    out.println("websnapr.com at " + a.add(i) + " block " + b.getName());
                    i = s.indexOf("websnapr.com", i+1);
                }
                i = s.indexOf("cycounter");
                while (i != -1) {
                    out.println("cycounter at " + a.add(i) + " block " + b.getName());
                    i = s.indexOf("cycounter", i+1);
                }
                i = s.indexOf("http://www.yandex.ru/cycounter?");
                while (i != -1) {
                    out.println("yandex full at " + a.add(i));
                    i = s.indexOf("http://www.yandex.ru/cycounter?", i+1);
                }
            } catch (Exception e) { out.println("scan err " + b.getName() + ": " + e); }
        }
        out.close();
        println("PROBE DONE");
    }
}
