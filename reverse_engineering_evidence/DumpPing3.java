import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class DumpPing3 extends GhidraScript {
    private DecompInterface ifc;
    public void run() throws Exception {
        ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/ping3.txt"));
        String[] needles = {
            "websnapr.com","grapher.compete.com","quantcast.com","siteanalytics.compete.com",
            "api.thumbalizr.com","open.thumbshots.org","pagepeeker.com","traffic.alexa.com",
            "alexa.com","siteadvisor.com","majesticseo.com","cycounter","/siteinfo/","/sites/",
            "/graph","/charts/backlinks-discovery","/trends/viz?q=","[Statistics Only]",
            "[Redirect Only]","Submitting to Indexing Services","pr_cache.dat","last_queue.txt",
            "http://www.yandex.ru/cycounter?","google."
        };
        Map<Address,String> found = new LinkedHashMap<>();
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            if (!b.isInitialized()) continue;
            long sz = b.getSize();
            byte[] buf = new byte[65536];
            long pos = 0;
            String carry = "";
            while (pos < sz) {
                int want = (int)Math.min(buf.length, sz-pos);
                int n = 0;
                try { n = currentProgram.getMemory().getBytes(b.getStart().add(pos), buf, 0, want); }
                catch (Exception e) { break; }
                if (n <= 0) break;
                String s = carry + new String(buf, 0, n, "ISO-8859-1");
                for (String nd : needles) {
                    if (!found.containsValue(nd)) {
                        int i = s.indexOf(nd);
                        if (i != -1) found.put(b.getStart().add(pos - carry.length() + i), nd);
                    }
                }
                carry = s.substring(Math.max(0, s.length()-80));
                pos += n;
            }
        }
        out.println("found " + found.size() + " strings");
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Set<Function> funcs = new LinkedHashSet<>();
        for (Map.Entry<Address,String> e : found.entrySet()) {
            out.println("\n### " + e.getValue() + " @ " + e.getKey());
            int cnt=0;
            for (Reference ref : rm.getReferencesTo(e.getKey())) {
                Function f = fm.getFunctionContaining(ref.getFromAddress());
                out.println("   ref " + ref.getFromAddress() + " -> " + (f==null?"(no fn)":f.getName()+" @"+f.getEntryPoint()));
                if (f!=null) { funcs.add(f); cnt++; }
                if (cnt>6) break;
            }
        }
        out.println("\n\n########## FUNCTIONS: " + funcs.size());
        for (Function f : funcs) {
            out.println("\n=========== " + f.getName() + " @ " + f.getEntryPoint() + " ===========");
            DecompileResults r = ifc.decompileFunction(f, 120, monitor);
            if (r!=null && r.decompileCompleted() && r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose();
        println("DONE3");
    }
}
