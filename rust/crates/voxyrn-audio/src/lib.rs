//! First slice of the Voxyrn realtime audio core.
//!
//! The capture thread will push samples here. A barge-in flushes playback.
//! The first inbound turn is classified from how long the person speaks
//! before they yield the floor.

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
