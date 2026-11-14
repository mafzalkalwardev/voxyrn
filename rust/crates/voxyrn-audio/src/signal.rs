//! CPU front end. The CUDA build runs the same reductions on the GPU.

use std::f32::consts::PI;

/// Root-mean-square in f64 so a frame matches NumPy's float64 square-mean.
pub fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let mut acc = 0.0f64;
    for sample in samples {
        let value = f64::from(*sample);
        acc += value * value;
    }
    (acc / samples.len() as f64).sqrt() as f32
}

/// Power at one exact frequency. Used for ringback (440+480) and the beep.
pub fn goertzel_power(samples: &[f32], freq_hz: f32, sample_rate: f32) -> f32 {
    let n = samples.len();
    if n == 0 || sample_rate <= 0.0 || freq_hz <= 0.0 {
        return 0.0;
    }
    let step = 2.0 * PI * freq_hz / sample_rate;
    let mut real = 0.0f32;
    let mut imag = 0.0f32;
    for (index, sample) in samples.iter().enumerate() {
        let angle = step * index as f32;
        real += sample * angle.cos();
        imag += sample * angle.sin();
    }
    let scale = n as f32;
    (real * real + imag * imag) / (scale * scale)
}

pub fn is_ring_frame(samples: &[f32], sample_rate: f32) -> bool {
    if rms(samples) < 0.01 {
        return false;
    }
    let low = goertzel_power(samples, 440.0, sample_rate);
    let high = goertzel_power(samples, 480.0, sample_rate);
    let voice = goertzel_power(samples, 200.0, sample_rate);
    let peak = low.max(high);
    let other = low.min(high);
    // Both tones of the dual-tone ring have to be present. A single voice
    // pitch lights one bin and does not look like 440+480.
    peak > 0.0 && other > peak * 0.25 && (low + high) > voice * 4.0
}

pub fn is_beep_frame(samples: &[f32], sample_rate: f32) -> bool {
    if rms(samples) < 0.01 {
        return false;
    }
    let beep = goertzel_power(samples, 1200.0, sample_rate);
    let other = goertzel_power(samples, 440.0, sample_rate)
        + goertzel_power(samples, 2000.0, sample_rate);
    beep > other * 4.0 && beep > 0.0
}

pub fn should_barge(agent_speaking: bool, level: f32, threshold: f32) -> bool {
    agent_speaking && level >= (threshold * 2.0).max(threshold + 0.01)
}
