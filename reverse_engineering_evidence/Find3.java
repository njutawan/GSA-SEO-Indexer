import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.mem.*;
import java.util.*;
import java.io.*;

public class Find3 extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/find3.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();

        // The http request is done by some helper. Find the helper that builds a URL
        // by looking for functions referencing "http://" style strings and the
        // "://" separator at 0x45d970 is in DATA (not CODE) -> skip.
        // Instead: find the function that references pr_cache.dat AND a "http" literal.
        long[] seeds = {0x722ee0L /*pr_cache.dat*/, 0x720984L /*[Statistics Only]*/, 0x723db8L /*[Redirect Only]*/};
        Set<Function> funcs = new LinkedHashSet<>();
        for (long va : seeds) {
            for (Reference r : rm.getReferencesTo(toAddr(va))) {
                Function f = fm.getFunctionContaining(r.getFromAddress());
                if (f!=null) { out.println("seed "+Long.toHexString(va)+" -> "+f.getName()+" @"+f.getEntryPoint()); funcs.add(f); }
            }
        }
        // also: functions that reference a string containing "http" scheme
        Memory mem = currentProgram.getMemory();
        for (MemoryBlock b : mem.getBlocks()) {
            if (!b.isInitialized()) continue;
            long sz=b.getSize(); if (sz>8*1024*1024) continue;
            byte[] buf=new byte[(int)sz]; int n=0;
            try{n=mem.getBytes(b.getStart(),buf);}catch(Exception e){continue;}
            String s=new String(buf,0,n,"ISO-8859-1");
            int i=0;
            while ((i=s.indexOf("http://",i))!=-1){
                Address a=b.getStart().add(i);
                for (Reference r : rm.getReferencesTo(a)) {
                    Function f=fm.getFunctionContaining(r.getFromAddress());
                    if (f!=null && !funcs.contains(f)) { out.println("http-literal @"+a+" -> "+f.getName()+" @"+f.getEntryPoint()); funcs.add(f); }
                }
                i+=7;
            }
        }
        out.println("\n#### candidate functions: "+funcs.size());
        for (Function f : funcs) {
            out.println("\n\n========== "+f.getName()+" @"+f.getEntryPoint()+" ==========");
            DecompileResults r=ifc.decompileFunction(f,180,monitor);
            if (r!=null&&r.decompileCompleted()&&r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
        }
        out.close(); ifc.dispose(); println("FIND3 DONE");
    }
}
