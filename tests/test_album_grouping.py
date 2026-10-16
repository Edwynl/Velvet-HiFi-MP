"""Folder-aware album grouping: one folder of songs must become one album."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from album_grouping import (
    VARIOUS_ARTISTS,
    album_folder_of,
    clean_folder_album_name,
    plan_album_folder,
    strip_ad_marks,
)
from db_schema import LATEST_SCHEMA_VERSION, apply_migrations
from scanner_service import refresh_folder_records, scan_music_dirs


def meta(path, *, artist=None, album=None, album_artist=None, title=None,
         track=None, disc=1, year=None):
    return {
        "album_artist": album_artist, "artist": artist or "Unknown Artist",
        "album": album or "Unknown Album", "title": title or Path(path).stem,
        "year": year, "genre": None, "label": None, "cover_data": None,
        "composer": None, "work_title": None, "performer": None,
        "conductor": None, "ensemble": None, "track_number": track,
        "disc_number": disc, "duration": 200, "format": "FLAC",
        "sample_rate": 44100, "bit_depth": 16, "channels": 2,
        "bitrate": 1000, "file_size": 3,
    }


class FolderNameTests(unittest.TestCase):
    def test_clean_folder_album_name(self):
        cases = {
            "[A]  TREBLE [349577223] [2024]": "TREBLE",
            " EVO [125776094] [2012]": "EVO",
            "2008-Fearless": "Fearless",
            "2009-American Girl(单曲)": "American Girl",
            "2017-渡 The Crossing": "渡 The Crossing",
            "2008-03-14劲歌金曲2：情歌王[wav]": "劲歌金曲2：情歌王",
            "张学友.2010 -《私人珍藏 》2CD 无损黑胶K2HD [WAV+分轨]": "私人珍藏",
            "Hubert Wu - Are You Feeling Blissful (2020) [16B-44.1kHz]": "Are You Feeling Blissful",
            "2005-04-22 funny girl[flac]": "funny girl",
            "[2004] Michael Jackson - The Ultimate Collection（4CD）[flac]": "The Ultimate Collection",
            "第1期[flac]": "第1期",
        }
        for folder, expected in cases.items():
            with self.subTest(folder=folder):
                self.assertEqual(clean_folder_album_name(folder), expected)

    def test_album_folder_of_disc_subfolders(self):
        self.assertEqual(album_folder_of(r"E:\M\Album\CD1\01.wav"), (r"E:\M\Album", 1))
        self.assertEqual(album_folder_of(r"E:\M\Album\Disc 2\01.flac"), (r"E:\M\Album", 2))
        self.assertEqual(
            album_folder_of(r"E:\M\X.2010 -《Y》2CD\X.2010 -《Y》CD2 K2HD\B01.wav"),
            (r"E:\M\X.2010 -《Y》2CD", 2),
        )
        self.assertEqual(album_folder_of(r"E:\M\Album\01.flac"), (r"E:\M\Album", None))

    def test_strip_ad_marks(self):
        self.assertEqual(strip_ad_marks("古巨基[60yp.com分享]"), "古巨基")
        self.assertIsNone(strip_ad_marks("【letians.cn分享】"))
        self.assertEqual(strip_ad_marks("Taylor Swift"), "Taylor Swift")
        self.assertIsNone(strip_ad_marks("wusunk.com"))
        self.assertIsNone(strip_ad_marks("wenanb.com收藏"))
        self.assertIsNone(strip_ad_marks("无损控资源[wusunk.com]"))
        self.assertEqual(strip_ad_marks("Bruno Mars [wusunk.com整理]"), "Bruno Mars")
        # Real names that merely look like web addresses stay intact.
        for real in ("So.Far.So.Good", "No Worry (feat. E.SO)", "Mr.Children", "W.LIN"):
            self.assertEqual(strip_ad_marks(real), real)


class PlanTests(unittest.TestCase):
    def plan(self, entries, sibling=None):
        return plan_album_folder(entries, sibling_artist=lambda folder: sibling)

    def test_compilation_folder_gets_one_various_artists_album(self):
        folder = r"E:\Roon Music\声生不息\第1期[flac]"
        entries = [
            (rf"{folder}\{i:02d}.flac", meta(f"{i}", artist=a, album="声生不息 第1期"))
            for i, a in enumerate(["李玟", "刘惜君", "单依纯", "李健"], start=1)
        ]
        plan = self.plan(entries)
        self.assertEqual({p["album_owner"] for p in plan.values()}, {VARIOUS_ARTISTS})
        self.assertTrue(all("album" not in p for p in plan.values()), "title already consistent")
        # Track artists are untouched.
        self.assertTrue(all("artist" not in p for p in plan.values()))

    def test_band_with_member_solo_tracks_stays_with_band(self):
        folder = r"E:\Roon2\Bigbang\2014-THE BEST"
        artists = ["BIGBANG (빅뱅)"] * 3 + ["BIGBANG"] * 2 + ["G-DRAGON", "GD", "T.O.P"]
        entries = [(rf"{folder}\{i}.flac", meta(f"{i}", artist=a, album="THE BEST"))
                   for i, a in enumerate(artists)]
        plan = self.plan(entries)
        self.assertEqual({p["album_owner"] for p in plan.values()}, {"BIGBANG"})

    def test_sibling_folder_artist_breaks_ties(self):
        folder = r"E:\Roon2\Bigbang\2012-SPECIAL"
        entries = [(rf"{folder}\{i}.flac", meta(f"{i}", artist=a, album="SPECIAL"))
                   for i, a in enumerate(["BIGBANG", "G-DRAGON", "GD", "대성"])]
        plan = self.plan(entries, sibling="BIGBANG")
        self.assertEqual({p["album_owner"] for p in plan.values()}, {"BIGBANG"})

    def test_single_artist_album_is_left_alone(self):
        folder = r"E:\M\Artist\Album"
        entries = [(rf"{folder}\{i}.flac", meta(f"{i}", artist="Artist", album="Album"))
                   for i in range(3)]
        self.assertEqual(self.plan(entries), {path: {} for path, _ in entries})

    def test_album_title_case_variants_merge(self):
        folder = r"E:\Q\周杰倫 - Greatest Works Of Art (2022)"
        titles = ["Greatest Works Of Art"] * 3 + ["Greatest Works of Art"]
        entries = [(rf"{folder}\{i}.flac", meta(f"{i}", artist="周杰倫", album=t))
                   for i, t in enumerate(titles)]
        plan = self.plan(entries)
        effective = {plan[path].get("album", m["album"]) for path, m in entries}
        self.assertEqual(effective, {"Greatest Works Of Art"})

    def test_punctuation_and_partial_title_variants_merge(self):
        folder = r"E:\Roon2\Bigbang\2008-2nd Album  Remember"
        titles = ["2nd Album : Remember"] * 3 + ["Remember", "2nd Album: Remember?"]
        entries = [(rf"{folder}\{i}.flac", meta(f"{i}", artist="BIGBANG", album=t))
                   for i, t in enumerate(titles)]
        plan = self.plan(entries)
        effective = {plan[path].get("album", m["album"]) for path, m in entries}
        self.assertEqual(effective, {"2nd Album : Remember"})

    def test_distinct_short_titles_are_not_merged(self):
        folder = r"E:\M\Loose"
        entries = [(rf"{folder}\1.flac", meta("1", artist="X", album="Up")),
                   (rf"{folder}\2.flac", meta("2", artist="X", album="Upside"))]
        self.assertEqual(self.plan(entries), {path: {} for path, _ in entries})

    def test_cd_suffix_becomes_disc_number(self):
        folder = r"E:\Roon2\Taylor\2014-1989(Deluxe Edition)"
        entries = [
            (rf"{folder}\a.flac", meta("a", artist="Taylor Swift", album="1989(Deluxe Edition 2CD).CD1")),
            (rf"{folder}\b.flac", meta("b", artist="Taylor Swift", album="1989(Deluxe Edition 2CD).CD2")),
        ]
        plan = self.plan(entries)
        self.assertEqual(plan[rf"{folder}\a.flac"]["album"], "1989(Deluxe Edition)")
        self.assertEqual(plan[rf"{folder}\b.flac"]["album"], "1989(Deluxe Edition)")
        self.assertEqual(plan[rf"{folder}\b.flac"]["disc_number"], 2)

    def test_untagged_tidal_files_infer_album_artist_title_and_number(self):
        folder = r"E:\Tidal\MC Cheung Tinfu\[A]  TREBLE [349577223] [2024]"
        path = rf"{folder}\02 - MC Cheung Tinfu - Floral Sea.m4a"
        plan = self.plan([(path, meta(path))])[path]
        self.assertEqual(plan["album"], "TREBLE")
        self.assertEqual(plan["artist"], "MC Cheung Tinfu")
        self.assertEqual(plan["title"], "Floral Sea")
        self.assertEqual(plan["track_number"], 2)
        self.assertEqual(plan["year"], 2024)

    def test_untagged_files_join_tagged_siblings(self):
        folder = r"E:\Tidal\田馥甄\ 演唱會的日常 [83311928] [2016]"
        tagged = rf"{folder}\01 - 田馥甄 - 日常 (Live).flac"
        bare = rf"{folder}\06 - 田馥甄 - 靈魂伴侶 (Live).flac"
        plan = self.plan([
            (tagged, meta(tagged, artist="田馥甄", album="演唱會的日常", title="日常 (Live)", year=2016)),
            (bare, meta(bare)),
        ])
        self.assertEqual(plan[bare]["album"], "演唱會的日常")
        self.assertEqual(plan[bare]["artist"], "田馥甄")
        self.assertEqual(plan[bare]["title"], "靈魂伴侶 (Live)")

    def test_missing_artist_uses_sibling_then_ancestor_folder(self):
        folder = r"E:\Roon Music\薛之谦\FLAC\2018-怪咖"
        path = rf"{folder}\01. 摩天大楼.flac"
        plan = self.plan([(path, meta(path))])[path]
        self.assertEqual((plan["artist"], plan["album"], plan["title"]), ("薛之谦", "怪咖", "摩天大楼"))
        plan = self.plan([(path, meta(path))], sibling="Joker Xue")[path]
        self.assertEqual(plan["artist"], "Joker Xue")


class ScannerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(self.root / "music.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.assertEqual(apply_migrations(self.db), LATEST_SCHEMA_VERSION)
        self.tags: dict[str, dict] = {}
        self.calls: list[str] = []

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add(self, rel, **tags):
        path = self.root / "music" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        self.tags[str(path)] = meta(str(path), **tags)
        return str(path)

    def extract(self, path):
        self.calls.append(path)
        return dict(self.tags[path])

    def kwargs(self):
        return dict(
            audio_extensions={".flac"}, extract_metadata=self.extract,
            find_folder_cover=lambda _: None, save_cover=lambda data, key: key,
            set_total=lambda n: None, on_progress=lambda n, f: None,
            on_track_added=lambda: None, on_error=lambda m: self.fail(m),
        )

    def scan(self):
        return scan_music_dirs(self.db, [str(self.root / "music")], scan_batch_size=10,
                               on_track_skipped=lambda: None, **self.kwargs())

    def albums(self):
        return self.db.execute(
            "SELECT al.title, ar.name, al.total_tracks FROM albums al "
            "JOIN artists ar ON ar.id=al.artist_id ORDER BY al.title"
        ).fetchall()

    def test_compilation_and_disc_folders_become_single_albums(self):
        for i, a in enumerate(["李玟", "刘惜君", "单依纯"]):
            self.add(rf"show\ep1\{i}.flac", artist=a, album="声生不息 第1期")
        self.add(r"box\CD1\01.flac", artist="Michael Jackson", album="The Ultimate Collection (disc 1)")
        self.add(r"box\CD2\01.flac", artist="Michael Jackson", album="The Ultimate Collection (disc 2)")
        self.scan()
        self.assertEqual([tuple(r) for r in self.albums()], [
            ("The Ultimate Collection", "Michael Jackson", 2),
            ("声生不息 第1期", VARIOUS_ARTISTS, 3),
        ])
        track_artists = {r[0] for r in self.db.execute(
            "SELECT ar.name FROM tracks t JOIN artists ar ON ar.id=t.artist_id")}
        self.assertEqual(track_artists, {"李玟", "刘惜君", "单依纯", "Michael Jackson"})
        discs = sorted(r[0] for r in self.db.execute(
            "SELECT disc_number FROM tracks t JOIN albums al ON al.id=t.album_id "
            "WHERE al.title='The Ultimate Collection'"))
        self.assertEqual(discs, [1, 2])

    def test_refresh_merges_previously_split_album_and_drops_empty_albums(self):
        folder = self.root / "music" / "show" / "ep1"
        # Simulate the old behaviour: one album per artist for one folder.
        for i, a in enumerate(["A", "B"]):
            path = self.add(rf"show\ep1\{i}.flac", artist=a, album="Ep 1")
            self.db.execute("INSERT OR IGNORE INTO artists(name, sort_name) VALUES (?, ?)", (a, a))
            artist_id = self.db.execute("SELECT id FROM artists WHERE name=?", (a,)).fetchone()[0]
            self.db.execute("INSERT INTO albums(artist_id, title) VALUES (?, 'Ep 1')", (artist_id,))
            album_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.execute(
                "INSERT INTO tracks(album_id, artist_id, title, file_path) VALUES (?, ?, ?, ?)",
                (album_id, artist_id, a, path),
            )
        self.db.commit()
        refresh_folder_records(self.db, str(folder), **self.kwargs())
        self.assertEqual([tuple(r) for r in self.albums()], [("Ep 1", VARIOUS_ARTISTS, 2)])
        # Track artists keep their own rows; nothing references a deleted album.
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(
            {r[0] for r in self.db.execute("SELECT name FROM artists")},
            {"A", "B", VARIOUS_ARTISTS},
        )

    def test_unchanged_files_are_still_not_decoded(self):
        self.add(r"album\1.flac", artist="X", album="Y")
        self.add(r"album\2.flac", artist="X", album="Y")
        self.scan()
        self.assertEqual(len(self.calls), 2, "each file decoded once on first scan")
        self.scan()
        self.assertEqual(len(self.calls), 2, "unchanged files are not decoded again")


if __name__ == "__main__":
    unittest.main()
