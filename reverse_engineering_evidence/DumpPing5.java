import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class DumpPing5 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/ping5.txt"));
        out.println("imageBase="+currentProgram.getImageBase());
        FunctionManager fm = currentProgram.getFunctionManager();
        out.println("functions="+fm.getFunctionCount());
        // find strings by scanning CODE block
        MemoryBlock b = currentProgram.getMemory().getBlock("CODE");
        if (b==null) { out.println("no CODE block"); }
        long[] want = {0x723860L,0x723878L,0x723894L,0x7238acL,0x7238d0L,0x7238ecL,0x723908L,
                       0x723920L,0x723928L,0x72393cL,0x723960L,0x723974L,0x72398cL,0x72399cL,
                       0x7239b4L,0x7239e8L,0x724058L,0x720984L,0x723db8L,0x721110L,0x722ee0L};
        ReferenceManager rm = currentProgram.getReferenceManager();
        DecompInterface ifc = new DecompInterface(); ifc.openProgram(currentProgram);
        Set<Function> funcs = new LinkedHashSet<>();
        for (long va : want) {
            Address a = toAddr(va);
            String txt="";
            try { byte[] bb=new byte[40]; int n=currentProgram.getMemory().getBytes(a,bb);
                  StringBuilder sb=new StringBuilder(); for(int i=0;i<n;i++){ if(bb[i]==0)break; sb.append((char)(bb[i]&0xff)); } txt=sb.toString(); }
            catch(Exception e){}
            out.println("\n### "+txt+" @ "+a);
            for (Reference ref : rm.getReferencesTo(a)) {
                Function f = fm.getFunctionContaining(ref.getFromAddress());
                out.println("   ref "+ref.getFromAddress()+" -> "+(f==null?"(nofn)":f.getName()+" @"+f.getEntryPoint()));
                if (f!=null) funcs.add(f);
            }
        }
        out.println("\n#### FUNCS: "+funcs.size());
        for (Function f : funcs) {
            out.println("\n========== "+f.getName()+" @"+f.getEntryPoint()+" ==========");
            DecompileResults r = ifc.decompileFunction(f, 120, monitor);
            if (r!=null && r.decompileCompleted() && r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose(); println("DONE5");
    }
}
