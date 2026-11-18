"""Observed narrator usage only. No word estimates, tokenizer guesses, or shared 'last response' state.

Counters use a numeric-only schema without relaxing the trace's secret/token redaction. GLiNER, embeddings and
separately collected headline judgments are outside this chat-narrator counter. A missing receipt is unknown.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_receipts = ContextVar('narrator_usage_receipts', default=None)
LIMIT = 2**53 - 1


def counts(value):
    """Allowlisted numeric receipt, safe to persist beside redacted trace text."""
    if not isinstance(value, dict):
        return None
    fields = [value.get(k) for k in ('input', 'output', 'total')]
    if any(type(n) is not int or not 0 <= n <= LIMIT for n in fields):
        return None
    if fields[0] + fields[1] != fields[2]:
        return None
    return dict(zip(('input', 'output', 'total'), fields))


def record_usage(value):
    """Called immediately after a runtime response, before output validation or cancellation checks."""
    target = _receipts.get()
    if target is None:
        return
    value = value if isinstance(value, dict) else {}
    target.append(counts({'input': value.get('prompt_tokens'), 'output': value.get('completion_tokens'),
                          'total': value.get('total_tokens')}))


@contextmanager
def capture_usage():
    receipts = []
    token = _receipts.set(receipts)
    try:
        yield receipts
    finally:
        _receipts.reset(token)


def summarize(spans, dropped=0):
    """Every narrator attempt, including rejected output and retries, once. Other model kinds never count."""
    roles = {}
    for span in spans:
        if span.get('kind') != 'model' or span.get('name') != 'Narrate evidence':
            continue
        key = (str(span.get('actor', 'Narrator')), str(span.get('model', 'Unknown model')))
        row = roles.setdefault(key, {'actor': key[0], 'model': key[1], 'attempts': 0, 'reported': 0,
                                     'input': 0, 'output': 0, 'total': 0})
        row['attempts'] += 1
        usage = counts(span.get('usage'))
        if usage is not None:
            row['reported'] += 1
            for k in ('input', 'output', 'total'):
                row[k] += usage[k]
    out = {k: sum(r[k] for r in roles.values()) for k in ('input', 'output', 'total', 'attempts', 'reported')}
    # Numbers are the known subtotal; completeness is explicit, even when a failed call reported nothing.
    out.update(version=1, scope='chat_narrator', complete=out['attempts'] == out['reported'] and not dropped,
               roles=list(roles.values()))
    return out
