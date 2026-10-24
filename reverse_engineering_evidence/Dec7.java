import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.util.*;
import java.io.*;

public class Dec7 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/dec7.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        long[] targets = {0x7278b4L, 0x723e50L, 0x723a14L, 0x723a2cL, 0x7267f8L, 0x724078L};
        for (long va : targets) {
            Function f = fm.getFunctionContaining(toAddr(va));
            if (f == null) { out.println("no fn at " + Long.toHexString(va)); continue; }
            out.println("\n\n================ " + f.getName() + " @" + f.getEntryPoint() + " ================");
            DecompileResults r = ifc.decompileFunction(f, 180, monitor);
            if (r!=null && r.decompileCompleted() && r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose(); println("DEC7 DONE");
    }
}
