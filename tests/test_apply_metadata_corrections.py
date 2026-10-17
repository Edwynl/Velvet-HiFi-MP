import csv
import sqlite3
import struct
import sys
import tempfile
import unittest
import wave
from contextlib import closing
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import TALB, TIT2, TPE1
from mutagen.wave import WAVE

from artist_aliases import fetch_aliases
from db_schema import apply_migrations

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_metadata_corrections as amc  # noqa: E402

FIELDS = ["kind", "id", "current", "proposed", "merge_ids", "source", "check", "approve"]


def write_flac(path: Path, tags: dict) -> None:
    """A header-only FLAC (STREAMINFO, no frames) that mutagen can tag."""
    info = struct.pack(">HH", 4096, 4096) + b"\0" * 6
    info += ((44100 << 44) | (1 << 41) | (15 << 36)).to_bytes(8, "big") + b"\0" * 16
    path.write_bytes(b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info)
    audio = FLAC(path)
    audio.add_tags()
    for key, value in tags.items():
        audio[key] = value
    audio.save()


def write_wav(path: Path, title: str, album: str, artist: str) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 80)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=[title]))
    audio.tags.add(TALB(encoding=3, text=[album]))
    audio.tags.add(TPE1(encoding=3, text=[artist]))
    audio.save()


class ApplyMetadataCorrectionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "library.db"
        self.flac = self.root / "01.flac"
        self.wav = self.root / "02.wav"
        write_flac(self.flac, {"title": "Xin Shui Xuan Ze", "album": "Xin Shui Xuan Ze",
                               "artist": "Sammi Cheng & Andy Lau", "albumartist": "Sammi Cheng"})
        write_wav(self.wav, "Love Song", "Xin Shui Xuan Ze", "SAMMI CHENG")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            apply_migrations(conn)
            conn.row_factory = None
            conn.executescript(
                f"""
                INSERT INTO artists (id, name, sort_name) VALUES
                    (1, '鄭秀文', '鄭秀文'), (2, 'Sammi Cheng', 'Sammi Cheng'), (3, 'Andy Lau', 'Andy Lau');
                INSERT INTO albums (id, artist_id, title) VALUES (10, 2, 'Xin Shui Xuan Ze');
                INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES
                    (100, 10, 2, 'Xin Shui Xuan Ze', '{self.flac.as_posix()}'),
                    (101, 10, 2, 'Love Song', '{self.wav.as_posix()}');
                INSERT INTO track_artists VALUES (100, 2), (100, 3), (101, 2);
                """
            )
            conn.commit()
        self.csv_path = self.root / "corrections.csv"
        self._write_csv([
            ["artist", 1, "鄭秀文", "鄭秀文", "2", "musicbrainz", "ok", "Y"],
            ["album", 10, "Xin Shui Xuan Ze", "心水選擇", "", "musicbrainz", "pinyin", "y"],
            ["track", 100, "Xin Shui Xuan Ze", "心水選擇", "", "musicbrainz", "pinyin", "是"],
            ["track", 101, "Love Song", "情歌", "", "guess", "", "N"],
        ])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_csv(self, rows):
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            writer.writerows(rows)

    def _db_state(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return (
                conn.execute("SELECT id, name FROM artists ORDER BY id").fetchall(),
                conn.execute("SELECT artist_id, title FROM albums").fetchall(),
                conn.execute("SELECT id, title, artist_id FROM tracks ORDER BY id").fetchall(),
                fetch_aliases(conn, 1),
            )

    def test_dry_run_changes_nothing(self):
        before = self._db_state()
        amc.main([str(self.csv_path), "--db", str(self.db_path)])
        self.assertEqual(self._db_state(), before)
        self.assertEqual(list(self.root.glob("library.backup-*")), [])

    def test_apply_writes_db_tags_backup_and_undo_restores_tags(self):
        amc.main([str(self.csv_path), "--db", str(self.db_path), "--apply", "--write-tags"])

        artists, albums, tracks, aliases = self._db_state()
        self.assertEqual(artists, [(1, "鄭秀文"), (3, "Andy Lau")])
        self.assertEqual(albums, [(1, "心水選擇")])
        self.assertEqual(tracks, [(100, "心水選擇", 1), (101, "Love Song", 1)])
        self.assertEqual(aliases, ["Sammi Cheng"])

        flac = FLAC(self.flac)
        self.assertEqual(flac["title"], ["心水選擇"])
        self.assertEqual(flac["album"], ["心水選擇"])
        self.assertEqual(flac["artist"], ["鄭秀文 & Andy Lau"])
        self.assertEqual(flac["albumartist"], ["鄭秀文"])
        wav = WAVE(self.wav).tags
        self.assertEqual(wav["TIT2"].text, ["Love Song"])  # not approved
        self.assertEqual(wav["TALB"].text, ["心水選擇"])
        self.assertEqual(wav["TPE1"].text, ["鄭秀文"])

        backups = list(self.root.glob("library.backup-before-metadata-cleanup-*.db"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute("SELECT name FROM artists WHERE id = 2").fetchone(), ("Sammi Cheng",))

        log = next(self.root.glob("metadata_cleanup_*.tags.backup.jsonl"))
        amc.main(["--undo-tags", str(log)])
        flac = FLAC(self.flac)
        self.assertEqual(flac["title"], ["Xin Shui Xuan Ze"])
        self.assertEqual(flac["artist"], ["Sammi Cheng & Andy Lau"])
        self.assertEqual(WAVE(self.wav).tags["TPE1"].text, ["SAMMI CHENG"])

    def test_rows_that_changed_since_review_are_skipped(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE tracks SET title = 'Edited' WHERE id = 100")
            conn.commit()
        amc.main([str(self.csv_path), "--db", str(self.db_path), "--apply"])
        _artists, _albums, tracks, _aliases = self._db_state()
        self.assertEqual(tracks[0][1], "Edited")

    def test_rewrite_artist_value_keeps_separators_and_other_names(self):
        renamed = {"Sammi Cheng": "鄭秀文", "Jacky Cheung": "張學友"}
        self.assertEqual(amc.rewrite_artist_value("sammi cheng", renamed), "鄭秀文")
        self.assertEqual(amc.rewrite_artist_value("Jacky Cheung / Sammi Cheng feat. X", renamed),
                         "張學友 / 鄭秀文 feat. X")
        self.assertEqual(amc.rewrite_artist_value("Sammi Chengs", renamed), "Sammi Chengs")


if __name__ == "__main__":
    unittest.main()
