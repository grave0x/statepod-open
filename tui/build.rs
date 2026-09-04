// build.rs — wireframes.json guard + libswarmstate link path.

use std::env;
use std::path::PathBuf;

fn main() {
    // 1) Wireframe guard
    let wf = PathBuf::from("wireframes.json");
    if !wf.exists() {
        std::process::exit(1);
    }
    let bytes = std::fs::read(&wf).expect("cannot read wireframes.json");
    if bytes.is_empty() {
        std::process::exit(2);
    }
    println!("cargo:rerun-if-changed=wireframes.json");

    // 2) Link against libswarmstate
    //    The library is installed at ~/.local/lib (and is also built in-tree
    //    during a swarmstate make/maturin build). We add both as search
    //    paths so the binary resolves whichever the user has most recent.
    let home = env::var("HOME").unwrap_or_else(|_| "/root".to_string());
    let local_lib = PathBuf::from(&home).join(".local/lib");
    let repo_lib = PathBuf::from(".."); // swarmstate's libswarmstate.{a,so}

    if local_lib.exists() {
        println!("cargo:rustc-link-search=native={}", local_lib.display());
    }
    if repo_lib.exists() {
        println!("cargo:rustc-link-search=native={}", repo_lib.display());
    }

    // Embed rpath so the binary finds libswarmstate.so at runtime without
    // needing LD_LIBRARY_PATH.
    if cfg!(unix) {
        println!("cargo:rustc-link-arg=-Wl,-rpath,{}", local_lib.display());
    }

    // Re-run if the library is newer than the build script
    println!("cargo:rerun-if-changed=../kernel.h");
    println!("cargo:rerun-if-changed=../libswarmstate.so");
    println!("cargo:rerun-if-changed={}/libswarmstate.so", local_lib.display());
}
