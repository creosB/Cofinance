"""docs/feature-map.json is the shared vocabulary for features. It must cover every route, view and UI component."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = json.loads((ROOT / 'docs/feature-map.json').read_text(encoding='utf-8'))
FEATURES = MAP['features']
ROUTE = re.compile(r"['\"](/api/[a-z_/-]+)")
HINT = 'Add or fix the entry in docs/feature-map.json (view, api, code, proof) so the next agent can find and prove it.'


def routes():
    found = set()
    sources = [ROOT / 'ui/dashboard.py', ROOT / 'ui/workbench.py',
               *(p for p in (ROOT / 'frontend/src').rglob('*') if p.suffix in ('.ts', '.tsx') and '.test.' not in p.name)]
    for p in sources:
        found |= set(ROUTE.findall(p.read_text(encoding='utf-8')))
    return found


class TestFeatureMap(unittest.TestCase):
    def test_ids_are_unique_and_entries_complete(self):
        ids = [f['id'] for f in FEATURES]
        self.assertEqual(len(ids), len(set(ids)))
        for f in FEATURES:
            self.assertTrue({'id', 'name', 'view', 'reach', 'api', 'code', 'proof'} <= set(f), f['id'])
            self.assertTrue(f['proof'], f'{f["id"]} has no proof. {HINT}')

    def test_every_file_named_exists(self):
        missing = [f'{f["id"]}: {p}' for f in FEATURES for p in f['code'] + f['proof'] if not (ROOT / p).exists()]
        self.assertEqual(missing, [], HINT)

    def test_every_route_belongs_to_a_feature(self):
        claimed = {r for f in FEATURES for r in f['api']}
        self.assertEqual(sorted(routes() - claimed), [], 'An API route has no feature. ' + HINT)
        self.assertEqual(sorted(claimed - routes()), [], 'The map claims a route nothing serves or calls. ' + HINT)

    def test_every_view_belongs_to_a_feature(self):
        app = (ROOT / 'frontend/src/App.tsx').read_text(encoding='utf-8')
        views = set(re.findall(r"\{id:'([a-z_-]+)',name:", app))
        self.assertTrue(views, 'Could not read the views list in App.tsx')
        self.assertEqual(sorted(views - {f['view'] for f in FEATURES}), [], 'A view has no feature. ' + HINT)

    def test_every_ui_component_belongs_to_a_feature(self):
        claimed = {p for f in FEATURES for p in f['code']}
        components = {str(p.relative_to(ROOT)).replace('\\', '/') for p in (ROOT / 'frontend/src/components').glob('*.tsx')
                      if '.test.' not in p.name}
        self.assertEqual(sorted(components - claimed), [], 'A UI component has no feature. ' + HINT)


if __name__ == '__main__':
    unittest.main()
