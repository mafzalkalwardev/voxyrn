//! Split a reply into the first speakable clause without waiting for the rest.

pub fn filler_due(wait_ms: u32) -> bool {
    wait_ms >= 600
}

/// Gap between dials, 12 to 28 seconds, from a seed. Not a fixed cooldown.
pub fn pace_gap_ms(seed: u64) -> u32 {
    let mut x = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
    x ^= x >> 30;
    x = x.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x ^= x >> 27;
    const LOW: u32 = 12_000;
    const HIGH: u32 = 28_000;
    LOW + (x % u64::from(HIGH - LOW + 1)) as u32
}

pub fn chunk_reply(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    for (i, (idx, ch)) in chars.iter().copied().enumerate() {
        let boundary = match ch {
            '.' | '!' | '?' => {
                let prev_digit = i > 0 && chars[i - 1].1.is_ascii_digit();
                let next_digit = chars.get(i + 1).map(|item| item.1.is_ascii_digit()).unwrap_or(false);
                let next_space = chars
                    .get(i + 1)
                    .map(|item| item.1.is_whitespace())
                    .unwrap_or(true);
                !(ch == '.' && prev_digit && next_digit) && next_space
            }
            ',' | ';' => word_count(&text[start..idx + ch.len_utf8()]) >= 6,
            _ => false,
        };
        if boundary {
            let end = idx + ch.len_utf8();
            push_piece(&mut out, &text[start..end]);
            start = end;
        }
    }
    push_piece(&mut out, &text[start..]);
    out
}

fn push_piece(out: &mut Vec<String>, piece: &str) {
    let trimmed = piece.trim();
    if !trimmed.is_empty() {
        out.push(trimmed.to_string());
    }
}

fn word_count(piece: &str) -> usize {
    piece.split_whitespace().filter(|word| !word.is_empty()).count()
}
