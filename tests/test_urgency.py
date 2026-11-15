"""Rule urgency on hand-written headlines that are NOT in any corpus (a small overfitting probe, not a benchmark)."""
import unittest

from core.urgency import urgency

URGENT = [
    ('Intel cuts full-year guidance as PC demand slows', 'results'),
    ('Netflix misses analyst estimates for subscriber growth', 'results'),
    ('Microsoft reports fiscal first quarter results', 'results'),
    ('Meta shares plunge after ad revenue warning', 'market_move'),
    ('Nvidia loses $300B in market value in a single session', 'market_move'),
    ('Ford halts F-150 Lightning production', 'production_stop'),
    ('Toyota recalls 1,200,000 vehicles over airbag defect', 'production_stop'),
    ('China bans Micron chip imports for critical infrastructure', 'trade_block'),
    ('US blocks Nvidia exports to the Middle East pending licence review', 'trade_block'),
    ('Boeing CEO steps down amid safety crisis', 'leadership_exit'),
    ('Intel chief executive ousted by board after turnaround stalls', 'leadership_exit'),
    ('Starbucks announces CEO transition effective immediately', 'leadership_exit'),
    ('Activist investor moves to oust Disney chairman', 'leadership_exit'),
    ('SEC opens investigation into Super Micro accounting', 'investigation'),
    ('Board retains outside counsel to review executive conduct', 'investigation'),
    ('AWS suffers widespread outage across US East', 'major_outage'),
    ('Google Cloud still down after 9 hours', 'major_outage'),
]
NOT_URGENT = [
    'NVIDIA Sets Conference Call for Third-Quarter Financial Results',
    'Apple to discuss its financial results on a call next week',
    'A history of Apple product launches',
    'Show HN: a dashboard for tracking earnings season',
    'Production designer leaves Apple TV series',
    'Why market cap is a misleading metric',
    'Tesla Roadster demo hits 60 mph in under a second',
    'Former Apple CEO John Sculley shares leadership lessons',
    'How CEO succession planning works at large companies',
    'Ask HN: Is the Slack outage just me?',
    'The investigation of a 1990s Unix bug',
    "Postmortem of last year's major outage at Fastly",
    'Ex-Google CFO joins startup board',
]


class TestRuleUrgency(unittest.TestCase):
    def test_unseen_urgent_headlines(self):
        for title, rule in URGENT:
            hit = urgency(title)
            self.assertIsNotNone(hit, title)
            self.assertEqual(hit.rule, rule, title)

    def test_unseen_routine_headlines(self):
        for title in NOT_URGENT:
            self.assertIsNone(urgency(title), title)

    def test_hn_comment_text_is_ignored_but_official_summary_counts(self):
        self.assertIsNone(urgency('Ask HN: favourite trucks', 'Tesla ends Cybertruck production, says a comment', 'hackernews'))
        self.assertEqual(urgency('Apple newsroom update', 'Apple reports fourth quarter results today.', 'apple_newsroom').rule, 'results')

    def test_hit_explains_itself(self):
        hit = urgency('Alphabet shares tumble in $500B rout')
        self.assertEqual(hit.rule, 'market_move')
        self.assertEqual(hit.matched.lower(), 'shares tumble')


if __name__ == '__main__':
    unittest.main()
