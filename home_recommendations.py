"""Small, explainable album shelves from the local library; no remote catalog calls."""
from collections import Counter
import math
import re
import sqlite3


_ASIAN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_ASIAN_GENRES = re.compile(r"mandopop|cantopop|c[ -]?pop|j[ -]?pop|k[ -]?pop|chinese|japanese|korean|华语|華語|国语|國語|粤语|粵語|港台|日语|日語|韩语|韓語", re.I)
_CHINESE_GENRES = re.compile(r"mandopop|cantopop|c[ -]?pop|chinese|华语|華語|国语|國語|粤语|粵語|港台", re.I)
_GLOBAL_POP = re.compile(r"pop|rock|r&b|soul|hip[ -]?hop|singer.songwriter|electronic", re.I)
_COLLECTIVE_ARTISTS = frozenset((
    'various artists', 'various artist', 'various', 'va', 'v.a.', 'v.a',
    '群星', '群星合辑', '群星合輯', '众星', '眾星', '合辑', '合輯',
    'unknown', 'unknown artist', '未知', '未知歌手', '未知艺术家', '未知藝術家',
))
# Language tags are absent in older imports. Be conservative: a Latin-script
# name alone does not make a Korean/Japanese artist an English-language pick.
_ENGLISH_POP_ARTISTS = frozenset(name.casefold() for name in (
    'Adele', 'Taylor Swift', 'Ed Sheeran', 'Billie Eilish', 'Coldplay', 'Dua Lipa',
    'Bruno Mars', 'The Weeknd', 'Ariana Grande', 'Olivia Rodrigo', 'Harry Styles',
    'Lana Del Rey', 'Sabrina Carpenter', 'Lady Gaga', 'Beyoncé', 'Beyonce',
    'Michael Jackson', 'Madonna', 'Whitney Houston', 'Mariah Carey', 'Celine Dion',
    'Céline Dion', 'The Beatles', 'ABBA', 'Elton John', 'Queen', 'Sam Smith',
    'Justin Bieber', 'Justin Timberlake', 'Katy Perry', 'Rihanna', 'Sia', 'Maroon 5',
))
# Familiar full-length records are a cold-start preference, not a live chart.
# Listening/favorite affinity still takes precedence over this small editorial boost.
_FAMILIAR_POP_ALBUMS = re.compile(
    r"^(21|25|30|1989|reputation|lover|folklore|evermore|midnights|red|"
    r"the tortured poets department|24k magic|doo-wops & hooligans|unorthodox jukebox|"
    r"an evening with silk sonic|hit me hard and soft|happier than ever|"
    r"future nostalgia|after hours|dawn fm|sour|guts|harry.s house|"
    r"eternal sunshine|thank u, next|thriller|bad|abbey road|parachutes|"
    r"a rush of blood to the head|no\.6 collaborations project|[+=÷])(?:\s*(?:\(|\[|:)|$)", re.I)


def parse_home_ids(value: str, limit: int = 32) -> list[int]:
    """Bounded, positive IDs only, preserving the browser's ranking."""
    result = []
    for item in (value or "").split(",")[:64]:
        if item.isascii() and item.isdigit() and 0 < int(item) <= 2**63 - 1:
            number = int(item)
            if number not in result:
                result.append(number)
                if len(result) >= limit:
                    break
    return result


