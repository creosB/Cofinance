import re
from typing import List, Tuple


CODE_BLOCK_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_+-]*)\n(?P<code>.*?)(?:```)", re.DOTALL
)


def extract_code_blocks(markdown: str, allowed_langs: List[str] | None = None) -> List[Tuple[str, str]]:
    """
    Extract fenced code blocks from Markdown.
    Returns a list of tuples: (language, code).
    If allowed_langs is provided, only return blocks with matching languages (case-insensitive).
    """
    found: List[Tuple[str, str]] = []
    if not markdown:
        return found
    for m in CODE_BLOCK_RE.finditer(markdown):
        lang = (m.group("lang") or "").strip().lower()
        code = m.group("code")
        if allowed_langs is None or lang in [l.lower() for l in allowed_langs]:
            found.append((lang, code))
    return found
