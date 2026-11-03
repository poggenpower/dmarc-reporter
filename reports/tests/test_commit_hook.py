import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"


class CommitHookTests(unittest.TestCase):
    def test_accepts_documented_prefixes(self):
        for prefix in ("major", "feat", "sec", "fix", "doc", "chore"):
            with self.subTest(prefix=prefix):
                result = self.run_hook(f"{prefix}: update behavior\n")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_or_invalid_prefix(self):
        for message in (
            "update behavior\n",
            "Feat: update behavior\n",
            "feat:update behavior\n",
            "feat: \n",
        ):
            with self.subTest(message=message):
                result = self.run_hook(message)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Invalid commit message", result.stderr)

    def run_hook(self, message):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as message_file:
            message_file.write(message)
            message_file.flush()
            return subprocess.run(
                [str(HOOK), message_file.name],
                capture_output=True,
                check=False,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
