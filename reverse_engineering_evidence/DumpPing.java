import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class DumpPing extends GhidraScript {
    private DecompInterface ifc;
    public void run() throws Exception {
        ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/ping_decompile.txt"));
        out.println("=== PROGRAM: " + currentProgram.getName());
        // Address list of interest (string VAs in the ping block)
        long[] strVAs = {0x723860L,0x723878L,0x723894L,0x7238b4L,0x7238d4L,0x7238f0L,0x723908L,
                         0x723920L,0x72393cL,0x723950L,0x723960L,0x72396cL,0x72397cL,0x72398cL,
                         0x72399cL,0x7239b4L,0x7239d0L,0x7239e8L,0x72406dL,0x7237ecL,0x723800L,0x723810L};
        Set<Function> funcs = new LinkedHashSet<>();
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        for (long va : strVAs) {
            Address a = toAddr(va);
            out.println("\n### STRING at " + a + " = \"" + readStr(a) + "\"");
            for (Reference ref : rm.getReferencesTo(a)) {
                Address from = ref.getFromAddress();
                Function f = fm.getFunctionContaining(from);
                if (f != null) {
                    out.println("   ref from " + from + " in " + f.getName() + " @" + f.getEntryPoint());
                    funcs.add(f);
                } else { out.println("   ref from " + from + " (no function)"); }
            }
        }
        // Also dump the function that contains 0x723338 and 0x72360c
        for (long va : new long[]{0x723338L, 0x72360cL, 0x72115cL, 0x45d6efL}) {
            Address a = toAddr(va);
            Function f = fm.getFunctionContaining(a);
            out.println("\n>>> function containing " + a + " = " + (f==null?"NONE":f.getName()+" @"+f.getEntryPoint()));
            if (f!=null) funcs.add(f);
        }
        // Decompile each
        for (Function f : funcs) {
            out.println("\n\n================ FUNCTION " + f.getName() + " @ " + f.getEntryPoint() + " ================");
            DecompileResults r = ifc.decompileFunction(f, 60, monitor);
            if (r != null && r.decompileCompleted() && r.getDecompiledFunction()!=null) {
                out.println(r.getDecompiledFunction().getC());
            } else {
                out.println("(decompile failed: " + (r==null?"null":r.getErrorMessage()) + ")");
            }
        }
        out.close();
        ifc.dispose();
        println("DONE");
    }
    private String readStr(Address a) {
        try {
            Memory m = currentProgram.getMemory();
            byte[] b = new byte[80];
            m.getBytes(a, b);
            StringBuilder sb = new StringBuilder();
            for (byte x : b) { if (x==0) break; sb.append((char)(x & 0xff)); }
            return sb.toString();
        } catch (Exception e) { return "<err>"; }
    }
}
