import unittest

from personalization_profile import bounded_profile


class PersonalizationProfileTests(unittest.TestCase):
  def test_profile_keeps_bounded_confirmed_provenance(self):
    result = bounded_profile({
      "schema": 1,
      "generated_at": "now",
      "source_commit": "abc",
      "confirmed": [{
        "id": "pref",
        "title": "Pref",
        "description": "Evidence",
      }],
    })
    self.assertTrue(result["available"])
    self.assertEqual(result["source_commit"], "abc")
    self.assertEqual(result["confirmed"][0]["id"], "pref")
    self.assertEqual(
      set(result),
      {"schema", "available", "staged_at", "generated_at", "source_commit", "confirmed"},
    )


if __name__ == "__main__":
  unittest.main()
