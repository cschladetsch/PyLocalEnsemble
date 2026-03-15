/// Split `text` into sentences on `.`, `!`, or `?` followed by whitespace or
/// end of string. Empty strings after trimming are dropped.
pub fn split_sentences(text: &str) -> Vec<String> {
    let mut sentences: Vec<String> = Vec::new();
    let mut current = String::new();
    let chars: Vec<char> = text.chars().collect();
    let len = chars.len();

    for i in 0..len {
        current.push(chars[i]);

        if matches!(chars[i], '.' | '!' | '?') {
            // Emit if followed by whitespace or at end
            let next_is_boundary = i + 1 >= len || chars[i + 1].is_whitespace();
            if next_is_boundary {
                let trimmed = current.trim().to_string();
                if !trimmed.is_empty() {
                    sentences.push(trimmed);
                }
                current = String::new();
            }
        }
    }

    // Any trailing text without a terminal punctuation mark
    let trimmed = current.trim().to_string();
    if !trimmed.is_empty() {
        sentences.push(trimmed);
    }

    sentences
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_split() {
        let result = split_sentences("Hello world. How are you? Fine!");
        assert_eq!(result, vec!["Hello world.", "How are you?", "Fine!"]);
    }

    #[test]
    fn trailing_no_punct() {
        let result = split_sentences("Hello world. Goodbye");
        assert_eq!(result, vec!["Hello world.", "Goodbye"]);
    }

    #[test]
    fn empty_input() {
        let result = split_sentences("");
        assert!(result.is_empty());
    }

    #[test]
    fn only_whitespace() {
        let result = split_sentences("   ");
        assert!(result.is_empty());
    }
}
