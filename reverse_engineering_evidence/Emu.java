import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.io.*;

public class Emu extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/emu.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Memory mem = currentProgram.getMemory();

        // find string literals in the emulator region and their referencing functions
        String[] needles = {
            "simulate host","simulate port","index_service_not_simulated","indexer_service_data",
            "index_service_behaviour","Choose Services to emulate","Starting webserver",
            "Unable to bind webserver","Authorization: Basic","Authorization: Token",
            "API-Key provided","login parameter provided","login%","API-Key: ","Login: ",
            "drivers/etc/hosts","webserver to interface :: ","0.0.0.0"
        };
        Map<Address,String> hits = new LinkedHashMap<>();
        MemoryBlock code = currentProgram.getMemory().getBlock("CODE");
        long sz = code.getSize();
        byte[] buf = new byte[(int)sz]; int n = mem.getBytes(code.getStart(), buf);
        String s = new String(buf, 0, n, "ISO-8859-1");
        for (String nd : needles) {
            int i = s.indexOf(nd);
            if (i >= 0) hits.put(code.getStart().add(i), nd);
        }
        out.println("== string hits: " + hits.size());
        Set<Function> funcs = new LinkedHashSet<>();
        for (Map.Entry<Address,String> e : hits.entrySet()) {
            out.println("\n### " + e.getValue() + " @ " + e.getKey());
            int c = 0;
            for (Reference r : rm.getReferencesTo(e.getKey())) {
                Function f = fm.getFunctionContaining(r.getFromAddress());
                out.println("   ref " + r.getFromAddress() + " -> " + (f==null?"(nofn)":f.getName()+" @"+f.getEntryPoint()));
                if (f!=null) { funcs.add(f); c++; }
                if (c>5) break;
            }
        }
        out.println("\n#### FUNCTIONS: " + funcs.size());
        for (Function f : funcs) {
            out.println("\n\n========== " + f.getName() + " @" + f.getEntryPoint() + " ==========");
            DecompileResults r = ifc.decompileFunction(f, 180, monitor);
            if (r!=null && r.decompileCompleted() && r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose(); println("EMU DONE");
    }
}
