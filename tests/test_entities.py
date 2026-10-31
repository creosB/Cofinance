import re
import unittest
from core.entities import extract_tickers

LEGACY = re.compile(r"\$?[A-Z]{1,5}")   # copied from utils/memory.py

def legacy(text):
    out = set()
    for tok in LEGACY.findall(text or ""):
        c = tok[1:] if tok.startswith("$") else tok
        if 1 <= len(c) <= 5 and c.isupper():
            out.add(c)
    return sorted(out)

UNIVERSE = {"NVDA", "AMD", "TSLA", "SOL", "BTC", "AAPL", "MSFT", "GOOGL", "AI", "A", "ALL", "IT"}

class TestEntities(unittest.TestCase):
    def test_legacy_is_broken(self):          # documents the defect being replaced
        self.assertIn("S", legacy("Should I buy NVDA or AMD after earnings?"))
        self.assertEqual(legacy("What about Tesla?"), ["T", "W"])

    def test_basic(self):
        self.assertEqual(extract_tickers("Should I buy NVDA or AMD after earnings?", UNIVERSE), ["NVDA", "AMD"])

    def test_capitalised_words_are_not_tickers(self):
        self.assertEqual(extract_tickers("What about Tesla?", UNIVERSE), [])      # name->ticker is a separate lookup
        self.assertEqual(extract_tickers("I think THE AI trade is over and USA rates are up", UNIVERSE), [])

    def test_ambiguous_symbols_need_cashtag(self):
        self.assertEqual(extract_tickers("IT is ALL about A", UNIVERSE), [])
        self.assertEqual(extract_tickers("watching $AI and $a today", UNIVERSE), ["AI", "A"])

    def test_cashtag_and_dedupe(self):
        self.assertEqual(extract_tickers("$sol vs BTC, then SOL again", UNIVERSE), ["SOL", "BTC"])

    def test_substrings_do_not_match(self):
        self.assertEqual(extract_tickers("NVDAX and xAMD and AMD1", UNIVERSE), [])

if __name__ == "__main__":
    unittest.main()
