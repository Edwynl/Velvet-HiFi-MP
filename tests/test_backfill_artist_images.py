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


if __name__ == "__main__":
    unittest.main()
