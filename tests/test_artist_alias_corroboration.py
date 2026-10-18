"""Release-corroborated MusicBrainz aliases and legacy-provenance safety.

All HTTP calls are replaced by deterministic fake WS/2 search, browse, and
lookup responses. No actual MusicBrainz network requests occur.
"""
import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from artist_aliases import add_alias, fetch_aliases
from artist_alias_provenance import (
    audit_legacy_aliases, mark_alias_source, resolve_legacy_alias,
)
from artist_alias_sync import sync_artist_aliases
from db_schema import apply_migrations

A = "a47ac8f5-6774-4540-842f-f1eb0a19845c"
B = "12345678-1234-1234-1234-123456789abc"


class Response:
    def __init__(self, data):
        self.data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self.data


def group(title, mbid, *, primary="Album", credited=True):
    return {
        "title": title, "primary-type": primary,
        "artist-credit": [{"artist": {"id": mbid}}] if credited else [
            {"artist": {"id": B}}
        ],
    }


class MusicBrainzFake:
    def __init__(self, *, candidates=None, release_groups=None, count=None):
        self.candidates = candidates if candidates is not None else [
            {"id": A, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"}
        ]
        self.records = {
            A: {"id": A, "name": "徐佳瑩", "sort-name": "Hsu, LaLa",
                "aliases": [{"name": "Lala Hsu"}, {"name": "徐佳莹"}]},
            B: {"id": B, "name": "徐佳瑩", "sort-name": "徐佳瑩",
                "aliases": [{"name": "Another Performer"}]},
        }
        self.release_groups = release_groups if release_groups is not None else {
            A: [group("理想人生", A), group("心里学", A)]
        }
        self.count = count
        self.calls = []

    async def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params or {}))
        if url.rstrip("/").endswith("/artist"):
            return Response({"artists": self.candidates,
                             "count": self.count if self.count is not None
                             else len(self.candidates)})
        if url.rstrip("/").endswith("/release-group"):
            mbid = (params or {})["artist"]
            return Response({"release-groups": self.release_groups.get(mbid, [])})
        mbid = url.rsplit("/", 1)[-1]
        return Response(self.records[mbid])


async def no_wait(_seconds):
    return None


class ArtistAliasCorroborationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        apply_migrations(self.db)
        self.db.execute("INSERT INTO artists(id,name,sort_name) "
                        "VALUES(1,'徐佳瑩','徐佳瑩')")
        self.db.executemany(
            "INSERT INTO albums(id,artist_id,title,year) VALUES(?,1,?,?)",
            [(1, "理想人生", 2012), (2, "心里学", 2017)],
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def run_sync(self, client, **kwargs):
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            return asyncio.run(sync_artist_aliases(
                self.db, 1, client=client, **kwargs,
            ))

    def test_two_distinctive_albums_identify_unique_artist_and_add_provenance(self):
        fake = MusicBrainzFake(candidates=[
            {"id": B, "name": "徐佳瑩"},
            {"id": A, "name": "徐佳瑩"},
        ], release_groups={
            A: [group("理想人生", A), group("心裡學", A)],
            B: [group("理想人生", B), group("Unrelated Album", B)],
        })
        result = self.run_sync(fake)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verification"], "release_corroborated")
        self.assertEqual(result["mbid"], A)
        self.assertEqual(set(result["evidence_albums"]), {"理想人生", "心里学"})
        # Alias names are unique case-insensitively: "Lala Hsu" is stored once,
        # whichever spelling of the sort-name reversal arrived first.
        self.assertIn("lala hsu", {name.casefold() for name in fetch_aliases(self.db, 1)})
        row = self.db.execute(
            """SELECT p.source,p.mbid,p.evidence FROM artist_alias_provenance p
               JOIN artist_aliases aa ON aa.id=p.alias_id
               WHERE aa.name='Lala Hsu'"""
        ).fetchone()
        self.assertEqual(row[0], "release_corroborated")
        self.assertEqual(row[1], A)
        self.assertIn("理想人生", row[2])
        self.assertEqual(self.db.execute(
            "SELECT mbid_verified FROM artists WHERE id=1"
        ).fetchone()[0], 1)
        self.assertEqual(len(fake.calls), 4)  # one search, two browse, one detail

    def test_only_one_album_overlap_requires_human_review(self):
        fake = MusicBrainzFake(release_groups={
            A: [group("理想人生", A), group("Unrelated Album", A)]
        })
        result = self.run_sync(fake)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM artist_aliases").fetchone()[0], 0)

    def test_two_candidates_matching_two_albums_are_ambiguous(self):
        fake = MusicBrainzFake(
            candidates=[{"id": A, "name": "徐佳瑩"}, {"id": B, "name": "徐佳瑩"}],
            release_groups={
                A: [group("理想人生", A), group("心里学", A)],
                B: [group("理想人生", B), group("心里学", B)],
            },
        )
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        self.assertIsNone(self.db.execute("SELECT mbid FROM artists WHERE id=1").fetchone()[0])

    def test_real_search_counts_do_not_block_corroboration(self):
        # The live search reports every fuzzy hit: count=2,997,690 for 陳奕迅
        # and 徐佳瑩 (CJK names tokenize per character). A score-ordered page
        # whose tail is no longer same-named already holds every exact match.
        fake = MusicBrainzFake(count=2_997_690, candidates=[
            {"id": A, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"},
            {"id": "00000000-0000-0000-0000-000000000009", "name": "徐佳"},
        ])
        result = self.run_sync(fake)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verification"], "release_corroborated")

    def test_edition_notes_in_local_tags_still_match(self):
        # Tags often carry "(Deluxe Edition)" or "（豪華版）"; MusicBrainz lists
        # the release group under the plain album title.
        self.db.execute("UPDATE albums SET title='理想人生 (Deluxe Edition)' WHERE id=1")
        self.db.execute("UPDATE albums SET title='心里学（豪華版）' WHERE id=2")
        result = self.run_sync(MusicBrainzFake())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verification"], "release_corroborated")

    def test_page_still_full_of_namesakes_must_not_autolink(self):
        # Every hit on a truncated page is same-named: more may follow it.
        namesakes = [{"id": f"00000000-0000-0000-0000-00000000000{i}", "name": "徐佳瑩"}
                     for i in range(1, 4)]
        fake = MusicBrainzFake(count=100, candidates=[
            {"id": A, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"}, *namesakes,
        ])
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        self.assertFalse(any("release-group" in url for url, _ in fake.calls))

    def test_generic_albums_cannot_prove_identity(self):
        self.db.execute("UPDATE albums SET title='Greatest Hits' WHERE id=1")
        self.db.execute("UPDATE albums SET title='Live' WHERE id=2")
        fake = MusicBrainzFake()
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")

    def test_untrusted_guessed_mbid_can_be_replaced_by_release_evidence(self):
        self.db.execute("UPDATE artists SET mbid=? WHERE id=1", (B,))
        fake = MusicBrainzFake()
        result = self.run_sync(fake)
        self.assertEqual(result["verification"], "release_corroborated")
        self.assertEqual(self.db.execute(
            "SELECT mbid,mbid_verified FROM artists WHERE id=1"
        ).fetchone()[:], (A, 1))

    def test_upgrade_keeps_unknown_legacy_alias_for_explicit_review(self):
        add_alias(self.db, 1, "Uncertain English Alias")
        self.db.commit()
        result = audit_legacy_aliases(self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["aliases"][0]["alias"], "Uncertain English Alias")
        reviewed = resolve_legacy_alias(
            self.db, 1, "Uncertain English Alias", action="keep",
        )
        self.assertEqual(reviewed["action"], "keep")
        self.assertEqual(audit_legacy_aliases(self.db)["total"], 0)
        self.assertIn("Uncertain English Alias", fetch_aliases(self.db, 1))

    def test_remove_wrong_old_alias_without_changing_artist_or_audio_files(self):
        add_alias(self.db, 1, "Wrong Celebrity")
        result = resolve_legacy_alias(
            self.db, 1, "Wrong Celebrity", action="remove",
        )
        self.assertEqual(result["action"], "remove")
        self.assertEqual(fetch_aliases(self.db, 1), [])
        self.assertEqual(self.db.execute(
            "SELECT name FROM artists WHERE id=1"
        ).fetchone()[0], "徐佳瑩")

    def test_schema_15_upgrade_preserves_old_alias_and_requires_review(self):
        add_alias(self.db, 1, "Unverified Earlier Import")
        self.db.commit()
        self.db.execute("DROP INDEX IF EXISTS idx_artist_alias_provenance_source")
        self.db.execute("DROP TABLE artist_alias_provenance")
        self.db.execute("DELETE FROM schema_migrations WHERE version=16")
        self.db.commit()
        self.assertEqual(apply_migrations(self.db), 16)
        audit = audit_legacy_aliases(self.db)
        self.assertEqual(audit["total"], 1)
        self.assertEqual(audit["aliases"][0]["alias"], "Unverified Earlier Import")
        self.assertIn("Unverified Earlier Import", fetch_aliases(self.db, 1))

    def test_manual_alias_source_is_not_in_legacy_audit(self):
        add_alias(self.db, 1, "Stage Name")
        self.assertTrue(mark_alias_source(self.db, 1, "Stage Name", "manual"))
        self.assertEqual(audit_legacy_aliases(self.db)["total"], 0)
        self.assertEqual(fetch_aliases(self.db, 1), ["Stage Name"])

    def test_merge_and_rename_keep_alias_provenance(self):
        # merge_artists deletes and re-adds the source's aliases; without
        # carrying their records, every merged alias became "unknown origin".
        from artist_aliases import merge_artists, rename_artist

        self.db.execute("INSERT INTO artists(id,name,sort_name) VALUES"
                        "(2,'Gone','Gone'),(3,'Old','Old')")
        add_alias(self.db, 2, "Imported")
        mark_alias_source(self.db, 2, "Imported", "release_corroborated",
                          mbid=A, evidence="理想人生; 心里学")
        add_alias(self.db, 2, "Old Unknown")  # pre-schema-16 row: no record
        merge_artists(self.db, 1, [2])
        rename_artist(self.db, 3, "New")

        sources = dict(self.db.execute(
            """SELECT aa.name, p.source FROM artist_aliases aa
               LEFT JOIN artist_alias_provenance p ON p.alias_id = aa.id"""
        ).fetchall())
        self.assertEqual(sources, {
            "Imported": "release_corroborated",
            "Old Unknown": None,
            "Gone": "manual",
            "Old": "manual",
        })
        evidence = self.db.execute(
            """SELECT p.mbid, p.evidence FROM artist_alias_provenance p
               JOIN artist_aliases aa ON aa.id = p.alias_id WHERE aa.name='Imported'"""
        ).fetchone()
        self.assertEqual(tuple(evidence), (A, "理想人生; 心里学"))
        audit = audit_legacy_aliases(self.db)
        self.assertEqual([a["alias"] for a in audit["aliases"]], ["Old Unknown"])


if __name__ == "__main__":
    unittest.main()
