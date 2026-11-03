import unittest

from scripts.bump_version import next_version


class VersionBumpTests(unittest.TestCase):
    def test_patch_increments_for_each_versioned_commit(self):
        self.assertEqual(
            next_version(
                "0.1.0",
                [self.commit("fix: correct parsing"), self.commit("doc: clarify setup")],
            ),
            "0.1.2",
        )

    def test_highest_impact_prefix_wins_for_pull_request(self):
        self.assertEqual(
            next_version(
                "0.1.7",
                [
                    self.commit("fix: correct parsing"),
                    self.commit("sec: rotate token"),
                    self.commit("feat: add report export"),
                ],
            ),
            "0.2.10",
        )

    def test_major_increments_major_and_preserves_build_increment(self):
        self.assertEqual(
            next_version("1.4.9", [self.commit("major: remove legacy endpoint")]),
            "2.0.10",
        )

    def test_chore_does_not_change_version(self):
        self.assertIsNone(next_version("0.1.0", [self.commit("chore: update dependency")]))

    def test_unknown_prefix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must start with"):
            next_version("0.1.0", [self.commit("refactor: simplify parser")])

    def test_invalid_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid VERSION"):
            next_version("v0.1.0", [self.commit("fix: correct parsing")])

    @staticmethod
    def commit(message):
        return {"commit": {"message": message}}


if __name__ == "__main__":
    unittest.main()
