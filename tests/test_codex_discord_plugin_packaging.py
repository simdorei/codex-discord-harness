from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import TypedDict, cast


class ManifestInterface(TypedDict):
    longDescription: str


class PluginManifest(TypedDict):
    skills: str
    version: str
    interface: ManifestInterface


class DiscordPluginPackagingTests(unittest.TestCase):
    def test_plugin_packages_workflow_skills(self) -> None:
        plugin_root = Path("plugins/codex-discord-remote")
        manifest = cast(
            PluginManifest,
            json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")),
        )
        skill_text = (plugin_root / "skills/deep-interview/SKILL.md").read_text(encoding="utf-8")
        auto_research = plugin_root / "skills/deep-interview/auto-research-greenfield.md"
        auto_answer = plugin_root / "skills/deep-interview/auto-answer-uncertain.md"
        notice = plugin_root / "skills/deep-interview/NOTICE.md"
        removed_skill_dirs = [
            plugin_root / "skills/github-project-triage",
            plugin_root / "skills/maintainer-orchestrator",
        ]
        readme = Path("README.md").read_text(encoding="utf-8")
        root_notice = Path("NOTICE.md").read_text(encoding="utf-8")
        long_description = manifest["interface"]["longDescription"]
        ask_skill = (
            plugin_root / "skills/ask-chatgpt-pro/SKILL.md"
        ).read_text(encoding="utf-8")
        source_skill = Path(
            ".agents/skills/ask-chatgpt-pro/SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["version"], "0.1.0+codex.20260730203000")
        self.assertEqual(ask_skill, source_skill)
        self.assertIn("select_project", ask_skill)
        self.assertIn("file_apply_patch", ask_skill)
        self.assertIn("git_push", ask_skill)
        self.assertIn("deep interview", long_description)
        self.assertNotIn("GitHub triage", long_description)
        self.assertNotIn("maintainer orchestration", long_description)
        self.assertIn("name: deep-interview", skill_text)
        self.assertIn("\uc791\uc5c5 \uad6c\uc870", skill_text)
        self.assertIn("Phase 0: Resolve Ambiguity Threshold", skill_text)
        self.assertIn("Ontology Convergence", skill_text)
        self.assertIn("pending-approval ticket", skill_text)
        self.assertIn("Do not silently fill missing requirements", skill_text)
        self.assertTrue(auto_research.is_file())
        self.assertTrue(auto_answer.is_file())
        self.assertIn("MIT License", notice.read_text(encoding="utf-8"))
        self.assertIn("`deep-interview`", readme)
        for removed_skill_dir in removed_skill_dirs:
            self.assertFalse(removed_skill_dir.exists())
        self.assertNotIn("steipete/agent-scripts", root_notice)
        self.assertNotIn("`github-project-triage`", readme)
        self.assertNotIn("`maintainer-orchestrator`", readme)


if __name__ == "__main__":
    _ = unittest.main()
