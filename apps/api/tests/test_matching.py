import unittest

from app.domains.matching.service import calculate_match_score, route_candidate


class MatchingServiceTests(unittest.TestCase):
    def test_calculates_weighted_score_from_three_dimensions(self):
        result = calculate_match_score(skill_score=90, experience_score=80, completeness_score=70)

        self.assertEqual(result.score, 82.0)
        self.assertEqual(result.breakdown["skills"], 36.0)
        self.assertEqual(result.breakdown["experience"], 32.0)
        self.assertEqual(result.breakdown["completeness"], 14.0)

    def test_rejects_scores_outside_zero_to_one_hundred(self):
        with self.assertRaises(ValueError):
            calculate_match_score(skill_score=101, experience_score=80, completeness_score=70)

    def test_routes_score_to_configured_pipeline(self):
        self.assertEqual(route_candidate(80), "INTERVIEW")
        self.assertEqual(route_candidate(60), "QUESTIONNAIRE")
        self.assertEqual(route_candidate(40), "TALENT_POOL")
        self.assertEqual(route_candidate(39.9), "CLOSED")


if __name__ == "__main__":
    unittest.main()
