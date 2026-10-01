#!/usr/bin/env python3
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_operational_skill_and_independent_verification_contract(self):
        skill = (ROOT / 'SKILL.md').read_text('utf-8')
        new = (ROOT / 'NewKnowledge.md').read_text('utf-8')
        self.assertLess(len(skill.split()), 1300)
        for phrase in ('NewKnowledge.md', 'KnowledgeStore.md', 'scripts/knowledge.py',
                       'Keep unknown fields unknown', 'runtime-verified', 'Completion criterion'):
            self.assertIn(phrase, skill)
        for phrase in ('one concept', 'donor', 'main agent must not'):
            self.assertIn(phrase, skill.lower())
        for phrase in ('cheap collector', 'independent verifier', 'original evidence',
                       'candidate', 'attach-evidence', 'conflict', 'needs-runtime',
                       'must never treat that same proposal as independent corroboration',
                       'independent extraction', "collector's reasoning", 'phase a', 'phase b',
                       "do **not** show the collector's candidate yet", 'scripts/knowledge.py',
                       'sqlite triggers'):
            self.assertIn(phrase, new.lower())


if __name__ == '__main__':
    unittest.main()
