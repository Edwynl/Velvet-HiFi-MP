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
    def __init__(
        self, *, candidates=None, release_groups=None, count=None,
        exact_candidates=None, exact_count=None, exact_offset_override=None,
        release_count_override=None,
    ):
        self.candidates = candidates if candidates is not None else [
            {"id": A, "name": "徐佳瑩", "sort-name": "Hsu, LaLa"}
        ]
        self.exact_candidates = (exact_candidates if exact_candidates is not None
                                 else self.candidates)
        self.exact_count = exact_count
        self.exact_offset_override = exact_offset_override
        self.release_count_override = release_count_override
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
            if (params or {}).get("query", "").startswith('artist:"'):
                offset = (params or {}).get("offset", 0)
                limit = (params or {}).get("limit", 25)
                return Response({
                    "artists": self.exact_candidates[offset:offset + limit],
                    "count": (self.exact_count if self.exact_count is not None
                              else len(self.exact_candidates)),
                    "offset": (self.exact_offset_override
                               if self.exact_offset_override is not None else offset),
                })
            return Response({
                "artists": self.candidates,
                "count": self.count if self.count is not None else len(self.candidates),
            })
        if url.rstrip("/").endswith("/release-group"):
            mbid = (params or {})["artist"]
            all_groups = self.release_groups.get(mbid, [])
            offset = (params or {}).get("offset", 0)
            limit = (params or {}).get("limit", 100)
            return Response({
                "release-groups": all_groups[offset:offset + limit],
                "release-group-count": (
                    self.release_count_override if self.release_count_override is not None
                    else len(all_groups)
                ),
                "release-group-offset": offset,
            })
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
        # One broad search, two scoped Chinese spelling searches, two
        # releases browses and one final alias lookup.
        self.assertEqual(len(fake.calls), 6)

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

    def test_incomplete_exact_name_page_fails_closed(self):
        # A MusicBrainz exact-name field result >100 cannot be exhaustively
        # checked within our conservative cap. Do NOT trust rank order.
        fake = MusicBrainzFake(
            count=2_997_690, exact_count=101,
            candidates=[
                {"id": A, "name": "徐佳瑩"},
                {"id": B, "name": "Some other singer"},
            ],
        )
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        self.assertFalse(any("release-group" in url for url, _ in fake.calls))

    def test_fuzzy_page_tail_cannot_hide_second_same_named_artist(self):
        # The formerly unsafe path: a broad query's tenth hit is fuzzy,
        # while another exact-name artist sits beyond the broad first page.
        unrelated = [
            {"id": f"00000000-0000-0000-0000-{i:012d}", "name": f"Other {i}"}
            for i in range(1, 10)
        ]
        fuzzy = [{"id": A, "name": "徐佳瑩"}, *unrelated]
        # The constrained artist-name result pages completely. Position #26
        # is a second namesake; it must be checked even after other records.
        constrained = [
            {"id": A, "name": "徐佳瑩"}, *unrelated,
            *[
                {"id": f"00000000-0000-0000-0000-{i:012d}",
                 "name": f"Other {i}"}
                for i in range(10, 25)
            ],
            {"id": B, "name": "徐佳瑩"},
        ]
        fake = MusicBrainzFake(
            candidates=fuzzy, count=2_997_690, exact_candidates=constrained,
            release_groups={
                A: [group("理想人生", A), group("心里学", A)],
                B: [group("理想人生", B), group("心里学", B)],
            },
        )
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        exact_queries = [
            args for url, args in fake.calls
            if url.rstrip("/").endswith("/artist")
            and args.get("query", "").startswith('artist:"')
        ]
        self.assertTrue(any(args.get("offset") == 25 for args in exact_queries))
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM artist_aliases").fetchone()[0], 0
        )
        self.assertIsNone(
            self.db.execute("SELECT mbid FROM artists WHERE id=1").fetchone()[0]
        )

    def test_inconsistent_page_offset_must_not_autolink(self):
        fake = MusicBrainzFake(exact_offset_override=999)
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        self.assertFalse(any("release-group" in url for url, _ in fake.calls))

    def test_truncated_namesake_release_groups_must_not_autolink(self):
        # A second artist can share the two local albums on a later release
        # group page. A browse with >200 results cannot prove uniqueness.
        fake = MusicBrainzFake(
            exact_candidates=[
                {"id": A, "name": "徐佳瑩"},
                {"id": B, "name": "徐佳瑩"},
            ],
            release_groups={
                A: [group("理想人生", A), group("心里学", A)],
                B: [group("Unrelated Album", B)],
            },
            release_count_override=201,
        )
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM artist_aliases").fetchone()[0], 0
        )

    def test_exhaustive_release_browse_checks_second_page_for_namesake(self):
        # Candidate B's two matching albums can be split across page 1 and
        # page 2. Every candidate's release groups must be checked completely.
        def large_discography(mbid):
            return [
                group("理想人生", mbid),
                *[group(f"Unrelated Record {i}", mbid) for i in range(99)],
                group("心里学", mbid),
            ]
        fake = MusicBrainzFake(
            exact_candidates=[
                {"id": A, "name": "徐佳瑩"},
                {"id": B, "name": "徐佳瑩"},
            ],
            release_groups={A: large_discography(A), B: large_discography(B)},
        )
        self.assertEqual(self.run_sync(fake)["status"], "needs_review")
        second_pages = [
            params for url, params in fake.calls
            if url.rstrip("/").endswith("/release-group")
            and params.get("offset") == 100
        ]
        self.assertEqual(len(second_pages), 2)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM artist_aliases"
        ).fetchone()[0], 0)

    def test_fixed_point_opencc_name_variants_cannot_hide_namesakes(self):
        from artist_alias_sync import _complete_exact_name_candidates
        fake = MusicBrainzFake(exact_candidates=[])
        with patch("artist_alias_sync.asyncio.sleep", no_wait):
            self.assertEqual(asyncio.run(
                _complete_exact_name_candidates("陳昇", fake)
            ), [])
        constrained = [
            params["query"] for url, params in fake.calls
            if params.get("query", "").startswith('artist:"')
        ]
        self.assertIn('artist:"陈升"', constrained)

    def test_constrained_artist_query_escapes_lucene_metacharacters(self):
        from artist_alias_sync import _artist_field_query
        self.assertEqual(_artist_field_query('AC/DC'),
                         'artist:"AC\\/DC"')
        self.assertEqual(_artist_field_query('A+B "Live"'),
                         'artist:"A\\+B \\"Live\\""')

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
