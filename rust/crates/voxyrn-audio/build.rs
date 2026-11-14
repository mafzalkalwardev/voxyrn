use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=cuda/front_end.cu");
    println!("cargo:rerun-if-env-changed=VOXYRN_CUDA");
    let cuda = env::var("CARGO_FEATURE_CUDA").is_ok();
    if !cuda {
        return;
    }
    let nvcc = env::var("NVCC").unwrap_or_else(|_| "nvcc".to_string());
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR"));
    let object = out_dir.join("front_end.o");
    let status = Command::new(&nvcc)
        .args([
            "-O3",
            "-std=c++17",
            "--compiler-options",
            "-fPIC",
            "-c",
            "cuda/front_end.cu",
            "-o",
        ])
        .arg(&object)
        .status();
    match status {
        Ok(code) if code.success() => {}
        Ok(code) => panic!("nvcc exited {code}. Install the CUDA toolkit or build without --features cuda."),
        Err(err) => panic!("nvcc ({nvcc}) failed to start: {err}. CUDA builds need the NVIDIA toolkit."),
    }
    let lib = out_dir.join("libvoxyrn_cuda.a");
    let archive = Command::new("ar")
        .args(["crus"])
        .arg(&lib)
        .arg(&object)
        .status()
        .expect("ar");
    if !archive.success() {
        panic!("ar failed while packing the CUDA object");
    }
    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-lib=static=voxyrn_cuda");
    println!("cargo:rustc-link-lib=cudart");
}
