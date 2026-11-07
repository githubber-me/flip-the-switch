"""End-to-end checks for tools/studio.py and its spending gate. No account and no credits.

python3 -m unittest discover tests
"""
import atexit
import http.server
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SHOTS = {"shots": [{"id": "s01", "seconds": 2, "picture": "a", "vo": "one two"}, {"id": "s02", "seconds": 2, "picture": "b", "vo": "three"}]}


class Project:
    """A throwaway copy of the tool, so tests never touch real productions."""

    def __init__(self, **overrides):
        work = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, work, True)
        self.root = pathlib.Path(work) / "repo"
        (self.root / "tools").mkdir(parents=True)
        shutil.copy(REPO / "tools" / "studio.py", self.root / "tools")
        cfg = json.loads((REPO / "studio.json").read_text())
        cfg.update({"mode": "dry", "aspect": "16:9", **overrides})
        (self.root / "studio.json").write_text(json.dumps(cfg))
        self.prod = self.root / "productions" / "t"
        self.prod.mkdir(parents=True)
        (self.prod / "shots.json").write_text(json.dumps(SHOTS))

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.root / "tools" / "studio.py"), *args], capture_output=True, text=True)

    def ok(self, *args):
        result = self.run(*args)
        assert result.returncode == 0, result.stderr
        return result.stdout


class DryMode(unittest.TestCase):
    def test_whole_film_costs_nothing(self):
        project = Project()
        for shot in ("s01", "s02"):
            project.ok("still", "t", shot, "--prompt", "a test frame")
            project.ok("approve", "t", shot, "1")
            project.ok("clip", "t", shot, "--prompt", "move")
            project.ok("voice", "t", shot, "--text", "one two")
        project.ok("music", "t", "--prompt", "drums", "--seconds", "4")
        project.ok("cut", "t")
        final = project.prod / "final.mp4"
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(final)],
                               capture_output=True, text=True, check=True).stdout
        info = json.loads(probe)
        self.assertAlmostEqual(float(info["format"]["duration"]), 4.0, delta=0.2)
        self.assertEqual(sorted(s["codec_type"] for s in info["streams"]), ["audio", "video"])
        self.assertIn("spent: $0.00", project.ok("status"))

    def test_clip_needs_a_still(self):
        result = Project().run("clip", "t", "s01", "--prompt", "move")
        self.assertEqual(result.returncode, 1)
        self.assertIn("approved stills", result.stderr)

    def test_clip_refuses_an_unapproved_still(self):
        project = Project()
        project.ok("still", "t", "s01", "--prompt", "first")
        result = project.run("clip", "t", "s01", "--prompt", "move")
        self.assertEqual(result.returncode, 1)
        self.assertIn("approved stills", result.stderr)

    def test_clip_uses_the_approved_take_not_the_newest(self):
        project = Project()
        project.ok("still", "t", "s01", "--prompt", "good")
        project.ok("still", "t", "s01", "--prompt", "rejected")
        project.ok("approve", "t", "s01", "1")
        self.assertIn("from s01_t1.png", project.ok("clip", "t", "s01", "--prompt", "move"))

    def test_reject_withdraws_an_approval(self):
        project = Project()
        project.ok("still", "t", "s01", "--prompt", "first")
        project.ok("approve", "t", "s01", "1")
        project.ok("reject", "t", "s01")
        self.assertEqual(project.run("clip", "t", "s01", "--prompt", "move").returncode, 1)

    def test_approve_needs_a_real_take(self):
        project = Project()
        project.ok("still", "t", "s01", "--prompt", "first")
        result = project.run("approve", "t", "s01", "2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no still", result.stderr)

    def test_quote_names_shots_still_waiting_for_approval(self):
        project = Project()
        project.ok("still", "t", "s01", "--prompt", "first")
        project.ok("approve", "t", "s01", "1")
        self.assertIn("NOT APPROVED YET: s02.", project.ok("quote", "t"))

    def test_new_takes_do_not_overwrite(self):
        project = Project()
        project.ok("still", "t", "s01", "--prompt", "first")
        project.ok("still", "t", "s01", "--prompt", "second")
        self.assertTrue((project.prod / "stills" / "s01_t1.png").exists())
        self.assertTrue((project.prod / "stills" / "s01_t2.png").exists())

    def test_quote_reports_the_lead_in(self):
        out = Project().ok("quote", "t")
        self.assertIn("s01: 3 s (used 2 s) $0.40", out)
        self.assertIn("TOTAL $0.81 for 2 clips", out)


