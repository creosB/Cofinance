import hashlib
import json
from urllib.parse import urljoin

from core.run_trace import read_trace_bytes, save_error_count, trace_integrity_issue


def require_saved_trace(traces, run_id, *, mode=None):
    raw = read_trace_bytes(traces / (run_id + '.json'))
    saved = json.loads(raw)
    if not isinstance(saved, dict) or saved.get('id') != run_id:
        raise AssertionError('Saved trace does not identify the requested run')
    marker_errors = save_error_count(traces, run_id)
    if marker_errors:
        saved['storage_errors'] = max(int(saved.get('storage_errors') or 0), marker_errors)
    digest_input = raw + (f'|save-error:{marker_errors}'.encode('ascii') if marker_errors else b'')
    saved['revision'] = int(hashlib.sha256(digest_input).hexdigest()[:12], 16)
    if issue := trace_integrity_issue(saved):
        raise AssertionError(f"{issue}: Execution proof requires a terminal trace saved with both counters at zero")
    if not isinstance(saved.get('spans'), list) or not saved['spans']:
        raise AssertionError('Saved trace has no recorded spans')
    if mode is not None and saved["mode"] != mode:
        raise AssertionError(f"Expected a {mode} trace, not {saved['mode']}")
    return saved


def expect_rendered_trace(page, saved):
    from playwright.sync_api import expect

    view = page.locator('main .breadcrumb[data-view="execution"]')
    expect(view).to_have_attribute("data-run-id", saved["id"], timeout=8000)
    expect(view).to_have_attribute("data-trace-revision", str(saved["revision"]), timeout=8000)
    expect(view.locator('.state-' + saved['status'])).to_be_visible()


def require_clean_execution(page, traces, run_id, *, mode=None):
    from playwright.sync_api import expect

    saved = require_saved_trace(traces, run_id, mode=mode)
    response = page.request.get(urljoin(page.url, '/api/run'), params={'id': run_id}, timeout=8000)
    if not response.ok:
        raise AssertionError(f"Execution API did not serve the saved trace (HTTP {response.status})")
    received = response.json()
    for key in ('id', 'revision', 'mode', 'status', 'dropped_spans', 'storage_errors'):
        if received.get(key) != saved[key]:
            raise AssertionError(f"Execution API disagrees with the saved trace: {key}")
    expect_rendered_trace(page, saved)
    expect(page.get_by_text('Trace incomplete:', exact=False)).to_have_count(0, timeout=1000)
    expect(page.locator('.error-banner')).to_have_count(0, timeout=1000)
    return saved


def capture_execution(page, traces, run_id, *, mode='live'):
    saved = require_clean_execution(page, traces, run_id, mode=mode)
    image = page.screenshot(animations='disabled')
    after = require_clean_execution(page, traces, run_id, mode=mode)
    if saved['revision'] != after['revision']:
        raise AssertionError('Execution changed during capture; capture a stable terminal trace instead')
    return image
