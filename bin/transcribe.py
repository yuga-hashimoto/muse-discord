#!/usr/bin/env python3
"""transcribe.py -- speech-to-text for a voice message, for the reply worker.

Discord voice messages (and audio attachments) are downloaded by the
monitor into the package's media/ directory and named on the queue item
(`audio`). The worker runs this tool on each one:

    python3 bin/transcribe.py <the file from the item's audio list>

It runs `engine.transcribe_command` from config.yaml with `{file}` replaced
by the audio file, prints what came back (the transcript), and exits:

    0  transcript on stdout
    2  usage or path problem (only media/ files are accepted)
    3  no transcribe_command configured: say you cannot hear it
    4  the command failed or timed out

So the worker never needs a shell, and the only file it can hand to a
speech-to-text tool is one the monitor itself downloaded. The command is
run without a shell (shlex splits it; the path is never parsed as shell
syntax), with a 180-second timeout, and only its stdout is used (capped).

Example config.yaml:

    engine:
      transcribe_command: "whisper-cli -m /models/ggml-base.bin -f {file}"
"""

import os
import shlex
import subprocess
import sys

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PKG_DIR, "bin"))
import setup_common as C  # noqa: E402

MAX_OUTPUT_CHARS = 8000
TIMEOUT_SECS = 180
MEDIA_DIR = os.path.join(PKG_DIR, "media")


def config_path():
    return os.environ.get("MUSE_DISCORD_CONFIG") \
        or os.path.join(PKG_DIR, "config.yaml")


def media_file(arg):
    """The audio file to transcribe, or None: it must resolve to a regular
    file inside the package's media directory (no .. escapes, no symlink
    out of it)."""
    if not arg:
        return None
    path = arg if os.path.isabs(arg) else os.path.join(PKG_DIR, arg)
    try:
        real = os.path.realpath(path)
        root = os.path.realpath(MEDIA_DIR)
        if os.path.commonpath([real, root]) != root:
            return None
    except (OSError, ValueError):
        return None
    return real if os.path.isfile(real) else None


def transcription_command(st):
    """engine.transcribe_command as a argv list (with {file} replaced), or
    None when it is not set."""
    raw = (st.engine or {}).get("transcribe_command")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw


def run(path, command):
    """(exit code, text): the command's stdout, capped."""
    argv = [part.replace("{file}", path) for part in shlex.split(command)]
    if not any(path == part or part.endswith(path) for part in argv):
        # A command that never mentions the file cannot be right: refuse
        # rather than run it with no input and print nonsense.
        print("transcribe_command does not use {file}.", file=sys.stderr)
        return 4, ""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=TIMEOUT_SECS)
    except FileNotFoundError:
        print(f"transcribe_command not found: {argv[0]}", file=sys.stderr)
        return 4, ""
    except subprocess.TimeoutExpired:
        print(f"transcribe_command timed out after {TIMEOUT_SECS}s.",
              file=sys.stderr)
        return 4, ""
    except Exception as e:
        print(f"transcribe_command failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 4, ""
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        print(f"transcribe_command exited {r.returncode}"
              + (f": {detail[-1][:200]}" if detail else ""), file=sys.stderr)
        return 4, ""
    text = (r.stdout or "").strip()[:MAX_OUTPUT_CHARS]
    if not text:
        print("transcribe_command printed nothing.", file=sys.stderr)
        return 4, ""
    return 0, text


def main(argv):
    path = media_file(argv[1] if len(argv) > 1 else "")
    if not path:
        print("usage: transcribe.py <a file from the item's audio list>",
              file=sys.stderr)
        print("Only files the monitor downloaded into media/ can be "
              "transcribed.", file=sys.stderr)
        return 2
    try:
        st = C.load_settings(config_path())
    except Exception as e:
        print(f"could not read config.yaml: {e}", file=sys.stderr)
        return 4
    command = transcription_command(st)
    if not command:
        print("No speech-to-text is set up (engine.transcribe_command in "
              "config.yaml), so this voice message cannot be heard. Say so "
              "plainly instead of guessing what it said.", file=sys.stderr)
        return 3
    code, text = run(path, command)
    if text:
        sys.stdout.write(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
