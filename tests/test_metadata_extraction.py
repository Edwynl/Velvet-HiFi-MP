import tempfile
import unittest
import wave
from pathlib import Path

from mutagen.id3 import TALB, TIT2, TPE1, TRCK
from mutagen.wave import WAVE

import server


class WavId3ExtractionTests(unittest.TestCase):
    def test_wav_id3_tags_are_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "01. To me.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(44100)
                wav.writeframes(b"\0" * 4410 * 4)
            audio = WAVE(str(path))
            audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text="To me"))
            audio.tags.add(TPE1(encoding=3, text="玉置浩二[60yp.com分享]"))
            audio.tags.add(TALB(encoding=3, text="酒红色的心"))
            audio.tags.add(TRCK(encoding=3, text="01"))
            audio.save()

            meta = server.extract_metadata(str(path))

        self.assertEqual(meta["title"], "To me")
        self.assertEqual(meta["artist"], "玉置浩二")
        self.assertEqual(meta["album"], "酒红色的心")
        self.assertEqual(meta["track_number"], 1)


if __name__ == "__main__":
    unittest.main()
