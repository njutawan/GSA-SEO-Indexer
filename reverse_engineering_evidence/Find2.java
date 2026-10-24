import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class Find2 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/find2.txt"));
        // find all references to the string block 0x723860..0x723a00 and to pr_cache.dat
        ReferenceManager rm = currentProgram.getReferenceManager();
        FunctionManager fm = currentProgram.getFunctionManager();
        // 1) all data refs INTO 0x723800-0x724100
        Set<Function> callers = new LinkedHashSet<>();
        for (long va = 0x723800; va < 0x724100; va++) {
            Address a = toAddr(va);
            for (Reference r : rm.getReferencesTo(a)) {
                Function f = fm.getFunctionContaining(r.getFromAddress());
                if (f != null) callers.add(f);
            }
        }
        out.println("callers touching string block: " + callers.size());
        for (Function f : callers) out.println("  " + f.getName() + " @" + f.getEntryPoint());
        // 2) find functions that reference BOTH a host string and a path string
        out.close(); println("FIND2 DONE");
    }
}