class Files(http.server.BaseHTTPRequestHandler):
    """Stands in for Gulab's file hosting."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "9")
        self.end_headers()
        self.wfile.write(b"\x89PNG fake")


class LiveMode(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Files)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_still_prints_the_call_and_a_saved_take_feeds_the_clip(self):
        project = Project(mode="live", gulab_board="board1", ref_urls={"bible/a.png": "https://host/a.png"})
        (project.root / "bible").mkdir()
        (project.root / "bible" / "a.png").write_bytes(b"x")
        out = project.ok("still", "t", "s01", "--prompt", "frame", "--ref", "bible/a.png")
        self.assertIn("TOOL generate_image", out)
        self.assertIn("google/nano-banana-2.1/edit", out)
        self.assertIn("https://host/a.png", out)
        self.assertIn("board1", out)
        self.assertFalse((project.prod / "stills").exists())
        project.ok("save", "t", "still", "s01", "--url", f"{self.base}/one.png")
        project.ok("save", "t", "still", "s01", "--url", f"{self.base}/two.png")
        self.assertTrue((project.prod / "stills" / "s01_t1.png").exists())
        project.ok("approve", "t", "s01", "1")
        out = project.ok("clip", "t", "s01", "--prompt", "move")
        self.assertIn("TOOL generate_video", out)
        self.assertIn(f"{self.base}/one.png", out)
        self.assertIn('"duration": "3"', out)

    def test_unhosted_reference_is_refused(self):
        project = Project(mode="live")
        (project.root / "a.png").write_bytes(b"x")
        result = project.run("still", "t", "s01", "--prompt", "frame", "--ref", "a.png")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ref_urls", result.stderr)

    def test_command_refuses_past_the_cap(self):
        result = Project(mode="live", budget_usd=0.05).run("still", "t", "s01", "--prompt", "frame")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cap", result.stderr)


class Gate(unittest.TestCase):
    STILL = {"model": "google/nano-banana-2.1", "prompt": "x"}
    CLIP = {"model": "fal-ai/kling-video/o3/standard/image-to-video", "prompt": "x", "duration": "5", "image_url": "https://host/s01.png"}
    MUSIC = {"kind": "music", "model": "fal-ai/elevenlabs/music", "text": "drums", "duration": 12}
    VOICE = {"kind": "speech", "model": "elevenlabs/tts/eleven-v4", "text": "one line"}

    def gate(self, project, event, tool, call):
        return subprocess.run([sys.executable, str(project.root / "tools" / "studio.py"), "gate"], capture_output=True, text=True,
                              input=json.dumps({"hook_event_name": event, "tool_name": f"mcp__claude_ai_Gulab__{tool}", "tool_input": call}))

    def approve_still(self, project):
        stills = project.prod / "stills"
        stills.mkdir()
        (stills / "s01_t1.png").write_bytes(b"x")
        (stills / "s01_t1.url").write_text(self.CLIP["image_url"])
        project.ok("approve", "t", "s01", "1")

    def test_dry_mode_blocks_every_paid_tool(self):
        result = self.gate(Project(), "PreToolUse", "generate_image", self.STILL)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Dry mode", result.stderr)

    def test_live_call_passes_and_is_charged_only_after_it_runs(self):
        project = Project(mode="live")
        self.approve_still(project)
        self.assertEqual(self.gate(project, "PreToolUse", "generate_video", self.CLIP).returncode, 0)
        self.assertIn("spent: $0.00", project.ok("status"))
        self.assertEqual(self.gate(project, "PostToolUse", "generate_video", self.CLIP).returncode, 0)
        self.assertIn("spent: $0.67", project.ok("status"))

    def test_cap_blocks_the_next_call(self):
        project = Project(mode="live", budget_usd=0.7)
        self.gate(project, "PostToolUse", "generate_video", self.CLIP)
        result = self.gate(project, "PreToolUse", "generate_video", self.CLIP)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cap", result.stderr)

    def test_unapproved_calls_are_blocked(self):
        project = Project(mode="live")
        for tool, call in (("generate_video", {**self.CLIP, "model": "fal-ai/veo3.1"}), ("generate_image", {"prompt": "no model"}),
                           ("generate_image", {**self.STILL, "num_images": 4}), ("generate_audio", {"kind": "sound_effect", "text": "x"})):
            self.assertEqual(self.gate(project, "PreToolUse", tool, call).returncode, 2, call)

    def test_clip_from_an_unapproved_image_is_blocked(self):
        project = Project(mode="live")
        self.approve_still(project)
        result = self.gate(project, "PreToolUse", "generate_video", {**self.CLIP, "image_url": "https://host/rejected.png"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("approved stills", result.stderr)

    def test_audio_length_is_limited(self):
        project = Project(mode="live")
        self.assertEqual(self.gate(project, "PreToolUse", "generate_audio", self.MUSIC).returncode, 0)
        self.assertEqual(self.gate(project, "PreToolUse", "generate_audio", self.VOICE).returncode, 0)
        for call in ({**self.MUSIC, "duration": 300}, {k: v for k, v in self.MUSIC.items() if k != "duration"},
                     {**self.MUSIC, "duration": "long"}, {**self.VOICE, "text": "x" * 301}):
            self.assertEqual(self.gate(project, "PreToolUse", "generate_audio", call).returncode, 2, call)

    def test_a_failing_gate_blocks_the_call(self):
        broken_ledger = Project(mode="live")
        (broken_ledger.root / "productions" / "ledger.jsonl").write_text('{"cost_usd": 1')
        self.assertEqual(self.gate(broken_ledger, "PreToolUse", "generate_video", self.CLIP).returncode, 2)

        broken_config = Project(mode="live")
        (broken_config.root / "studio.json").write_text("{oops")
        self.assertEqual(self.gate(broken_config, "PreToolUse", "generate_video", self.CLIP).returncode, 2)

        project = Project(mode="live")
        bad_input = subprocess.run([sys.executable, str(project.root / "tools" / "studio.py"), "gate"], capture_output=True, text=True, input="not json")
        self.assertEqual(bad_input.returncode, 2)
        self.assertIn("BLOCKED", bad_input.stderr)

    def test_agents_are_denied_edits_to_the_money_files(self):
        deny = json.loads((REPO / ".claude" / "settings.json").read_text())["permissions"]["deny"]
        for rule in ("Edit(/studio.json)", "Edit(/productions/ledger.jsonl)", "Edit(/productions/*/approved.json)"):
            self.assertIn(rule, deny)


if __name__ == "__main__":
    unittest.main()
