import unittest

from personalization_profile import bounded_profile


class PersonalizationProfileTests(unittest.TestCase):
  def test_profile_keeps_provenance_classes_separate(self):
    result = bounded_profile({
      "schema": 1,
      "generated_at": "now",
      "source_commit": "abc",
      "confirmed": [{
        "id": "pref",
        "title": "Pref",
        "description": "Evidence",
      }],
      "priorities": ["Ship the base layer"],
      "boundaries": ["No silent permission"],
      "hypotheses": ["May prefer short briefs"],
    })
    self.assertTrue(result["available"])
    self.assertEqual(result["confirmed"][0]["id"], "pref")
    self.assertEqual(result["priorities"], ["Ship the base layer"])
    self.assertEqual(result["boundaries"], ["No silent permission"])
    self.assertEqual(result["hypotheses"], ["May prefer short briefs"])


if __name__ == "__main__":
  unittest.main()
