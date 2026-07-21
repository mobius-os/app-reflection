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
    self.assertIn("memory-health.json", goal)
    self.assertIn("meta-state-status.json", goal)
    self.assertIn("different scopes", goal)
    self.assertIn("Memory is the sole writer", goal)
    self.assertIn("do not repeat a hardened check", goal)

  def test_turn_steering_respects_an_already_written_brief(self):
    soft, _ = reflection_runner.steering_thresholds(60)
    message = reflection_runner.steering_message(
      soft - 1, soft, 60, brief_written=True,
    )
    self.assertIn("floor deliverable is already written", message)
    self.assertIn("Do not replace", reflection_runner.steering_message(
      44, 45, 60, brief_written=True,
    ))


class UsageLimitClassificationTests(unittest.TestCase):
  def test_monthly_spend_limit_routes_to_static_brief_path(self):
    message = "You've hit your monthly spend limit · raise it in usage settings"
    self.assertTrue(reflection_runner._is_usage_limit(message))

  def test_generic_model_error_is_not_misclassified_as_usage_limit(self):
    self.assertFalse(reflection_runner._is_usage_limit("The model process exited unexpectedly"))


if __name__ == "__main__":
  unittest.main()
