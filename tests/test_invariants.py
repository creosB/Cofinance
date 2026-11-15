"""Repository rules that used to live only in prose. Each failure message names the root cause and the fix.

The Rules page of the Cofinance doc pairs every rule with the test below that enforces it. When an agent is corrected
for something new, the fix belongs here (or in an API change), not in another paragraph of instructions.
"""
import ast
import hashlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FROZEN = {
    'qualify/scorers.py': 'cf60f820e4ed4128f6791576601504b222c0d56bf0867b1f9abd916317e07034',
    'qualify/cases.jsonl': '0eb5fcb53186701d4b8c6dcf0646acbfa8e46b3bf883eb6977b2357f12b007d8',
    'qualify/gates_narrator.json': '9e062ec2f5999b25fbbb5414dca5268780187a5df46fcf219ca6b1b831b37eeb',
}
V2_CODE = ('core', 'ui', 'qualify', 'frontend/src')
HOSTED_LLM = re.compile(r'openai\.com|generativelanguage\.googleapis|openrouter\.ai|api\.anthropic\.com|api\.groq\.com|'
                        r'api\.mistral\.ai|api\.together\.xyz|api\.deepseek\.com')
DASHES = re.compile('[\u2013\u2014]')
POLYMARKET_FILES = ('core/polymarket.py', 'core/crowd_odds.py')
POLYMARKET_HOSTS = {'gamma-api.polymarket.com', 'clob.polymarket.com'}
LINK_ONLY_HOSTS = {'polymarket.com'}


def files(base, *suffixes):
    return [p for p in (ROOT / base).rglob('*') if p.suffix in suffixes and 'node_modules' not in p.parts]


class TestArchitecture(unittest.TestCase):
    def test_no_module_shadows_the_standard_library(self):
        clash = sorted(str(p.relative_to(ROOT)) for base in ('core', 'qualify', 'ui') for p in (ROOT / base).glob('*.py')
                       if p.stem in sys.stdlib_module_names)
        self.assertEqual(clash, [], 'core/hymemory_worker.py and core/router_worker.py run as scripts, so their folder is '
                         'first on the import path and a file named like a stdlib module replaces it (core/numbers.py '
                         'crashed numpy on 2026-11-08). Rename the module.')

    def test_no_hosted_model_endpoints_in_v2(self):
        hits = sorted(str(p.relative_to(ROOT)) for base in V2_CODE for p in files(base, '.py', '.ts', '.tsx')
                      if HOSTED_LLM.search(p.read_text(encoding='utf-8', errors='ignore')))
        self.assertEqual(hits, [], 'v2 runs local inference only. Route model work through core/local_models.py, '
                         'never a hosted API.')

    def test_polymarket_stays_read_only(self):
        from core.polymarket import HOSTS
        self.assertEqual(HOSTS, POLYMARKET_HOSTS, 'Polymarket access is two public read-only hosts. A new host needs '
                         'Baris to approve the source first.')
        problems = []
        for rel in POLYMARKET_FILES:
            tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == 'method' and not (isinstance(kw.value, ast.Constant) and kw.value.value == 'GET'):
                            problems.append(f'{rel}:{node.lineno} non-GET method')
                        if kw.arg == 'data':
                            problems.append(f'{rel}:{node.lineno} request body')
                    if isinstance(node.func, ast.Attribute) and node.func.attr in {'post', 'put', 'patch', 'delete'}:
                        problems.append(f'{rel}:{node.lineno} .{node.func.attr}()')
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r'https?://', node.value):
                    host = re.sub(r'^https?://([^/:?#]+).*$', r'\1', node.value, flags=re.S)
                    if host not in POLYMARKET_HOSTS | LINK_ONLY_HOSTS:
                        problems.append(f'{rel}:{node.lineno} host {host}')
        self.assertEqual(problems, [], 'Cofinance never trades: Polymarket code may only send keyless GET requests to '
                         'the approved hosts.')


class TestFrozenEvidence(unittest.TestCase):
    def test_qualification_inputs_are_unchanged(self):
        changed = [rel for rel, sha in FROZEN.items()
                   if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != sha]
        self.assertEqual(changed, [], 'These files define measured qualification gates. Editing them silently changes '
                         'past results. Add a new scorer or case file and run a new qualification instead.')

    def test_benchmark_docs_carry_the_experimental_warning(self):
        missing = [p.name for p in (ROOT / 'docs/benchmarks').glob('*.md')
                   if '[!WARNING]' not in (t := p.read_text(encoding='utf-8')) or 'xperimental' not in t]
        self.assertEqual(missing, [], 'Every benchmark write-up starts with a > [!WARNING] Experimental callout, so no '
                         'reader mistakes a measurement for advice.')

    def test_private_state_is_ignored(self):
        ignored = set((ROOT / '.gitignore').read_text(encoding='utf-8').split())
        self.assertTrue({'data/', '.env', '.fabrika/'} <= ignored, 'data/ holds databases, traces, models and corpora; '
                        '.env holds secrets; .fabrika/ is a local agent spend ledger. None of them may be committed.')


class TestWriting(unittest.TestCase):
    def test_no_em_or_en_dashes_in_docs_or_ui(self):
        targets = [ROOT / 'README.md', ROOT / 'TODO.md', ROOT / 'frontend/README.md', *(ROOT / 'docs').rglob('*.md'),
                   *files('frontend/src', '.tsx', '.ts')]
        hits = sorted(str(p.relative_to(ROOT)) for p in targets if p.exists() and DASHES.search(p.read_text(encoding='utf-8')))
        self.assertEqual(hits, [], 'Em and en dashes render badly in chat clients and read as machine prose. Use a '
                         'colon, comma or parentheses. README.old.md is an archive and exempt.')


if __name__ == '__main__':
    unittest.main()
