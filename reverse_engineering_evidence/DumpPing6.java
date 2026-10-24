import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class DumpPing6 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/ping6.txt"));
        MemoryBlock b = currentProgram.getMemory().getBlock("CODE");
        out.println("CODE " + b.getStart() + " size " + b.getSize());
        byte[] buf = new byte[(int)b.getSize()];
        int n = currentProgram.getMemory().getBytes(b.getStart(), buf);
        out.println("read n=" + n);
        String s = new String(buf, 0, n, "ISO-8859-1");
        String[] needles = {
            "websnapr.com","grapher.compete.com","quantcast.com","siteanalytics.compete.com",
            "api.thumbalizr.com","open.thumbshots.org","pagepeeker.com","traffic.alexa.com",
            "alexa.com","siteadvisor.com","majesticseo.com","cycounter","/siteinfo/","/sites/",
            "/graph","/charts/backlinks-discovery","/trends/viz?q=","[Statistics Only]",
            "[Redirect Only]","Submitting to Indexing Services","pr_cache.dat","last_queue.txt",
            "http://www.yandex.ru/cycounter?"
        };
        Map<Address,String> found = new LinkedHashMap<>();
        for (String nd : needles) {
            int i = s.indexOf(nd);
            if (i != -1) found.put(b.getStart().add(i), nd);
            else out.println("  NOT FOUND: " + nd);
        }
        out.println("found " + found.size());
        FunctionManager fm = currentProgram.getFunctionManager();
        out.println("functions=" + fm.getFunctionCount());
        ReferenceManager rm = currentProgram.getReferenceManager();
        DecompInterface ifc = new DecompInterface(); ifc.openProgram(currentProgram);
        Set<Function> funcs = new LinkedHashSet<>();
        for (Map.Entry<Address,String> e : found.entrySet()) {
            out.println("\n### " + e.getValue() + " @ " + e.getKey());
            int c=0;
            for (Reference ref : rm.getReferencesTo(e.getKey())) {
                Function f = fm.getFunctionContaining(ref.getFromAddress());
                out.println("   ref " + ref.getFromAddress() + " -> " + (f==null?"(nofn)":f.getName()+" @"+f.getEntryPoint()));
                if (f!=null){funcs.add(f);c++;}
                if (c>6) break;
            }
        }
        out.println("\n#### FUNCS "+funcs.size());
        for (Function f : funcs) {
            out.println("\n========== "+f.getName()+" @"+f.getEntryPoint()+" ==========");
            DecompileResults r = ifc.decompileFunction(f, 120, monitor);
            if (r!=null && r.decompileCompleted() && r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose(); println("DONE6");
    }
}
