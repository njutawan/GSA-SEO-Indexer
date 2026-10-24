import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.io.*;

public class Trace extends GhidraScript {
    public void run() throws Exception {
        PrintWriter out = new PrintWriter(new FileWriter("C:/tools/GSA INDEXER/work/trace.txt"));
        DecompInterface ifc = new DecompInterface();
        ifc.toggleCCode(true); ifc.setSimplificationStyle("decompile");
        ifc.openProgram(currentProgram);
        FunctionManager fm = currentProgram.getFunctionManager();
        // BFS from FUN_00723338 depth 3, collecting functions in the 0x72xxxx range
        Set<Function> seen = new LinkedHashSet<>();
        Deque<Function> q = new ArrayDeque<>();
        Function start = fm.getFunctionContaining(toAddr(0x723338L));
        q.add(start);
        Map<Function,Integer> depth = new HashMap<>();
        depth.put(start,0);
        while(!q.isEmpty()){
            Function f = q.poll();
            if (!seen.add(f)) continue;
            int d = depth.get(f);
            if (d>=3) continue;
            for (Function c : f.getCalledFunctions(monitor)) {
                if (c==null) continue;
                if (!depth.containsKey(c)){ depth.put(c,d+1); q.add(c); }
            }
        }
        out.println("reachable funcs: "+seen.size());
        List<Function> lst = new ArrayList<>(seen);
        lst.sort(Comparator.comparingLong(f->f.getEntryPoint().getOffset()));
        for (Function f : lst) {
            long ep = f.getEntryPoint().getOffset();
            if (ep < 0x700000L || ep > 0x740000L) continue;
            out.println("\n\n========== "+f.getName()+" @"+Long.toHexString(ep)+" depth="+depth.get(f)+" ==========");
            DecompileResults r=ifc.decompileFunction(f,180,monitor);
            if (r!=null&&r.decompileCompleted()&&r.getDecompiledFunction()!=null) out.println(r.getDecompiledFunction().getC());
        }
        out.close(); ifc.dispose(); println("TRACE DONE");
    }
}
