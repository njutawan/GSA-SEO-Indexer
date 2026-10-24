import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.io.*;

public class Emu2 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/emu2.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        Memory mem = currentProgram.getMemory();
        ReferenceManager rm = currentProgram.getReferenceManager();

        // The emulator is TIndexServer; find its class methods by looking for
        // the RTTI method-name strings near the class name and resolving the
        // vmt.  Simpler: decompile EVERY function in 0x756000-0x762000 and keep
        // the ones that mention the emulator markers.
        String[] markers = {"indexer_service_data","index_service_not_simulated","index_service_behaviour",
                            "Indexer Server here","Empty Reply","page title: ","TIndexServer","TIndexClient"};
        int scanned=0, kept=0;
        for (Function f : fm.getFunctions(true)) {
            long ep=f.getEntryPoint().getOffset();
            if (ep < 0x755000L || ep > 0x763000L) continue;
            scanned++;
            DecompileResults r=ifc.decompileFunction(f,120,monitor);
            if (r==null||!r.decompileCompleted()||r.getDecompiledFunction()==null) continue;
            String c=r.getDecompiledFunction().getC();
            boolean hit=false;
            for (String m : markers) if (c.contains(m)) { hit=true; break; }
            if (!hit) {
                // also keep anything that calls the known handler
                for (Function callee : f.getCalledFunctions(monitor)) {
                    if (callee!=null && (callee.getEntryPoint().getOffset()==0x7564bcL
                        || callee.getEntryPoint().getOffset()==0x7568c0L
                        || callee.getEntryPoint().getOffset()==0x7606b0L)) { hit=true; break; }
                }
            }
            if (hit) {
                kept++;
                out.println("\n\n========== "+f.getName()+" @"+f.getEntryPoint()+" ==========");
                out.println(c);
            }
        }
        out.println("\n\n# scanned "+scanned+" functions in 0x755000-0x763000, kept "+kept);
        out.close(); ifc.dispose(); println("EMU2 DONE");
    }
}
