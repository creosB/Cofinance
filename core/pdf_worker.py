"""Extract page text from one PDF read on stdin; print JSON to stdout. Runs in its own process so a malformed or
hostile file can be killed on a time limit without touching the dashboard. Text only: no images, no OCR, no scripts.

    python core/pdf_worker.py MAX_PAGES < file.pdf
"""
import io
import json
import logging
import sys


def main():
    max_pages = int(sys.argv[1])
    logging.disable(logging.CRITICAL)  # pypdf warns about many harmless quirks; the parent only reads JSON
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        print(json.dumps({'error': 'PDF support is not installed (pypdf).'}))
        return
    data = sys.stdin.buffer.read()
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        # Many PDFs are encrypted only with an empty user password (copy or print limits); those open as normal.
        if reader.is_encrypted and not reader.decrypt(''):
            print(json.dumps({'error': 'Password-protected PDFs are not supported.'}))
            return
        total = len(reader.pages)
        if total > max_pages:
            print(json.dumps({'error': f'PDFs are limited to {max_pages} pages; this one has {total}.'}))
            return
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or '')
            except Exception:
                pages.append('')  # one unreadable page must not lose the rest
        print(json.dumps({'pages': pages, 'total_pages': total}))
    except Exception as exc:  # any parser failure (including a missing decryption dependency) is one plain refusal
        print(json.dumps({'error': 'This file could not be read as a PDF.', 'detail': type(exc).__name__}))


if __name__ == '__main__':
    main()
