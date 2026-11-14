//! CUDA is a compile-time backend for the per-frame front end.
//!
//! `backend_name` is `cpu` unless this crate is built with `--features cuda`,
//! which compiles `cuda/front_end.cu` and links it. Model weights (whisper,
//! ONNX, GGUF) stay in their own runtimes; those pick CUDA only when the
//! process can see an NVIDIA device.

pub fn backend_name() -> &'static str {
    if cfg!(feature = "cuda") {
        "cuda"
    } else {
        "cpu"
    }
}

#[cfg(feature = "cuda")]
pub mod ffi {
    use std::os::raw::{c_float, c_int};

    extern "C" {
        pub fn voxyrn_cuda_rms(host: *const c_float, n: c_int, out: *mut c_float) -> c_int;
    }
}
