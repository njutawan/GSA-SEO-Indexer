import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class Scan3 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/scan3.txt"));
        // For every function in 0x71xxxx-0x72xxxx, collect the string literals it
        // references, and report ones that mention hosts/paths/ping-ish words.
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Memory mem = currentProgram.getMemory();
        String[] interesting = {"http", "www.", "url", "link", "site", "submit", "add", "index", "ping", "stat", "value", "back", "ref", "domain", "counter"};
        for (Function f : fm.getFunctions(true)) {
            long ep = f.getEntryPoint().getOffset();
            if (ep < 0x710000L || ep > 0x730000L) continue;
            Set<String> lits = new LinkedHashSet<>();
            for (Reference r : rm.getReferencesTo(f.getEntryPoint())) { /*incoming*/ }
            // outgoing refs from the function body
            AddressIterator ai = f.getBody().getAddresses(true);
            int count = 0;
            for (Address a : ai) {
                for (Reference r : rm.getReferencesFrom(a)) {
                    Address to = r.getToAddress();
                    MemoryBlock tb = mem.getBlock(to);
                    if (tb == null || !tb.isInitialized()) continue;
                    try {
                        byte[] b = new byte[80]; int n = mem.getBytes(to, b);
                        StringBuilder sb = new StringBuilder();
                        boolean ok = true;
                        for (int i=0;i<n;i++){ if(b[i]==0) break; if(b[i]<32||b[i]>126){ok=false;break;} sb.append((char)b[i]); }
                        if (ok && sb.length()>=3) lits.add(sb.toString());
                    } catch(Exception e){}
                }
                if (++count > 4000) break;
            }
            List<String> hits = new ArrayList<>();
            for (String s : lits) {
                String ls = s.toLowerCase();
                for (String k : interesting) {
                    if (ls.contains(k)) { hits.add(s); break; }
                }
            }
            if (hits.size() >= 3) {
                out.println("\n### " + f.getName() + " @" + Long.toHexString(ep) + "  (" + hits.size() + " hits)");
                for (String h : hits) out.println("    " + h);
            }
        }
        out.close(); println("SCAN3 DONE");
    }
}
