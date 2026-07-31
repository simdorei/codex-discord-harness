from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import BaseModel, Field


class ManifestInterface(BaseModel):
    long_description: str = Field(alias="longDescription")


class PluginManifest(BaseModel):
    skills: str
    version: str
    interface: ManifestInterface


def _parse_manifest(path: Path) -> PluginManifest:
    return PluginManifest.model_validate_json(path.read_text(encoding="utf-8"))


class DiscordPluginPackagingTests(unittest.TestCase):
    def test_plugin_packages_workflow_skills(self) -> None:
        plugin_root = Path("plugins/codex-discord-remote")
        manifest = _parse_manifest(plugin_root / ".codex-plugin/plugin.json")
        skill_text = (plugin_root / "skills/deep-interview/SKILL.md").read_text(
            encoding="utf-8"
        )
        auto_research = (
            plugin_root / "skills/deep-interview/auto-research-greenfield.md"
        )
        auto_answer = plugin_root / "skills/deep-interview/auto-answer-uncertain.md"
        notice = plugin_root / "skills/deep-interview/NOTICE.md"
        removed_skill_dirs = [
            plugin_root / "skills/github-project-triage",
            plugin_root / "skills/maintainer-orchestrator",
        ]
        ask_skill = (plugin_root / "skills/ask-chatgpt-pro/SKILL.md").read_text(
            encoding="utf-8"
        )
        source_skill = Path(".agents/skills/ask-chatgpt-pro/SKILL.md").read_text(
            encoding="utf-8"
        )
        conversation_map = (
            plugin_root / "skills/ask-chatgpt-pro/scripts/conversation_map.py"
        )

        self.assertEqual(manifest.skills, "./skills/")
        self.assertRegex(manifest.version, r"^0\.1\.0\+codex\.\d{14}$")
        self.assertEqual(ask_skill, source_skill)
        self.assertIn("select_project", ask_skill)
        self.assertIn("file_apply_patch", ask_skill)
        self.assertIn("git_push", ask_skill)
        self.assertIn("conversation_map.py", ask_skill)
        self.assertTrue(conversation_map.is_file())
        self.assertIn("name: deep-interview", skill_text)
        self.assertTrue(auto_research.is_file())
        self.assertTrue(auto_answer.is_file())
        self.assertIn("MIT License", notice.read_text(encoding="utf-8"))
        for removed_skill_dir in removed_skill_dirs:
            self.assertFalse(removed_skill_dir.exists())


if __name__ == "__main__":
    _ = unittest.main()
