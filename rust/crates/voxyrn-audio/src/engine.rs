//! One call's audio state. The sample ring never grows past eight seconds.

use crate::signal::{is_beep_frame, is_ring_frame, rms, should_barge};
use crate::{classify_first_turn, FirstTurn, PcmRing, GREETING_CONTINUOUS_SECS};

pub const SAMPLE_RATE: f32 = 16_000.0;
const RING_SAMPLES: usize = 8 * 16_000;
const BEEP_SECONDS: f32 = 0.35;
const RING_ON_SECONDS: f32 = 1.5;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FrameDecision {
    pub rms: f32,
    pub barge_in: bool,
    pub in_speech: bool,
    pub first_turn: FirstTurn,
    pub beep: bool,
    pub ringback: &'static str,
}

pub struct CallEngine {
    ring: PcmRing,
    burst_samples: u32,
    silence_samples: u32,
    locked: Option<FirstTurn>,
    beep_samples: u32,
    ring_on_samples: u32,
    ring_armed: bool,
    ringback: &'static str,
}

impl CallEngine {
    pub fn new() -> Self {
        Self {
            ring: PcmRing::new(RING_SAMPLES),
            burst_samples: 0,
            silence_samples: 0,
            locked: None,
            beep_samples: 0,
            ring_on_samples: 0,
            ring_armed: false,
            ringback: "idle",
        }
    }

    pub fn memory_bytes(&self) -> usize {
        self.ring.capacity() * std::mem::size_of::<f32>()
    }

    pub fn push(&mut self, samples: &[f32], agent_speaking: bool, threshold: f32) -> FrameDecision {
        for sample in samples {
            self.ring.push(*sample);
        }
        let level = rms(samples);
        let speech = !samples.is_empty() && level >= threshold;
        let count = samples.len() as u32;
        if self.locked.is_none() {
            if speech {
                self.burst_samples = self.burst_samples.saturating_add(count);
                self.silence_samples = 0;
            } else if self.burst_samples > 0 {
                self.silence_samples = self.silence_samples.saturating_add(count);
            }
            let turn = classify_first_turn(
                self.burst_samples as f32 / SAMPLE_RATE,
                self.silence_samples as f32 / SAMPLE_RATE,
            );
            if turn != FirstTurn::Wait {
                self.locked = Some(turn);
            }
        }
        self.note_beep(samples, count);
        self.note_ring(samples, count);
        FrameDecision {
            rms: level,
            barge_in: should_barge(agent_speaking, level, threshold),
            in_speech: speech,
            first_turn: self.locked.unwrap_or(FirstTurn::Wait),
            beep: self.beep_samples >= (BEEP_SECONDS * SAMPLE_RATE) as u32,
            ringback: self.ringback,
        }
    }

    fn note_beep(&mut self, samples: &[f32], count: u32) {
        if is_beep_frame(samples, SAMPLE_RATE) {
            self.beep_samples = self.beep_samples.saturating_add(count);
        } else if self.beep_samples < (BEEP_SECONDS * SAMPLE_RATE) as u32 {
            self.beep_samples = 0;
        }
    }

    fn note_ring(&mut self, samples: &[f32], count: u32) {
        if self.ringback == "pickup" {
            return;
        }
        if is_ring_frame(samples, SAMPLE_RATE) {
            self.ring_on_samples = self.ring_on_samples.saturating_add(count);
            if self.ring_on_samples >= (RING_ON_SECONDS * SAMPLE_RATE) as u32 {
                self.ring_armed = true;
                self.ringback = "ringing";
            }
            return;
        }
        self.ring_on_samples = 0;
        if self.ring_armed && rms(samples) >= 0.01 {
            self.ringback = "pickup";
        } else if self.ring_armed {
            self.ringback = "ringing";
        }
    }
}

impl Default for CallEngine {
    fn default() -> Self {
        Self::new()
    }
}

pub fn turn_name(turn: FirstTurn) -> &'static str {
    match turn {
        FirstTurn::Human => "human",
        FirstTurn::Greeting => "greeting",
        FirstTurn::Wait => "wait",
    }
}

/// Greeting lock uses the same continuous-speech constant the pure classifier uses.
pub fn greeting_seconds() -> f32 {
    GREETING_CONTINUOUS_SECS
}
