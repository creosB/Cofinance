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


V1_PACKAGES = {'v1', 'agents', 'tools', 'utils', 'storage', 'app'}
V1_ROOT_PATHS = ('app.py', 'agents', 'tools', 'utils', 'screenshots', 'ui/layout.py', 'ui/styles.py')
CORE_MAY_IMPORT_FROM_QUALIFY = {'qualify.decide_eval', 'qualify.run', 'qualify.scorers'}


def imported(path):
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            yield from ((a.name, node.lineno) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, node.lineno


class TestBoundaries(unittest.TestCase):
    def test_v2_never_imports_v1(self):
        v2 = [*(ROOT / 'core').glob('*.py'), *(ROOT / 'qualify').glob('*.py'), *(ROOT / 'ui').glob('*.py')]
        bad = [f'{p.relative_to(ROOT)}:{line} {name}' for p in v2 for name, line in imported(p)
               if name.split('.')[0] in V1_PACKAGES]
        self.assertEqual(bad, [], 'v1 lives under v1/ as history. '
                         'v2 must not depend on it; copy what you need into core/ and test it there.')

    def test_v1_files_stay_under_v1_folder(self):
        leaked = [rel for rel in V1_ROOT_PATHS if (ROOT / rel).exists()]
        self.assertEqual(leaked, [], 'Archived v1 files belong under v1/ so they stay separated from v2.')

    def test_core_stays_headless(self):
        bad = []
        for p in (ROOT / 'core').glob('*.py'):
            for name, line in imported(p):
                top = name.split('.')[0]
                if top == 'ui' or (top == 'qualify' and name not in CORE_MAY_IMPORT_FROM_QUALIFY):
                    bad.append(f'{p.relative_to(ROOT)}:{line} {name}')
        self.assertEqual(bad, [], 'core/ is the headless engine: it never imports ui/, and from qualify/ only the '
                         'qualification gates (run, scorers, decide_eval). Move shared code into core/ instead.')

    def test_tests_cannot_reach_the_network(self):
        import os
        import socket
        if os.environ.get('COFINANCE_NETWORK_TESTS') == '1':
            self.skipTest('live network checks opted in')
        with self.assertRaises(RuntimeError, msg='The no-network guard is not installed. Run the suite as '
                               '`python -m unittest discover -s tests -t .` so tests/__init__.py is imported.'):
            socket.getaddrinfo('example.com', 443)


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

    def test_readme_benchmarks_pass_checklist(self):
        import json
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        targets = re.findall(r'\]\((docs/benchmarks/[^)#\s]+)\)', readme)
        self.assertTrue(targets, 'README.md must link at least one benchmark artifact under docs/benchmarks/.')
        failures = []
        for rel in targets:
            p = ROOT / rel
            if not p.exists():
                failures.append(f'{rel}: missing file')
                continue
            if p.suffix == '.md':
                txt = p.read_text(encoding='utf-8')
                low = txt.lower()
                if '[!warning]' not in low or 'experimental' not in low:
                    failures.append(f'{rel}: missing Experimental warning')
                linked = re.findall(r'\]\(([^)#\s]+\.jsonl?)\)', txt)
                if not linked or not any((p.parent / link).exists() for link in linked):
                    failures.append(f'{rel}: missing existing sibling .json/.jsonl result link')
                if 'protocol' not in low and not re.search(r'\b[0-9a-f]{8,64}\b', txt):
                    failures.append(f'{rel}: missing frozen protocol or hash provenance')
                if not any(w in low for w in ('malformed', 'error', 'abstain', 'abstention', 'skipped', 'failure', 'fallback')):
                    failures.append(f'{rel}: missing error/abstention accounting')
                if not any(w in low for w in ('baseline', 'direct', 'always-up', 'crowd', 'unchanged')):
                    failures.append(f'{rel}: missing baseline comparison')
                if not any(w in low for w in ('not tested', 'what was not', 'limits', 'limitations', 'not proven', 'does not', 'not a product-accuracy', 'not an independent')):
                    failures.append(f'{rel}: missing explicit scope limits or what was not tested')
            elif p.suffix == '.json':
                data = json.loads(p.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    failures.append(f'{rel}: JSON report must be an object')
                    continue
                if 'experimental' not in str(data.get('warning', '')).lower():
                    failures.append(f'{rel}: JSON report missing experimental warning')
                if not data.get('limits'):
                    failures.append(f'{rel}: JSON report missing limits')
                if not any('sha256' in k.lower() for k in data):
                    failures.append(f'{rel}: JSON report missing sha256 provenance')
        self.assertEqual(failures, [], 'Every README benchmark line must pass the Benchmark Checklist before publishing.')

    def test_linked_evidence_exists(self):
        missing = []
        for doc in [ROOT / 'README.md', *(ROOT / 'docs').rglob('*.md')]:
            for target in re.findall(r'\]\(([^)#\s]+\.(?:json|jsonl|md|png))\)', doc.read_text(encoding='utf-8')):
                if not target.startswith('http') and not (doc.parent / target).exists():
                    missing.append(f'{doc.relative_to(ROOT)} -> {target}')
        self.assertEqual(missing, [], 'A doc cites a result file that is not in the repo. Publish the compact report '
                         'next to the write-up, or fix the link; claims must point at evidence a reader can open.')

    def test_private_state_is_ignored(self):
        ignored = set((ROOT / '.gitignore').read_text(encoding='utf-8').split())
        self.assertTrue({'data/', '.env', '.fabrika/'} <= ignored, 'data/ holds databases, traces, models and corpora; '
                        '.env holds secrets; .fabrika/ is a local agent spend ledger. None of them may be committed.')


EXCUSE_WORDS = re.compile(r'(?:#|//).*\b(?:hack|workaround|work around|for now|temporary fix|kludge|fixme|xxx)\b', re.I)
TIMER_CALL = re.compile(r'\b(?:setTimeout|setInterval|requestAnimationFrame|requestIdleCallback)\(')
TIMER_BUDGET = 12   # 2026-11-16 baseline: polling loops, request timeouts and focus restore. Lower it, never raise it silently.


class TestCodeHygiene(unittest.TestCase):
    def test_no_comments_that_excuse_workarounds(self):
        code = [p for base in V2_CODE for p in files(base, '.py', '.ts', '.tsx') if '.test.' not in p.name]
        hits = [f'{p.relative_to(ROOT)}:{i}' for p in code
                for i, line in enumerate(p.read_text(encoding='utf-8', errors='ignore').splitlines(), 1) if EXCUSE_WORDS.search(line)]
        self.assertEqual(hits, [], 'A comment that excuses a workaround gets copied by the next agent. Fix the root cause, '
                         'or turn the constraint into a named check and delete the comment.')

    def test_ui_timer_count_does_not_grow(self):
        n = sum(len(TIMER_CALL.findall(p.read_text(encoding='utf-8'))) for p in files('frontend/src', '.ts', '.tsx')
                if '.test.' not in p.name)
        self.assertLessEqual(n, TIMER_BUDGET, f'{n} timer calls in frontend/src (budget {TIMER_BUDGET}). Timers used to '
                             'patch ordering bugs cause jank and GC churn. Use an event, an effect cleanup or the existing '
                             'refresh loops: lib/live-sync.ts (stream first, timer only as fallback) and lib/use-dashboard.ts.')


class TestWriting(unittest.TestCase):
    def test_private_todo_stays_excluded(self):
        ignored = set((ROOT / '.gitignore').read_text(encoding='utf-8').split())
        self.assertIn('/TODO.md', ignored, 'Private planning is not a public repository artifact. Keep /TODO.md ignored.')
        self.assertFalse((ROOT / 'TODO.md').exists(), 'Do not recreate the removed root TODO.md. Public docs describe the product and reproducible results.')

    def test_active_docs_do_not_publish_private_workflow_locations(self):
        private = ('https://app.clickup.com/', 'private roadmap', ':' + chr(92) + 'users' + chr(92), ':/users/')
        targets = (ROOT / 'README.md', ROOT / 'docs/benchmarks/REPLAY.md')
        hits = [str(p.relative_to(ROOT)) for p in targets if p.exists() and
                any(marker in p.read_text(encoding='utf-8').lower() for marker in private)]
        self.assertEqual(hits, [], 'Public product docs must not publish personal PC paths, private workspace links or private planning locations. Keep only product behavior, reproduction steps and evidence.')

    def test_no_em_or_en_dashes_in_docs_or_ui(self):
        targets = [ROOT / 'README.md', ROOT / 'frontend/README.md', *(ROOT / 'docs').rglob('*.md'),
                   *files('frontend/src', '.tsx', '.ts')]
        hits = sorted(str(p.relative_to(ROOT)) for p in targets if p.exists() and DASHES.search(p.read_text(encoding='utf-8')))
        self.assertEqual(hits, [], 'Em and en dashes render badly in chat clients and read as machine prose. Use a '
                         'colon, comma or parentheses.')


if __name__ == '__main__':
    unittest.main()
