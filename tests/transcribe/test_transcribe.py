"""bin/transcribe.py: the worker's only way to hear a voice message. It
runs engine.transcribe_command with {file} replaced, prints what came back,
and refuses anything but a file the monitor downloaded into media/ (so the
worker's narrow tool surface is not widened into "run a command on any
path"). The fake command here is /bin/cat, which makes the audio file's
contents the "transcript".

Usage: [MUT_DIR=<tree>] python3 tests/transcribe/test_transcribe.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MUT = os.environ.get("MUT_DIR") \
    or os.path.dirname(os.path.dirname(HERE))
VOICE_TEXT = "hello from a voice note"


def build(tmp, command):
    """A throwaway package: muse_settings, bin/, one media file, config."""
    pkg = os.path.join(tmp, "pkg")
    os.makedirs(os.path.join(pkg, "bin"))
    os.makedirs(os.path.join(pkg, "media"))
    shutil.copy(os.path.join(MUT, "muse_settings.py"), pkg)
    for name in ("setup_common.py", "transcribe.py"):
        shutil.copy(os.path.join(MUT, "bin", name),
                    os.path.join(pkg, "bin", name))
    with open(os.path.join(pkg, "media", "voice.ogg"), "w") as f:
        f.write(VOICE_TEXT)
    with open(os.path.join(pkg, "config.yaml"), "w") as f:
        f.write('version: 1\nowners: ["111"]\n'
                'token_from: env:MUSE_TEST_TOKEN\n'
                'engine:\n'
                f'  transcribe_command: "{command}"\n')
    return pkg


def run(pkg, *args):
    env = dict(os.environ, MUSE_DISCORD_CONFIG=os.path.join(pkg, "config.yaml"))
    return subprocess.run(
        [sys.executable, os.path.join(pkg, "bin", "transcribe.py")] + list(args),
        env=env, capture_output=True, text=True, timeout=120)


class Transcribe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="muse-stt-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_transcript_is_printed(self):
        pkg = build(self.tmp, "/bin/cat {file}")
        r = run(pkg, os.path.join(pkg, "media", "voice.ogg"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), VOICE_TEXT)

    def test_a_relative_path_inside_media_is_taken(self):
        pkg = build(self.tmp, "/bin/cat {file}")
        r = run(pkg, "media/voice.ogg")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), VOICE_TEXT)

    def test_no_speech_to_text_configured(self):
        pkg = build(self.tmp, "")
        r = run(pkg, os.path.join(pkg, "media", "voice.ogg"))
        self.assertEqual(r.returncode, 3)
        self.assertIn("cannot be heard", r.stderr)

    def test_only_files_the_monitor_downloaded(self):
        pkg = build(self.tmp, "/bin/cat {file}")
        r = run(pkg, os.path.join(pkg, "config.yaml"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        r = run(pkg, os.path.join(pkg, "media", "..", "config.yaml"))
        self.assertEqual(r.returncode, 2)

    def test_a_failing_command_is_reported(self):
        false = "/usr/bin/false" if os.path.exists("/usr/bin/false") \
            else "/bin/false"
        pkg = build(self.tmp, f"{false} {{file}}")
        r = run(pkg, os.path.join(pkg, "media", "voice.ogg"))
        self.assertEqual(r.returncode, 4)
        self.assertIn("exited", r.stderr)

    def test_a_command_that_never_uses_the_file_is_refused(self):
        pkg = build(self.tmp, "/bin/echo hi")
        r = run(pkg, os.path.join(pkg, "media", "voice.ogg"))
        self.assertEqual(r.returncode, 4)
        self.assertIn("does not use {file}", r.stderr)

    def test_a_missing_command_is_reported(self):
        pkg = build(self.tmp, "/nonexistent/stt {file}")
        r = run(pkg, os.path.join(pkg, "media", "voice.ogg"))
        self.assertEqual(r.returncode, 4)
        self.assertIn("not found", r.stderr)

    def test_without_a_path_it_explains_usage(self):
        pkg = build(self.tmp, "/bin/cat {file}")
        r = run(pkg)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
