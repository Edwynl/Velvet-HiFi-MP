import logging
import traceback
import os
from server import extract_metadata, init_db, get_db, _sort_name, find_folder_cover, save_cover, DB_PATH

logging.basicConfig(level=logging.DEBUG)
MUSIC_DIR = os.environ.get("VELVET_MUSIC_DIR") or os.environ.get("MUSIC_DIR") or os.path.join(os.path.expanduser("~"), "Music")

def test_scan():
    if not os.path.isdir(MUSIC_DIR):
        print(f"Music directory not found: {MUSIC_DIR}")
        return

    try:
        all_files = []
        for root, dirs, files in os.walk(MUSIC_DIR):
            for f in files:
                all_files.append(os.path.join(root, f))
    except Exception as e:
        print("Error walking:", e)
        return
        
    with open('scan_test.log', 'w', encoding='utf-8') as logf:
        logf.write(f"Found {len(all_files)} files in {MUSIC_DIR}\n")
        
        db = get_db()
        for fp in all_files[:20]:
            logf.write(f"\nProcessing: {fp}\n")
            try:
                meta = extract_metadata(fp)
                logf.write(f"Meta tags extracted.\n")
                
                artist_name = (meta['album_artist'] or meta['artist']).strip() or "Unknown Artist"
                logf.write(f"Artist: {artist_name}\n")
                
                # Insert artist
                db.execute("INSERT OR IGNORE INTO artists (name, sort_name) VALUES (?,?)", 
                           (artist_name, _sort_name(artist_name)))
                row = db.execute("SELECT id FROM artists WHERE name=?", (artist_name,)).fetchone()
                if not row:
                    logf.write("ERROR: Artist row is None!\n")
                    continue
                artist_id = row[0]
                
                # Insert album
                album = meta['album']
                logf.write(f"Album: {album}\n")
                db.execute("INSERT OR IGNORE INTO albums (artist_id, title) VALUES (?,?)", (artist_id, album))
                row = db.execute("SELECT id FROM albums WHERE artist_id=? AND title=?", (artist_id, album)).fetchone()
                if not row:
                    logf.write("ERROR: Album row is None!\n")
                    continue
                album_id = row[0]
                
                # Insert track
                db.execute(
                    """INSERT OR IGNORE INTO tracks
                       (album_id, artist_id, title, track_number, disc_number,
                        duration, file_path, format, sample_rate, bit_depth,
                        channels, bitrate, file_size)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (album_id, artist_id, meta['title'],
                     meta['track_number'], meta['disc_number'],
                     meta['duration'], fp, meta['format'],
                     meta['sample_rate'], meta['bit_depth'],
                     meta['channels'], meta['bitrate'],
                     os.path.getsize(fp))
                )
                logf.write(f"Track '{meta['title']}' inserted.\n")

            except Exception as e:
                logf.write(f"EXCEPTION: {e}\n{traceback.format_exc()}\n")

        db.commit()
        db.close()

if __name__ == '__main__':
    test_scan()
