import unittest

from scripts.bump_version import next_version, next_version_for_pull_requests
from scripts.dev_version import dev_version
from scripts.release_prs_since_tag import pull_requests_since_tag


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

    def test_release_version_applies_each_merged_pull_request_in_order(self):
        self.assertEqual(
            next_version_for_pull_requests(
                "0.1.1",
                [
                    [self.commit("sec: rotate credentials")],
                    [self.commit("feat: add report export")],
                    [self.commit("chore: update dependency")],
                ],
            ),
            "0.3.3",
        )

    def test_release_version_skips_chore_only_pull_requests(self):
        self.assertIsNone(
            next_version_for_pull_requests(
                "0.1.1",
                [[self.commit("chore: update dependency")]],
            )
        )

    def test_development_version_increments_patch_and_includes_branch_build_and_sha(self):
        self.assertEqual(
            dev_version("0.1.9", "feature/email-report", "42", "abcdef0123456789"),
            "0.1.10-feature-email-report.42.abcdef0",
        )

    def test_development_version_sanitizes_and_limits_branch_name(self):
        version = dev_version(
            "1.2.3",
            "Feature/" + "A" * 50,
            "1",
            "1234567",
        )
        self.assertEqual(version, "1.2.4-feature-" + "a" * 32 + ".1.1234567")

    def test_development_version_rejects_invalid_inputs(self):
        with self.assertRaisesRegex(ValueError, "Invalid VERSION"):
            dev_version("v0.1.0", "main", "1", "abcdef0")
        with self.assertRaisesRegex(ValueError, "Build number"):
            dev_version("0.1.0", "main", "0", "abcdef0")
        with self.assertRaisesRegex(ValueError, "Commit SHA"):
            dev_version("0.1.0", "main", "1", "not-a-sha")

    def test_release_pull_requests_follow_main_commit_order_since_tag(self):
        pull_pages = [
            [
                self.pull_request(12, "merge-12"),
                self.pull_request(13, "before-tag"),
                self.pull_request(17, "merge-17"),
                self.pull_request(19, "not-merged", merged_at=None),
            ]
        ]
        self.assertEqual(
            pull_requests_since_tag(pull_pages, ["merge-17", "merge-12"]),
            [17, 12],
        )

    @staticmethod
    def commit(message):
        return {"commit": {"message": message}}

    @staticmethod
    def pull_request(number, merge_commit_sha, merged_at="2026-11-04T17:00:00Z"):
        return {
            "number": number,
            "merge_commit_sha": merge_commit_sha,
            "merged_at": merged_at,
            "base": {"ref": "main"},
        }


if __name__ == "__main__":
    unittest.main()
