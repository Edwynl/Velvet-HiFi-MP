import sqlite3
import os

db_path = os.path.join(os.environ.get('DATA_DIR', 'velvet_data'), 'library.db')
print(f"Checking database at {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. Check all artists with 0 albums
print("\nArtists with 0 albums (as per standard query):")
rows = cursor.execute("""
    SELECT ar.id, ar.name,
           (SELECT COUNT(DISTINCT album_id) FROM tracks t 
            JOIN track_artists ta ON t.id = ta.track_id 
            WHERE ta.artist_id = ar.id) as album_count,
           (SELECT COUNT(*) FROM track_artists ta WHERE ta.artist_id = ar.id) as track_count
    FROM artists ar
    ORDER BY album_count DESC
    LIMIT 100
""").fetchall()
for r in rows:
    print(f"ID: {r[0]}, Name: {r[1]}, Albums: {r[2]}, Tracks: {r[3]}")

# 2. Check track_artists table size
count = cursor.execute("SELECT COUNT(*) FROM track_artists").fetchone()[0]
print(f"\nTotal records in track_artists: {count}")

# 3. Sample an artist with 0 albums and see if they are in track_artists
if rows:
    artist_id = rows[0][0]
    artist_name = rows[0][1]
    print(f"\nChecking track links for '{artist_name}' (ID: {artist_id})...")
    links = cursor.execute("SELECT * FROM track_artists WHERE artist_id=?", (artist_id,)).fetchall()
    print(f"Found {len(links)} links in track_artists")
    
    tracks = cursor.execute("SELECT id, title, album_id FROM tracks WHERE artist_id=?", (artist_id,)).fetchall()
    print(f"Found {len(tracks)} tracks in 'tracks' table where they are primary")

# 4. Check if we need to backfill track_artists for all existing tracks
print("\nVerifying if all tracks have at least one entry in track_artists...")
missing = cursor.execute("""
    SELECT COUNT(*) FROM tracks t 
    WHERE NOT EXISTS (SELECT 1 FROM track_artists ta WHERE ta.track_id = t.id)
""").fetchone()[0]
print(f"Tracks missing from track_artists: {missing}")

conn.close()
