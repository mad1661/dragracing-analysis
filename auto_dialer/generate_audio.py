#!/usr/bin/env python3
"""
Generate the "No" audio file for the auto-dialer.
Creates a WAV file that says "No" in the format pjsua expects:
  - 16-bit PCM
  - 16000 Hz sample rate (or 8000 Hz for narrow-band)
  - Mono
"""

import os
import sys


def generate_with_gtts():
    """Generate using Google Text-to-Speech (requires internet)."""
    from gtts import gTTS
    from pydub import AudioSegment

    print("Generating 'No' audio with Google TTS...")
    tts = gTTS(text="No", lang="en", slow=False)
    tts.save("audio/no_raw.mp3")

    # Convert to WAV format that pjsua expects
    audio = AudioSegment.from_mp3("audio/no_raw.mp3")
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    audio.export("audio/no.wav", format="wav")

    os.remove("audio/no_raw.mp3")
    print("Created audio/no.wav")


def generate_with_say():
    """Generate using macOS 'say' command (no internet needed)."""
    print("Generating 'No' audio with macOS 'say' command...")

    # macOS say can output AIFF directly
    os.system('say -o audio/no_raw.aiff "No"')

    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file("audio/no_raw.aiff")
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        audio.export("audio/no.wav", format="wav")
        os.remove("audio/no_raw.aiff")
    except ImportError:
        # Fallback: use ffmpeg or afconvert
        os.system(
            "afconvert -f WAVE -d LEI16@16000 -c 1 audio/no_raw.aiff audio/no.wav"
        )
        os.remove("audio/no_raw.aiff")

    print("Created audio/no.wav")


def main():
    os.makedirs("audio", exist_ok=True)

    # Try macOS 'say' first (no dependencies needed), then fall back to gTTS
    if sys.platform == "darwin":
        generate_with_say()
    else:
        try:
            generate_with_gtts()
        except ImportError:
            print("ERROR: Install gtts and pydub: pip install gtts pydub")
            print("  Or run on macOS where 'say' command is available.")
            sys.exit(1)

    if os.path.exists("audio/no.wav"):
        size = os.path.getsize("audio/no.wav")
        print(f"Success! audio/no.wav ({size} bytes)")
    else:
        print("ERROR: Failed to create audio/no.wav")
        sys.exit(1)


if __name__ == "__main__":
    main()
