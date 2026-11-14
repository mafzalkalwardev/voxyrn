//! Realtime audio core for one live call.
//!
//! The capture thread pushes PCM here. The ring is fixed-capacity, barge-in
//! drops playback, and the first turn is classified from burst length.
//! Ringback and beep detection run on the same frames. CUDA is an optional
//! build of the same front end; the default build is the CPU path.

mod chunk;
mod cuda;
mod engine;
mod signal;

pub use chunk::{chunk_reply, filler_due, pace_gap_ms};
pub use cuda::backend_name;
pub use engine::{greeting_seconds, turn_name, CallEngine, FrameDecision};
pub use signal::rms;

/// Continuous speech longer than this, with no yield, is a recorded greeting.
pub const GREETING_CONTINUOUS_SECS: f32 = 2.2;
/// A live pickup is a short burst, then silence.
pub const HUMAN_BURST_MAX_SECS: f32 = 1.2;
/// Silence after a short burst before we treat it as a live person waiting.
pub const HUMAN_YIELD_SECS: f32 = 0.4;

/// Fixed-capacity mono PCM ring. Oldest samples drop when it is full.
#[derive(Debug, Clone)]
pub struct PcmRing {
    samples: Vec<f32>,
    write: usize,
    len: usize,
}

impl PcmRing {
    pub fn new(capacity: usize) -> Self {
        Self {
            samples: vec![0.0; capacity.max(1)],
            write: 0,
            len: 0,
        }
    }

    pub fn push(&mut self, sample: f32) {
        let cap = self.samples.len();
        self.samples[self.write] = sample;
        self.write = (self.write + 1) % cap;
        self.len = (self.len + 1).min(cap);
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn capacity(&self) -> usize {
        self.samples.len()
    }

    /// Oldest sample first.
    pub fn snapshot(&self) -> Vec<f32> {
        let cap = self.samples.len();
        let start = (self.write + cap - self.len) % cap;
        (0..self.len)
            .map(|offset| self.samples[(start + offset) % cap])
            .collect()
    }

    pub fn clear(&mut self) {
        self.write = 0;
        self.len = 0;
    }
}

/// Playback that can be thrown away the moment the caller talks over it.
#[derive(Debug, Default)]
pub struct Playback {
    queued_ms: u32,
    speaking: bool,
}

impl Playback {
    pub fn start(&mut self, queued_ms: u32) {
        self.queued_ms = queued_ms;
        self.speaking = queued_ms > 0;
    }

    pub fn is_speaking(&self) -> bool {
        self.speaking
    }

