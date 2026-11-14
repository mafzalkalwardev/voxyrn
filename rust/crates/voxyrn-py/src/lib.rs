//! Native module the capture thread calls instead of doing the gate in Python.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use voxyrn_audio::{backend_name, chunk_reply, CallEngine, FrameDecision};

#[pyclass]
struct AudioEngine {
    inner: CallEngine,
}

#[pymethods]
impl AudioEngine {
    #[new]
    fn new() -> Self {
        Self {
            inner: CallEngine::new(),
        }
    }

    fn memory_bytes(&self) -> usize {
        self.inner.memory_bytes()
    }

    /// Little-endian f32 PCM for one frame.
    fn push_pcm(&mut self, py: Python<'_>, pcm: &[u8], agent_speaking: bool, threshold: f32) -> PyResult<PyObject> {
        let mut samples = Vec::with_capacity(pcm.len() / 4);
        for chunk in pcm.chunks_exact(4) {
            let bytes: [u8; 4] = chunk.try_into().unwrap();
            samples.push(f32::from_le_bytes(bytes));
        }
        let decision = self.inner.push(&samples, agent_speaking, threshold);
        Ok(decision_dict(py, decision)?.into())
    }
}

fn decision_dict<'py>(py: Python<'py>, decision: FrameDecision) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("rms", decision.rms)?;
    dict.set_item("barge_in", decision.barge_in)?;
    dict.set_item("in_speech", decision.in_speech)?;
    dict.set_item("first_turn", voxyrn_audio::turn_name(decision.first_turn))?;
    dict.set_item("beep", decision.beep)?;
    dict.set_item("ringback", decision.ringback)?;
    dict.set_item("implementation", "rust")?;
    dict.set_item("backend", backend_name())?;
    Ok(dict)
}

#[pyfunction]
fn chunk_reply_text(text: &str) -> Vec<String> {
    chunk_reply(text)
}

#[pyfunction]
fn audio_backend() -> &'static str {
    backend_name()
}

#[pymodule]
fn voxyrn_core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<AudioEngine>()?;
    module.add_function(wrap_pyfunction!(chunk_reply_text, module)?)?;
    module.add_function(wrap_pyfunction!(audio_backend, module)?)?;
    Ok(())
}
