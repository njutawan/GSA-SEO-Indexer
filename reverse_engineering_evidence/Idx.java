import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.io.*;

public class Idx extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/idx.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        Memory mem = currentProgram.getMemory();

        // Method-name strings (Delphi publishes them as RTTI) let us find the
        // handler: search for "DLwwwindexkingscom1Click" anywhere in memory and
        // take xrefs to it.
        String[] needles = {"DLwwwindexkingscom1Click", "DLwwwindexkingscom1",
                            "FilterbySite1Click", "wait_to_finish", "Timer2Timer"};
        Map<Address,String> hits = new LinkedHashMap<>();
        for (MemoryBlock b : mem.getBlocks()) {
            if (!b.isInitialized()) continue;
            long sz=b.getSize(); if (sz > 12*1024*1024) continue;
            byte[] buf=new byte[(int)sz]; int n=0;
            try{n=mem.getBytes(b.getStart(),buf);}catch(Exception e){continue;}
            String s=new String(buf,0,n,"ISO-8859-1");
            for (String nd : needles) {
                int i=s.indexOf(nd);
                if (i>=0) hits.put(b.getStart().add(i), nd);
            }
        }
        out.println("== hits: " + hits.size());
        Set<Function> funcs = new LinkedHashSet<>();
        for (Map.Entry<Address,String> e : hits.entrySet()) {
            out.println("\n### " + e.getValue() + " @ " + e.getKey());
            int c=0;
            for (Reference r : rm.getReferencesTo(e.getKey())) {
                Function f=fm.getFunctionContaining(r.getFromAddress());
                out.println("   ref "+r.getFromAddress()+" -> "+(f==null?"(nofn)":f.getName()+" @"+f.getEntryPoint()));
                if (f!=null){funcs.add(f);c++;}
                if (c>5) break;
            }
        }
        out.println("\n#### FUNCS "+funcs.size());
        for (Function f : funcs) {
            out.println("\n\n========== "+f.getName()+" @"+f.getEntryPoint()+" ==========");
            DecompileResults r=ifc.decompileFunction(f,180,monitor);
            if (r!=null&&r.decompileCompleted()&&r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
            else out.println("(failed)");
        }
        out.close(); ifc.dispose(); println("IDX DONE");
    }
}
