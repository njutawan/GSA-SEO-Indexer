/* Critical one-liner: flags JS support before first paint so reveal
   animations never flash the final state. Zero dependencies. */
document.documentElement.classList.add("js");
