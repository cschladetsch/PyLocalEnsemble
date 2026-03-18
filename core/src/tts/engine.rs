use ort::{
    inputs,
    session::Session,
    value::{Shape, Tensor},
};

#[derive(thiserror::Error, Debug)]
pub enum TtsError {
    #[error("Failed to load TTS model from '{path}': {source}")]
    LoadFailed {
        path: String,
        #[source]
        source: Box<dyn std::error::Error + Send + Sync>,
    },

    #[error("Inference failed: {0}")]
    InferenceFailed(#[source] Box<dyn std::error::Error + Send + Sync>),

    #[error("Output shape mismatch: expected audio tensor")]
    OutputShapeMismatch,
}

pub struct TtsEngine {
    session: Session,
}

impl TtsEngine {
    pub fn load(model_path: &str) -> Result<Self, TtsError> {
        let session = Session::builder()
            .map_err(|e| TtsError::LoadFailed {
                path: model_path.to_string(),
                source: Box::new(e),
            })?
            .commit_from_file(model_path)
            .map_err(|e| TtsError::LoadFailed {
                path: model_path.to_string(),
                source: Box::new(e),
            })?;

        Ok(Self { session })
    }

    pub fn synthesize(
        &mut self,
        text: &str,
        voice_embedding: &[f32],
        speed: f32,
    ) -> Result<Vec<i16>, TtsError> {
        // Tokenize text into i64 token IDs (simple ASCII ordinals as placeholder;
        // real usage should pass pre-tokenized IDs).
        let token_ids: Vec<i64> = text.chars().map(|c| c as i64).collect();
        let token_len = token_ids.len();

        let tokens_array = Tensor::<i64>::from_array((Shape::new([1_i64, token_len as i64]), token_ids))
            .map_err(|e| TtsError::InferenceFailed(Box::new(e)))?;

        let style_len = voice_embedding.len();
        let style_array =
            Tensor::<f32>::from_array((Shape::new([1_i64, style_len as i64]), voice_embedding.to_vec()))
                .map_err(|e| TtsError::InferenceFailed(Box::new(e)))?;

        let speed_array =
            Tensor::<f32>::from_array((Shape::new([1]), vec![speed])).map_err(|e| TtsError::InferenceFailed(Box::new(e)))?;

        let outputs = self
            .session
            .run(inputs![tokens_array, style_array, speed_array])
            .map_err(|e| TtsError::InferenceFailed(Box::new(e)))?;

        let audio_tensor = outputs
            .values()
            .next()
            .ok_or(TtsError::OutputShapeMismatch)?;

        let audio_f32 = audio_tensor
            .try_extract_tensor::<f32>()
            .map_err(|e| TtsError::InferenceFailed(Box::new(e)))?;

        let samples: Vec<f32> = audio_f32.1.to_vec();

        // Normalise f32 [-1, 1] → i16
        let pcm: Vec<i16> = samples
            .iter()
            .map(|&s: &f32| {
                let clamped = s.clamp(-1.0, 1.0);
                (clamped * i16::MAX as f32) as i16
            })
            .collect();

        Ok(pcm)
    }
}
