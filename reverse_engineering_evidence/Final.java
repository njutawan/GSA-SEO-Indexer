import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.io.*;

public class Final extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/final.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.toggleSyntaxTree(true);
        ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        out.println("functions=" + fm.getFunctionCount());
        long[] addrs = {
            0x723860L,0x723878L,0x723894L,0x7238acL,0x7238d0L,0x7238ecL,0x723908L,
            0x723920L,0x723928L,0x72393cL,0x723960L,0x723974L,0x72398cL,0x72399cL,
            0x7239b4L,0x7239e8L,0x724058L,0x720984L,0x723db8L,0x721110L,0x722ee0L,
            0x72119cL,0x7237ecL,0x723800L,0x723810L,0x7237ecL,0x71a234L
        };
        Set<Function> funcs = new LinkedHashSet<>();
        for (long va : addrs) {
            Address a = toAddr(va);
            String txt = "";
            try { byte[] bb = new byte[60]; int n = currentProgram.getMemory().getBytes(a, bb);
                  StringBuilder sb = new StringBuilder(); for (int i=0;i<n;i++){ if(bb[i]==0)break; sb.append((char)(bb[i]&0xff)); } txt=sb.toString(); } catch(Exception e){}
            out.println("\n### \"" + txt + "\" @ " + a);
            int c = 0;
            for (Reference ref : rm.getReferencesTo(a)) {
                Function f = fm.getFunctionContaining(ref.getFromAddress());
                out.println("   ref " + ref.getFromAddress() + " -> " + (f==null?"(nofn)":f.getName()+" @"+f.getEntryPoint()));
                if (f!=null){funcs.add(f);c++;}
                if (c>6) break;
            }
        }
        out.println("\n#### FUNCTIONS: " + funcs.size());
        for (Function f : funcs) {
            out.println("\n\n========== " + f.getName() + " @" + f.getEntryPoint() + " ==========");
            DecompileResults r = ifc.decompileFunction(f, 180, monitor);
            if (r!=null && r.decompileCompleted() && r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose(); println("FINAL DONE");
    }
}
