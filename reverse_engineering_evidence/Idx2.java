import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.io.*;

public class Idx2 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/idx2.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        Memory mem = currentProgram.getMemory();

        // Delphi publishes method names in the RTTI/vmt. Find the "DLwwwindexkingscom1Click"
        // string, then look for a code pointer stored near the RTTI that points into CODE.
        // Simpler: search CODE for any function that references the RTTI string indirectly is hard;
        // instead decompile every function in 0x71x000-0x72x000 and grep the output for the
        // indexkings URL / download logic.
        int decompiled = 0;
        for (Function f : fm.getFunctions(true)) {
            long ep = f.getEntryPoint().getOffset();
            if (ep < 0x718000L || ep > 0x722000L) continue;
            DecompileResults r = ifc.decompileFunction(f, 60, monitor);
            if (r==null || !r.decompileCompleted() || r.getDecompiledFunction()==null) continue;
            String c = r.getDecompiledFunction().getC();
            decompiled++;
            String low = c.toLowerCase();
            if (low.contains("indexkings") || low.contains("download") || low.contains("0x71da78")
                || low.contains("0x71dfa5") || low.contains("sites_uncrypted") || low.contains("pr_cache")) {
                out.println("\n\n========== "+f.getName()+" @"+f.getEntryPoint()+" ==========");
                out.println(c);
            }
        }
        out.println("\n\n# decompiled functions scanned: " + decompiled);
        out.close(); ifc.dispose(); println("IDX2 DONE");
    }
}