    /// Returns true when inbound speech should cut the agent off.
    pub fn barge_in(&mut self, inbound_speech: bool) -> bool {
        if self.speaking && inbound_speech {
            self.queued_ms = 0;
            self.speaking = false;
            return true;
        }
        false
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FirstTurn {
    /// Short utterance, then the caller waits.
    Human,
    /// Long unbroken speech. Treat as a voicemail greeting.
    Greeting,
    /// Not enough evidence yet. Stay quiet and keep listening.
    Wait,
}

/// Classify the first thing heard after answer.
pub fn classify_first_turn(speech_secs: f32, trailing_silence_secs: f32) -> FirstTurn {
    if speech_secs >= GREETING_CONTINUOUS_SECS {
        return FirstTurn::Greeting;
    }
    if speech_secs > 0.0
        && speech_secs <= HUMAN_BURST_MAX_SECS
        && trailing_silence_secs >= HUMAN_YIELD_SECS
    {
        return FirstTurn::Human;
    }
    FirstTurn::Wait
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_drops_the_oldest_sample() {
        let mut ring = PcmRing::new(3);
        for sample in [1.0, 2.0, 3.0, 4.0] {
            ring.push(sample);
        }
        assert_eq!(ring.snapshot(), vec![2.0, 3.0, 4.0]);
    }

    #[test]
    fn barge_in_flushes_playback_once() {
        let mut playback = Playback::default();
        playback.start(800);
        assert!(playback.barge_in(true));
        assert!(!playback.is_speaking());
        assert!(!playback.barge_in(true));
    }

    #[test]
    fn silence_while_speaking_does_not_flush() {
        let mut playback = Playback::default();
        playback.start(400);
        assert!(!playback.barge_in(false));
        assert!(playback.is_speaking());
    }

    #[test]
    fn short_burst_then_silence_is_a_person() {
        assert_eq!(classify_first_turn(0.6, 0.5), FirstTurn::Human);
    }

    #[test]
    fn long_unbroken_speech_is_a_greeting() {
        assert_eq!(classify_first_turn(3.0, 0.1), FirstTurn::Greeting);
    }

    #[test]
    fn a_finished_long_greeting_stays_a_greeting() {
        assert_eq!(classify_first_turn(3.4, 1.0), FirstTurn::Greeting);
    }

    #[test]
    fn an_unfinished_short_burst_keeps_waiting() {
        assert_eq!(classify_first_turn(0.8, 0.1), FirstTurn::Wait);
    }
}

#[cfg(test)]
mod engine_tests {
    use super::*;

    fn tone(freq: f32, seconds: f32) -> Vec<f32> {
        let n = (seconds * engine::SAMPLE_RATE) as usize;
        (0..n)
            .map(|i| (2.0 * std::f32::consts::PI * freq * i as f32 / engine::SAMPLE_RATE).sin() * 0.2)
            .collect()
    }

    fn push_all(gate: &mut CallEngine, samples: &[f32], speaking: bool) -> FrameDecision {
        let mut last = gate.push(&[], speaking, 0.02);
        for frame in samples.chunks(320) {
            last = gate.push(frame, speaking, 0.02);
        }
        last
    }

    #[test]
    fn the_ring_stays_eight_seconds() {
        let mut gate = CallEngine::new();
        let before = gate.memory_bytes();
        let loud = vec![0.2f32; 16_000 * 10];
        push_all(&mut gate, &loud, false);
        assert_eq!(gate.memory_bytes(), before);
        assert_eq!(before, 8 * 16_000 * 4);
    }

    #[test]
    fn loud_audio_while_speaking_barge_in() {
        let mut gate = CallEngine::new();
        let decision = gate.push(&[0.2; 320], true, 0.02);
        assert!(decision.barge_in);
        let quiet = gate.push(&[0.0; 320], true, 0.02);
        assert!(!quiet.barge_in);
    }

    #[test]
    fn a_short_burst_then_silence_locks_human() {
        let mut gate = CallEngine::new();
        let speech = vec![0.2f32; (0.6 * engine::SAMPLE_RATE) as usize];
        let silence = vec![0.0f32; (0.5 * engine::SAMPLE_RATE) as usize];
        push_all(&mut gate, &speech, false);
        let decision = push_all(&mut gate, &silence, false);
        assert_eq!(decision.first_turn, FirstTurn::Human);
    }

    #[test]
    fn a_long_tone_locks_the_greeting() {
        let mut gate = CallEngine::new();
        let speech = vec![0.2f32; (2.3 * engine::SAMPLE_RATE) as usize];
        let decision = push_all(&mut gate, &speech, false);
        assert_eq!(decision.first_turn, FirstTurn::Greeting);
        assert!((engine::greeting_seconds() - 2.2).abs() < 0.01);
    }

    #[test]
    fn north_american_ringback_breaks_when_someone_speaks() {
        let mut gate = CallEngine::new();
        let ring = {
            let low = tone(440.0, 1.6);
            let high = tone(480.0, 1.6);
            low.iter().zip(high).map(|(a, b)| a + b).collect::<Vec<_>>()
        };
        let during = push_all(&mut gate, &ring, false);
        assert_eq!(during.ringback, "ringing");
        let gap = vec![0.0f32; 16_000];
        assert_eq!(push_all(&mut gate, &gap, false).ringback, "ringing");
        let voice = tone(180.0, 0.4);
        assert_eq!(push_all(&mut gate, &voice, false).ringback, "pickup");
    }

    #[test]
    fn a_sustained_beep_is_marked() {
        let mut gate = CallEngine::new();
        let beep = tone(1200.0, 0.4);
        assert!(push_all(&mut gate, &beep, false).beep);
        let mut other = CallEngine::new();
        let noise = tone(180.0, 0.4);
        assert!(!push_all(&mut other, &noise, false).beep);
    }

    #[test]
    fn replies_start_at_the_first_sentence() {
        let parts = chunk_reply("Got it. Let me check that for you.");
        assert_eq!(
            parts,
            vec!["Got it.".to_string(), "Let me check that for you.".to_string()]
        );
        let price = chunk_reply("It is 1.5 percent, then we send the lanes.");
        assert_eq!(price.len(), 1);
    }

    #[test]
    fn a_long_clause_can_start_playback() {
        let parts = chunk_reply("Got it, let me pull the lanes for you, then we can book.");
        assert!(parts.len() >= 2);
        assert!(parts[0].ends_with(','));
    }

    #[test]
    fn fillers_and_pacing_stay_in_range() {
        assert!(!filler_due(599));
        assert!(filler_due(600));
        for seed in [1u64, 15, 99] {
            let gap = pace_gap_ms(seed);
            assert!((12_000..=28_000).contains(&gap));
        }
        assert_ne!(pace_gap_ms(1), pace_gap_ms(2));
    }

    #[test]
    fn the_default_backend_is_cpu_and_the_kernel_is_in_tree() {
        assert_eq!(backend_name(), "cpu");
        let kernel = include_str!("../cuda/front_end.cu");
        assert!(kernel.contains("__global__"));
        assert!(kernel.contains("voxyrn_frame_rms_kernel"));
        assert!(kernel.contains("voxyrn_cuda_rms"));
    }
}