def fetch_home_shelves(db: sqlite3.Connection, album_ids: list[int], artist_ids: list[int]) -> dict:
    rows = [dict(row) for row in db.execute("""
        SELECT al.id, al.artist_id, al.title, al.year, al.genre, al.created_at,
               al.is_favorite, ar.name AS artist_name,
               COALESCE(p.plays, 0) AS play_count, p.last_played,
               EXISTS(SELECT 1 FROM tracks hi WHERE hi.album_id = al.id
                      AND (hi.sample_rate > 44100 OR hi.bit_depth > 16)) AS is_hires
        FROM albums al JOIN artists ar ON ar.id = al.artist_id
        LEFT JOIN (
            SELECT t.album_id, COUNT(*) AS plays, MAX(ph.played_at) AS last_played
            FROM play_history ph JOIN tracks t ON t.id = ph.track_id GROUP BY t.album_id
        ) p ON p.album_id = al.id
        WHERE EXISTS(SELECT 1 FROM tracks t WHERE t.album_id = al.id)
    """)]
    by_id = {row["id"]: row for row in rows}
    opened = [by_id[i] for i in album_ids[:12] if i in by_id]
    artist_weight, genre_weight = Counter(), Counter()
    for index, artist_id in enumerate(artist_ids[:32]):
        artist_weight[artist_id] += max(2, 16 - index)
    for index, row in enumerate(opened):
        weight = max(2, 12 - index)
        artist_weight[row["artist_id"]] += weight
        genre_weight[(row["genre"] or "").casefold()] += weight
    artist_rows = {}
    for row in rows:
        artist = artist_rows.setdefault(row["artist_id"], {
            "id": row["artist_id"], "name": row["artist_name"], "album_count": 0, "play_count": 0,
        })
        artist["album_count"] += 1
        artist["play_count"] += row["play_count"]
        if row["play_count"]:
            weight = min(10, math.log2(row["play_count"] + 1))
            artist_weight[row["artist_id"]] += weight
            genre_weight[(row["genre"] or "").casefold()] += weight
        if row["is_favorite"]:
            artist_weight[row["artist_id"]] += 2
            genre_weight[(row["genre"] or "").casefold()] += 2

    # Browsing frequency is authoritative for the artist shelf; playback fills gaps.
    # Compilation credits are valid album metadata, but are not artist entries.
    artist_rows = {i: artist for i, artist in artist_rows.items()
                   if ' '.join(artist['name'].split()).casefold() not in _COLLECTIVE_ARTISTS}
    frequent = [artist_rows[i] for i in artist_ids[:32] if i in artist_rows][:8]
    frequent_ids = {row["id"] for row in frequent}
    for artist in sorted(artist_rows.values(), key=lambda a: (-a["play_count"], a["name"])):
        if len(frequent) == 8:
            break
        if artist["id"] not in frequent_ids and artist["play_count"] > 0:
            frequent.append(artist)
            frequent_ids.add(artist["id"])

    recent_ids = {row["id"] for row in sorted(rows, key=lambda a: (a["created_at"] or 0, a["id"]), reverse=True)[:24]}
    excluded = recent_ids | {row["id"] for row in opened}

    def score(row):
        return (min(40, artist_weight[row["artist_id"]]) * 3
                + min(20, genre_weight[(row["genre"] or "").casefold()])
                + 5 * bool(row["is_favorite"]) + min(10, math.log2(row["play_count"] + 1)))

    asia, global_pop = [], []
    for row in rows:
        if row["id"] in excluded:
            continue
        genre = row["genre"] or ""
        if _ASIAN.search(row["artist_name"] + row["title"]) or _ASIAN_GENRES.search(genre):
            row["recommendation_reason"] = ("From an artist you return to" if artist_weight[row["artist_id"]] else
                                            "Matches your listening styles" if genre_weight[genre.casefold()] else
                                            "Asian selections from your library")
            asia.append(row)
        elif _GLOBAL_POP.search(genre) and row['artist_name'].casefold() in _ENGLISH_POP_ARTISTS:
            row["recommendation_reason"] = "Familiar pop from your library"
            global_pop.append(row)

    def select(candidates, limit, chinese_first=False, per_artist_limit=2):
        ordered = sorted(candidates, key=lambda r: (
            score(r) + (12 if chinese_first and _CHINESE_GENRES.search(r["genre"] or "") else 0)
            + (8 if not chinese_first and _FAMILIAR_POP_ALBUMS.search(r["title"]) else 0),
            r["play_count"], r["year"] or 0, r["id"],
        ), reverse=True)
        chosen, per_artist = [], Counter()
        for row in ordered:
            if per_artist[row["artist_id"]] >= per_artist_limit:
                continue
            chosen.append(row)
            per_artist[row["artist_id"]] += 1
            if len(chosen) == limit:
                break
        return chosen

    return {"recently_opened": opened, "frequent_artists": frequent,
            "for_you": select(asia, 10, True), "global_picks": select(global_pop, 2, per_artist_limit=1)}
