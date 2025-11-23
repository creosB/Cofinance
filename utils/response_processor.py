import re
from typing import Tuple, List, Set


def extract_thinking_blocks(text: str) -> Tuple[str, List[str]]:
    """
    Extracts all <think>...</think> blocks from `text` (case-insensitive, dotall) and
    returns a tuple of (text_without_think_blocks, list_of_think_contents).

    Args:
        text (str): The input text potentially containing <think> blocks.

    Returns:
        Tuple[str, List[str]]: (filtered_text, [think_contents])
    """
    if not text:
        return text, []

    try:
        matches = re.findall(r'(?is)<think>(.*?)</think>', text)
        cleaned = [m.strip() for m in matches if m.strip()]
        filtered_text = re.sub(r'(?is)<think>.*?</think>', '', text)
        return filtered_text, cleaned
    except re.error:
        # Fallback: no change
        return text, []


def normalize_thought(text: str) -> str:
    """Normalize a thought string for deduplication.

    Lowercases, strips markup characters, removes punctuation, and collapses
    whitespace. This is used to detect semantically identical thoughts.
    """
    if not text:
        return ""
    # Remove emoji and common markers
    s = text.replace('💭', ' ')
    s = re.sub(r'<.*?>', ' ', s)  # strip any HTML/markup
    s = re.sub(r'\*\*', '', s)  # remove bold markers
    # Replace punctuation with spaces
    s = re.sub(r'[^a-z0-9\s]', ' ', s.lower())
    # Collapse whitespace
    s = ' '.join(s.split())
    return s.strip()


def add_unique_thought(reasoning_text: str, existing: Set[str], raw: str) -> Tuple[str, Set[str]]:
    """Add `raw` as a formatted 'Thinking' block to reasoning_text if it's unique.

    Returns (reasoning_text, existing_set) updated.
    """
    norm = normalize_thought(raw)
    if not norm or norm in existing:
        return reasoning_text, existing
    existing.add(norm)
    reasoning_text += f"💭 **Thinking:** {raw}\n"
    return reasoning_text, existing
