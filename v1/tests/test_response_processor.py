from utils.response_processor import extract_thinking_blocks


def test_extract_thinking_blocks_single():
    text = "Hello world\n<think>Strategy Analysis: Detected BTC. Activating Risk Guardian Mode.</think>\n1. Data Summary: ..."
    filtered, thoughts = extract_thinking_blocks(text)
    assert 'Strategy Analysis' in thoughts[0]
    assert '<think>' not in filtered


def test_extract_thinking_blocks_multi_line():
    text = "<think>\n1. Strategy Analysis:\n- Classify: CRYPTO\n- Risk Mode: Activating Risk Guardian Mode\n</think>\n1. Data Summary: ..."
    filtered, thoughts = extract_thinking_blocks(text)
    assert len(thoughts) == 1
    assert 'Classify' in thoughts[0]
    assert 'Risk Mode' in thoughts[0]
    assert '<think>' not in filtered


def test_extract_thinking_blocks_none():
    text = "1. Data Summary: ...\nSome more text"
    filtered, thoughts = extract_thinking_blocks(text)
    assert filtered == text
    assert thoughts == []


def test_add_unique_thought_dedup():
    from utils.response_processor import add_unique_thought, normalize_thought

    reasoning = ""
    existing = set()
    raw1 = "💭 Strategy Analysis: Detected BTC. Activating Risk Guardian Mode."
    reasoning, existing = add_unique_thought(reasoning, existing, raw1)
    # Adding it a second time with alternate formatting should not add duplicate
    raw2 = "Strategy Analysis: Detected BTC. Activating Risk Guardian Mode"
    reasoning2, existing = add_unique_thought(reasoning, existing, raw2)
    assert reasoning.count("💭 **Thinking:**") == 1
    # Also ensure normalize worked
    assert normalize_thought(raw1) == normalize_thought(raw2)


def test_streamed_think_block_deduping():
    # Simulate streaming of chunks where <think> block closes across chunks
    from utils.response_processor import extract_thinking_blocks, add_unique_thought, normalize_thought

    chunks = ["<think>Strategy Analysis: Detected BTC.", " Activating Risk Guardian Mode.</think>", "\nStrategy Analysis: Detected BTC. Activating Risk Guardian Mode.\n1. Data Summary: ..."]
    reasoning_text = ""
    existing_thoughts = set()

    accumulated_response = ""
    for chunk in chunks:
        accumulated_response += chunk
        filtered, think_matches = extract_thinking_blocks(accumulated_response)
        for match in think_matches:
            reasoning_text, existing_thoughts = add_unique_thought(reasoning_text, existing_thoughts, match)
        accumulated_response = filtered

        # per-line fallback detection for user-friendly thinking lines
        for line in accumulated_response.split('\n'):
            line_lower = line.strip().lower()
            if not line.strip():
                continue
            if line.strip().startswith('💭') or line_lower.startswith('strategy analysis'):
                norm = normalize_thought(line.strip())
                if norm not in existing_thoughts:
                    reasoning_text, existing_thoughts = add_unique_thought(reasoning_text, existing_thoughts, line.strip())

    # Ensure Strategy Analysis appears only once in reasoning
    assert reasoning_text.count('💭 **Thinking:**') == 1
