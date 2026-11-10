"""Voice the sample scripts with macOS `say` and write the reference transcripts.

For each `samples/<slug>/script.json` this:

1. renders every turn with the speaker's voice as 16 kHz mono 16-bit WAV,
2. joins the turns with short pauses into one recording, noting where each turn starts
   and ends,
3. converts the recording to AAC in an `.m4a` container with `afconvert` (small enough
   to commit),
4. writes `transcript.json`: the scripted turns with exact timestamps. The
   ReferenceTranscriber serves this so the samples work without any API key.

macOS only. Run from anywhere: `python samples/tools/build_audio.py [slug ...]`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[1]
RATE = 16_000
SAMPLE_WIDTH = 2  # bytes, 16-bit PCM
LEAD_IN_S = 0.4
GAP_SAME_SPEAKER_S = 0.35
GAP_NEW_SPEAKER_S = 0.6
TAIL_S = 0.5
AAC_BITRATE = 32_000


def silence(seconds: float) -> bytes:
    return b"\x00" * (round(seconds * RATE) * SAMPLE_WIDTH)


def render_turn(text: str, voice: str, target: Path) -> bytes:
    subprocess.run(
        [
            "say",
            "-v",
            voice,
            "-o",
            str(target),
            "--file-format=WAVE",
            f"--data-format=LEI16@{RATE}",
            "--",
            text,
        ],
        check=True,
    )
    with wave.open(str(target), "rb") as wav:
        if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (
            1,
            RATE,
            SAMPLE_WIDTH,
        ):
            raise RuntimeError(f"unexpected format from say for voice {voice!r}")
        return wav.readframes(wav.getnframes())


def build(sample_dir: Path) -> None:
    script = json.loads((sample_dir / "script.json").read_text())
    voices: dict[str, str] = script["voices"]
    pcm = bytearray(silence(LEAD_IN_S))
    segments = []
    previous_speaker: str | None = None

    with tempfile.TemporaryDirectory() as tmp:
        for index, turn in enumerate(script["turns"], start=1):
            if previous_speaker is not None:
                gap = (
                    GAP_SAME_SPEAKER_S if turn["speaker"] == previous_speaker else GAP_NEW_SPEAKER_S
                )
                pcm += silence(gap)
            start = len(pcm) / SAMPLE_WIDTH / RATE
            pcm += render_turn(turn["text"], voices[turn["speaker"]], Path(tmp) / f"{index}.wav")
            end = len(pcm) / SAMPLE_WIDTH / RATE
            segments.append(
                {
                    "id": f"s{index}",
                    "speaker": turn["speaker"],
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "text": turn["text"],
                }
            )
            previous_speaker = turn["speaker"]
        pcm += silence(TAIL_S)

        combined = Path(tmp) / "combined.wav"
        with wave.open(str(combined), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(RATE)
            out.writeframes(bytes(pcm))

        audio = sample_dir / "audio.m4a"
        subprocess.run(
            [
                "afconvert",
                "-f",
                "m4af",
                "-d",
                "aac",
                "-b",
                str(AAC_BITRATE),
                str(combined),
                str(audio),
            ],
            check=True,
        )

    duration = len(pcm) / SAMPLE_WIDTH / RATE
    transcript = {
        "slug": script["slug"],
        "source": "script",
        "audio": "audio.m4a",
        "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "duration_seconds": round(duration, 2),
        "segments": segments,
    }
    (sample_dir / "transcript.json").write_text(json.dumps(transcript, indent=2) + "\n")
    size_kb = audio.stat().st_size / 1024
    print(f"{script['slug']}: {len(segments)} turns, {duration:.1f} s, {size_kb:.0f} KB")


def main(argv: list[str]) -> int:
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        print("needs macOS `say` and `afconvert`", file=sys.stderr)
        return 1
    slugs = argv or sorted(p.parent.name for p in SAMPLES.glob("*/script.json"))
    for slug in slugs:
        build(SAMPLES / slug)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
