import unittest

import reflection_runner


class AdaptiveReflectionGoalTests(unittest.TestCase):
  def test_goal_stages_meta_model_and_bounded_system_evidence(self):
    goal = reflection_runner.build_goal({})
    self.assertIn("meta-state.md", goal)
    self.assertIn("meta-learning.jsonl", goal)
    self.assertIn("resource-snapshot.json", goal)
    self.assertIn("resource-history.jsonl", goal)
    self.assertIn("resource-decisions.jsonl", goal)
    self.assertIn("do not repeat a hardened check", goal)


class UsageLimitClassificationTests(unittest.TestCase):
  def test_monthly_spend_limit_routes_to_static_brief_path(self):
    message = "You've hit your monthly spend limit · raise it in usage settings"
    self.assertTrue(reflection_runner._is_usage_limit(message))

  def test_generic_model_error_is_not_misclassified_as_usage_limit(self):
    self.assertFalse(reflection_runner._is_usage_limit("The model process exited unexpectedly"))


if __name__ == "__main__":
  unittest.main()
