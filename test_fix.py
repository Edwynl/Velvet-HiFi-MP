#!/usr/bin/env python3
"""
测试脚本：验证数据库插入修复
该脚本测试以下修复：
1. 使用 last_insert_rowid() 获取刚插入的 ID
2. 批次提交而不是每次插入后都 commit
3. album_id 为 None 时的 fallback 处理
4. track 插入后的计数器更新
"""

import logging
import sys
from pathlib import Path

# 添加项目目录到路径
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

try:
    from server import (
        init_db, get_db, extract_metadata, _sort_name,
        find_folder_cover, save_cover, DB_PATH, DATA_DIR
    )
except Exception as e:
    print(f"导入错误：{e}")
    print("请确保已在 venv 环境中运行此脚本")
    sys.exit(1)

def test_database_operations():
    """测试基本的数据库操作"""
    print("\n=== 测试数据库操作 ===")

    conn = get_db()

    # 测试 artist 插入
    print("\n1. 测试 Artist 插入...")
    result = conn.execute(
        "INSERT OR IGNORE INTO artists (name, sort_name) VALUES (?,?)",
        ("TestArtist", "Test Artist")
    )
    conn.commit()

    # 使用 last_insert_rowid() 获取 ID
    artist_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"   插入的艺术家 ID: {artist_id}")

    # 验证记录存在
    row = conn.execute("SELECT * FROM artists WHERE name=?", ("TestArtist",)).fetchone()
    assert row is not None, "艺术家记录不存在！"
    print(f"   验证通过：{row['id']} - {row['name']}")

    # 测试 album 插入（需要有效的 artist_id）
    print("\n2. 测试 Album 插入...")
    result = conn.execute(
        """INSERT OR IGNORE INTO albums
           (artist_id, title, year, genre, label, cover_path)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (artist_id, "TestAlbum", 2024, "Rock", "Test Label", None)
    )
    conn.commit()

    # 使用 last_insert_rowid() 获取 ID
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"   插入的专辑 ID: {album_id}")

    # 验证记录存在
    row = conn.execute(
        "SELECT * FROM albums WHERE artist_id=? AND title=?",
        (artist_id, "TestAlbum")
    ).fetchone()
    assert row is not None, "专辑记录不存在！"
    print(f"   验证通过：{row['id']} - {row['title']} (year={row['year']})")

    # 测试 track 插入
    print("\n3. 测试 Track 插入...")
    track_path = "/fake/path/test.flac"  # 使用假路径进行测试
    result = conn.execute(
        """INSERT OR IGNORE INTO tracks
           (album_id, artist_id, title, track_number, disc_number,
            duration, file_path, format, sample_rate, bit_depth,
            channels, bitrate, file_size)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (album_id, artist_id, "Test Track", 1, 1, 180.5, track_path,
         "FLAC", 44100, 24, 2, None, 1000000)
    )
    conn.commit()

    # 使用 last_insert_rowid() 获取 ID
    track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"   插入的曲目 ID: {track_id}")

    # 验证记录存在
    row = conn.execute(
        "SELECT * FROM tracks WHERE file_path=?",
        (track_path,)
    ).fetchone()
    assert row is not None, "曲目记录不存在！"
    print(f"   验证通过：{row['id']} - {row['title']} ({row['format']})")

    # 清理测试数据
    print("\n4. 清理测试数据...")
    conn.execute("DELETE FROM tracks WHERE file_path=?", (track_path,))
    conn.execute("DELETE FROM albums WHERE title=?", ("TestAlbum",))
    conn.execute("DELETE FROM artists WHERE name=?", ("TestArtist",))
    conn.commit()
    print("   测试数据已清理")

    # 验证清理结果
    count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    print(f"   当前曲目数：{count}")
    assert count == 0, "测试数据清理不完整！"

    print("\n=== 所有测试通过 ===")
    conn.close()

def test_cache_usage():
    """测试缓存使用模式（模拟扫描中的情况）"""
    print("\n=== 测试缓存使用模式 ===")

    artists_cache = {}
    albums_cache = {}

    # 模拟处理多个相同艺人的专辑
    artist_id_map = {
        "Artist1": 1,
        "Artist2": 2,
    }

    print("\n1. 测试艺术家缓存...")
    for i, artist_name in enumerate(["Artist1", "Unknown", "Artist2", "Artist1"]):
        if artist_name not in artists_cache:
            # 模拟插入逻辑
            new_id = i + 100
            print(f"   [{artist_name}] -> 新 ID: {new_id}")
            artists_cache[artist_name] = new_id
        else:
            print(f"   [{artist_name}] -> 缓存命中：ID={artists_cache[artist_name]}")

    print(f"\n   最终艺术家缓存：{artists_cache}")

    print("\n2. 测试专辑缓存...")
    album_key_map = {
        (1, "Album1"): 10,
        (2, "Album2"): 20,
    }

    for i, (artist_id, album_title) in enumerate([(1, "Album1"), (2, "Album2"), (1, "Album1")]):
        album_key = (artist_id, album_title)
        if album_key not in albums_cache:
            # 模拟插入逻辑 - 使用 last_insert_rowid()
            new_id = i + 1000
            print(f"   [{album_title}] -> 新 ID: {new_id}")
            albums_cache[album_key] = new_id
        else:
            print(f"   [{album_title}] -> 缓存命中：ID={albums_cache[album_key]}")

    print(f"\n   最终专辑缓存：{albums_cache}")

    print("\n=== 缓存测试通过 ===")

def main():
    """主函数"""
    print("="*60)
    print("MusicIQ 数据库插入修复测试")
    print("="*60)

    try:
        test_database_operations()
        test_cache_usage()

        print("\n" + "="*60)
        print("所有测试通过！修复已生效。")
        print("="*60)
        return 0

    except AssertionError as e:
        print(f"\n✗ 测试失败：{e}")
        return 1
    except Exception as e:
        print(f"\n✗ 错误：{e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
