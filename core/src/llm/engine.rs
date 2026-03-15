use llama_cpp_2::{
    context::params::LlamaContextParams,
    llama_backend::LlamaBackend,
    model::{params::LlamaModelParams, LlamaModel},
    token::data_array::LlamaTokenDataArray,
};

#[derive(Debug)]
pub struct Message {
    pub role: String,
    pub content: String,
}

#[derive(thiserror::Error, Debug)]
pub enum LlmError {
    #[error("Failed to load model from '{path}': {source}")]
    LoadFailed {
        path: String,
        #[source]
        source: Box<dyn std::error::Error + Send + Sync>,
    },

    #[error("Failed to create context: {0}")]
    ContextFailed(#[source] Box<dyn std::error::Error + Send + Sync>),

    #[error("Tokenization failed: {0}")]
    TokenizeFailed(#[source] Box<dyn std::error::Error + Send + Sync>),

    #[error("Inference failed: {0}")]
    InferenceFailed(#[source] Box<dyn std::error::Error + Send + Sync>),

    #[error("Decode failed: {0}")]
    DecodeFailed(#[source] Box<dyn std::error::Error + Send + Sync>),
}

pub struct LlmEngine {
    model: LlamaModel,
    n_ctx: u32,
}

impl LlmEngine {
    pub fn load(model_path: &str, n_ctx: u32, n_gpu_layers: u32) -> Result<Self, LlmError> {
        let _backend = LlamaBackend::init().map_err(|e| LlmError::LoadFailed {
            path: model_path.to_string(),
            source: Box::new(e),
        })?;

        let model_params = LlamaModelParams::default().with_n_gpu_layers(n_gpu_layers);

        let model =
            LlamaModel::load_from_file(&_backend, model_path, &model_params).map_err(|e| {
                LlmError::LoadFailed {
                    path: model_path.to_string(),
                    source: Box::new(e),
                }
            })?;

        Ok(Self { model, n_ctx })
    }

    pub fn chat(
        &mut self,
        messages: &[Message],
        system_prompt: &str,
        on_token: impl Fn(&str),
    ) -> Result<String, LlmError> {
        let prompt = build_chatml_prompt(messages, system_prompt);

        let ctx_params = LlamaContextParams::default().with_n_ctx(
            std::num::NonZeroU32::new(self.n_ctx).unwrap_or(std::num::NonZeroU32::new(2048).unwrap()),
        );

        let mut ctx = self
            .model
            .new_context(&LlamaBackend::init().map_err(|e| LlmError::ContextFailed(Box::new(e)))?, ctx_params)
            .map_err(|e| LlmError::ContextFailed(Box::new(e)))?;

        let tokens = self
            .model
            .str_to_token(&prompt, llama_cpp_2::model::AddBos::Always)
            .map_err(|e| LlmError::TokenizeFailed(Box::new(e)))?;

        let mut batch = llama_cpp_2::llama_batch::LlamaBatch::new(tokens.len() + 512, 1);
        for (i, token) in tokens.iter().enumerate() {
            batch
                .add(*token, i as i32, &[0], i == tokens.len() - 1)
                .map_err(|e| LlmError::InferenceFailed(Box::new(e)))?;
        }

        ctx.decode(&mut batch)
            .map_err(|e| LlmError::InferenceFailed(Box::new(e)))?;

        let mut output = String::new();
        let mut n_cur = tokens.len();

        loop {
            let candidates = ctx.candidates_ith(batch.n_tokens() - 1);
            let mut candidates_arr = LlamaTokenDataArray::from_iter(candidates, false);
            let token = ctx.sample_token_greedy(&mut candidates_arr);

            if token == self.model.token_eos() {
                break;
            }

            let token_str = self
                .model
                .token_to_str(token, llama_cpp_2::model::Special::Tokenize)
                .map_err(|e| LlmError::DecodeFailed(Box::new(e)))?;

            on_token(&token_str);
            output.push_str(&token_str);

            batch.clear();
            batch
                .add(token, n_cur as i32, &[0], true)
                .map_err(|e| LlmError::InferenceFailed(Box::new(e)))?;

            ctx.decode(&mut batch)
                .map_err(|e| LlmError::InferenceFailed(Box::new(e)))?;

            n_cur += 1;

            if n_cur >= self.n_ctx as usize {
                break;
            }
        }

        Ok(output)
    }
}

fn build_chatml_prompt(messages: &[Message], system_prompt: &str) -> String {
    let mut prompt = String::new();

    if !system_prompt.is_empty() {
        prompt.push_str("<|im_start|>system\n");
        prompt.push_str(system_prompt);
        prompt.push_str("<|im_end|>\n");
    }

    for msg in messages {
        prompt.push_str("<|im_start|>");
        prompt.push_str(&msg.role);
        prompt.push('\n');
        prompt.push_str(&msg.content);
        prompt.push_str("<|im_end|>\n");
    }

    prompt.push_str("<|im_start|>assistant\n");
    prompt
}
