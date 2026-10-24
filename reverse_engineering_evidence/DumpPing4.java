import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;
import java.nio.file.*;

public class DumpPing4 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/ping4.txt"));
        // Read the ORIGINAL memory image file directly (flat mapped: file offset == VA)
        byte[] img = Files.readAllBytes(Paths.get("C:/tools/GSA INDEXER/work/SEO_Indexer_flat.exe"));
        out.println("file size " + img.length);
        String[] needles = {
            "websnapr.com","grapher.compete.com","quantcast.com","siteanalytics.compete.com",
            "api.thumbalizr.com","open.thumbshots.org","pagepeeker.com","traffic.alexa.com",
            "alexa.com","siteadvisor.com","majesticseo.com","cycounter","/siteinfo/","/sites/",
            "/graph","/charts/backlinks-discovery","/trends/viz?q=","[Statistics Only]",
            "[Redirect Only]","Submitting to Indexing Services","pr_cache.dat","last_queue.txt"
        };
        Map<Long,String> found = new LinkedHashMap<>();
        String s = new String(img, "ISO-8859-1");
        for (String nd : needles) {
            int i = s.indexOf(nd);
            if (i != -1) found.put((long)i, nd);
        }
        out.println("found " + found.size());
        for (Map.Entry<Long,String> e : found.entrySet()) {
            out.println("  " + e.getValue() + " @ fileoff " + Long.toHexString(e.getKey())
              + " VA " + Long.toHexString(e.getKey()+0x400000L));
        }
        out.close();
        println("DONE4");
    }
}
