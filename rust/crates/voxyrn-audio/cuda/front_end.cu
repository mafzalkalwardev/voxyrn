// Per-frame audio front end for an NVIDIA GPU.
// The CPU crate computes the same RMS. This file is compiled only with
// `cargo build --features cuda` when nvcc is on PATH. The Oracle ARM host
// has no GPU and does not build this.

extern "C" {

__global__ void voxyrn_frame_rms_kernel(const float* samples, int n, float* out) {
    __shared__ float partial[256];
    int tid = threadIdx.x;
    float acc = 0.0f;
    for (int i = tid; i < n; i += blockDim.x) {
        float value = samples[i];
        acc += value * value;
    }
    partial[tid] = acc;
    __syncthreads();
    for (int stride = blockDim.x / 2; stride > 0; stride >>= 1) {
        if (tid < stride) {
            partial[tid] += partial[tid + stride];
        }
        __syncthreads();
    }
    if (tid == 0) {
        out[0] = n > 0 ? sqrtf(partial[0] / static_cast<float>(n)) : 0.0f;
    }
}

// Returns 0 on success, 1 when CUDA reports an error.
int voxyrn_cuda_rms(const float* host, int n, float* out) {
    if (host == nullptr || out == nullptr || n <= 0) {
        return 1;
    }
    float* device_samples = nullptr;
    float* device_out = nullptr;
    if (cudaMalloc(&device_samples, sizeof(float) * static_cast<size_t>(n)) != cudaSuccess) {
        return 1;
    }
    if (cudaMalloc(&device_out, sizeof(float)) != cudaSuccess) {
        cudaFree(device_samples);
        return 1;
    }
    cudaMemcpy(device_samples, host, sizeof(float) * static_cast<size_t>(n), cudaMemcpyHostToDevice);
    voxyrn_frame_rms_kernel<<<1, 256>>>(device_samples, n, device_out);
    cudaMemcpy(out, device_out, sizeof(float), cudaMemcpyDeviceToHost);
    cudaError_t err = cudaDeviceSynchronize();
    cudaFree(device_samples);
    cudaFree(device_out);
    return err == cudaSuccess ? 0 : 1;
}

}
