"""Tests for the artist-picture backfill's Apple Music path.

The artist page's og:image is often Apple's stand-in cover, while the page
also carries the label's own square picture of the act. The backfill must
prefer that identity picture, and must recognise the generic artwork so it
does not store a blank tile.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_artist_images as backfill  # noqa: E402

ARTIST_ID = 123456
IDENTITY = ("https://is1-ssl.mzstatic.com/image/thumb/AMCArtistImages221/v4/ab/cd/"
            "ami_identity-2026-10-15T08-10-15.855Z_cropped.png/600x600bb.png")
GENERIC = "https://is1-ssl.mzstatic.com/image/thumb/Music20/v4/de/9b/1d/cover.jpg/600x600bb.jpg"
PLACEHOLDER = "https://is1-ssl.mzstatic.com/image/thumb/Music20/v4/x/apple-music.png/600x600bb.png"

IDENTITY_SCRIPT = ('https://is1-ssl.mzstatic.com/image/thumb/AMCArtistImages221/v4/ab/cd/'
                   'ami_identity-2026-10-15T08-10-15.855Z_cropped.png/1200x630cw.png')
# The page's own picture of the act, the mzl.* series, served with a square
# crop variant.
PORTRAIT_SCRIPT = ('https://is1-ssl.mzstatic.com/image/thumb/Features115/v4/87/37/9c/'
                   '87379cfc-1038-1f74-fc8f-1dfd4997a952/mzl.lvyhvtcz.jpg/243x243cc.webp')

PAGE_TEMPLATE = ('<html><head>\n'
                 '<meta property="og:image" content="OG_URL">\n'
                 '</head><body>\n'
                 '<script>{"url":"IDENTITY_URL"}</script>\n'
                 '</body></html>')


def page(og, identity=IDENTITY_SCRIPT):
    return (PAGE_TEMPLATE.replace("OG_URL", og)
            .replace("IDENTITY_URL", identity))


def make_client(og):
    import httpx

    def handler(request):
        if request.url.host == "itunes.apple.com":
            return httpx.Response(200, json={"results": [{
                "artistId": ARTIST_ID,
                "artistName": "測試歌手",
                "artistLinkUrl": "https://music.apple.com/us/artist/test/%d" % ARTIST_ID,
            }]})
        return httpx.Response(200, text=page(og))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class AppleArtistImageTests(unittest.TestCase):
    def _resolve(self, og):
        async def run():
            import httpx
            async with make_client(og) as client:
                apple = backfill.Apple(client)
                return await apple.artist_image("測試歌手", min_level="exact")

        return asyncio.run(run())

    def test_identity_picture_is_preferred_over_og_image(self):
        found = self._resolve(GENERIC)
        self.assertIsNotNone(found)
        self.assertEqual(found["source"], "apple-music-artist-image")
        self.assertIn("AMCArtistImages", found["url"])

    def test_og_image_is_used_when_there_is_no_identity_picture(self):
        body = page(GENERIC, identity="https://is1-ssl.mzstatic.com/image/thumb/other/x.png")
        import httpx

        def handler(request):
            if request.url.host == "itunes.apple.com":
                return httpx.Response(200, json={"results": [{
                    "artistId": ARTIST_ID,
                    "artistName": "測試歌手",
                    "artistLinkUrl": "https://music.apple.com/us/artist/test/%d" % ARTIST_ID,
                }]})
            return httpx.Response(200, text=body)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await backfill.Apple(client).artist_image("測試歌手", min_level="exact")

        found = asyncio.run(run())
        self.assertIsNotNone(found)
        self.assertEqual(found["source"], "apple-og-image-generic")

    def test_generic_artwork_is_recognised(self):
        self.assertTrue(backfill._GENERIC_APPLE_IMAGE.search(GENERIC))
        self.assertTrue(backfill._GENERIC_APPLE_IMAGE.search(PLACEHOLDER))
        self.assertFalse(backfill._GENERIC_APPLE_IMAGE.search(IDENTITY))

    def test_identity_picture_is_recognised(self):
        self.assertTrue(backfill._IDENTITY_IMAGE.search(IDENTITY))
        self.assertFalse(backfill._IDENTITY_IMAGE.search(GENERIC))

    def test_artist_portrait_is_recognised(self):
        """The mzl.* set holds good portraits, but it also holds other
        people's, so it is found for a person to choose from rather than
        taken on its own."""
        self.assertTrue(backfill._ARTIST_PORTRAIT.search(PORTRAIT_SCRIPT))
        self.assertFalse(backfill._ARTIST_PORTRAIT.search(GENERIC))
        self.assertFalse(backfill._ARTIST_PORTRAIT.search(IDENTITY))

    def test_portrait_set_is_not_taken_automatically(self):
        """Only the identity picture is used on its own; the mzl.* set is
        left alone, because on some pages it opens on somebody else."""
        body = page(GENERIC, identity=PORTRAIT_SCRIPT)
        import httpx

        def handler(request):
            if request.url.host == "itunes.apple.com":
                return httpx.Response(200, json={"results": [{
                    "artistId": ARTIST_ID,
                    "artistName": "測試歌手",
                    "artistLinkUrl": "https://music.apple.com/us/artist/test/%d" % ARTIST_ID,
                }]})
            return httpx.Response(200, text=body)

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await backfill.Apple(client).artist_image("測試歌手", min_level="exact")

        found = asyncio.run(run())
        self.assertIsNotNone(found)
        self.assertNotEqual(found["source"], "apple-music-artist-portrait")
        self.assertEqual(found["source"], "apple-og-image-generic")


class UpscaleTests(unittest.TestCase):
    """Apple serves a picture only with a size segment.

    The regex that finds the artist picture stops before that segment, so
    the URL it returns has none and Apple answers 404 for it. The size
    segment therefore has to be put back on.
    """

    BASE = ("https://is1-ssl.mzstatic.com/image/thumb/AMCArtistImages221/v4/32/2f/40/"
            "322f40b2-6f05-567a-c2a1-ac7de53f3599/"
            "2321431d-ddfd-46a5-89bd-d61f6527ec31_file_cropped.png")

    def test_size_segment_is_appended_when_missing(self):
        out = backfill._upscale(self.BASE)
        self.assertTrue(out.endswith("/800x800bb.jpg"))
        self.assertIn("file_cropped.png", out)

    def test_existing_size_segment_is_replaced(self):
        out = backfill._upscale(self.BASE + "/1200x630cw.png")
        self.assertTrue(out.endswith("/800x800bb.jpg"))
        self.assertNotIn("1200x630cw", out)

    def test_non_apple_url_is_left_alone(self):
        url = "https://api.deezer.com/artist/1/image?size=big"
        self.assertEqual(backfill._upscale(url), url)


class TitleKeyTests(unittest.TestCase):
    def test_release_tag_is_ignored(self):
        self.assertIn("red", backfill._title_keys("Red - Single"))
        self.assertEqual(backfill._title_keys("Red - Single"),
                         backfill._title_keys("Red"))

    def test_feature_suffix_still_meets_the_plain_title(self):
        keys = backfill._title_keys("The Time We Dream (feat. FDSM Elite-Voice Choir)")
        self.assertIn("thetimewedream", keys)

    def test_different_titles_do_not_meet(self):
        self.assertFalse(backfill._title_keys("Meet You")
                         & backfill._title_keys("Meet Yourself"))


class DeezerMatchTests(unittest.TestCase):
    """Deezer is matched by discography, not by name alone.

    謝凌君 is filed by Deezer as "Lisa Djaati" and the library's albums
    turn up under exactly that name, so the album titles are what prove the
    two are the same act.
    """

    def _client(self, albums_by_id):
        import httpx

        def handler(request):
            path = request.url.path
            if path == "/search/artist":
                return httpx.Response(200, json={"data": [
                    {"id": 1, "name": "Lisa Djaati",
                     "picture_big": "https://img/1.jpg"},
                    {"id": 2, "name": "Someone Else",
                     "picture_big": "https://img/2.jpg"},
                ]})
            if path.startswith("/artist/") and path.endswith("/albums"):
                aid = int(path.split("/")[2])
                return httpx.Response(200, json={
                    "data": [{"title": t} for t in albums_by_id.get(aid, [])]})
            return httpx.Response(404, json={})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _picture(self, albums_by_id, library_albums):
        async def run():
            async with self._client(albums_by_id) as client:
                deezer = backfill.Deezer(client)
                return await deezer.artist_picture(["謝凌君"], library_albums)

        return asyncio.run(run())

    def test_album_overlap_identifies_a_differently_named_act(self):
        found = self._picture(
            {1: ["World Of Silver & Gold", "漣漪效應", "Re-Vox"],
             2: ["Unrelated Record"]},
            ["漣漪效應", "Re-Vox", "World Of Silver & Gold", "漫漫"])
        self.assertIsNotNone(found)
        self.assertEqual(found["deezer_name"], "Lisa Djaati")
        self.assertEqual(found["source"], "deezer-artist-picture")
        self.assertEqual(found["confidence"], "high")
        self.assertGreaterEqual(found["album_overlap"], 2)

    def test_act_without_overlapping_albums_is_not_used(self):
        found = self._picture(
            {1: ["Totally Different"], 2: ["Also Different"]},
            ["漣漪效應", "Re-Vox"])
        self.assertIsNone(found)


class ResolveSourceTests(unittest.TestCase):
    """Apple's stand-in artwork must not end up on an artist page."""

    def test_generic_og_image_falls_through_to_the_next_source(self):
        class StubApple:
            async def artist_image(self, name, min_level="alias", exact_name=None):
                return {"url": "https://music.apple.com/assets/meta/apple-music.png",
                        "apple_name": "測試歌手", "artist_id": 1,
                        "match_level": "exact",
                        "source": "apple-og-image-generic"}

        class StubDeezer:
            async def artist_picture(self, names, library_albums):
                return {"url": "https://img/1.jpg", "deezer_name": "測試歌手",
                        "deezer_id": 7, "match_level": "exact",
                        "album_overlap": 2, "matched_albums": ["A", "B"],
                        "source": "deezer-artist-picture",
                        "confidence": "high",
                        "reason": "Deezer artist picture, 2 album title(s) match the library (A; B)",
                        "reference": "https://www.deezer.com/artist/7"}

        async def run():
            artist = {"id": 1, "name": "測試歌手", "aliases": [], "albums": ["A", "B"]}
            return await backfill.resolve(StubApple(), None, StubDeezer(), artist,
                                          {"apple", "deezer"}, use_commons=False)

        found = asyncio.run(run())
        self.assertEqual(found["source"], "deezer-artist-picture")
        self.assertEqual(found["status"], "ok")


if __name__ == "__main__":
    unittest.main()
