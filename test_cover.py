#!/usr/bin/env python3
"""Quick test for cover search."""

import asyncio
import sys
sys.path.insert(0, '.')
import enrichment

async def test():
    print("Testing cover search for a Chinese album...")

    # Test with a Chinese album
    results = await enrichment.search_album_covers("周杰伦", "依然范特西")
    print(f"Results for 周杰伦 - 依然范特西: {len(results)}")
    for r in results[:3]:
        print(f"  - {r.get('source')}: {r.get('url', '')[:80]}")

    print("\nTesting with English album...")
    results2 = await enrichment.search_album_covers("Michael Jackson", "Thriller")
    print(f"Results for Michael Jackson - Thriller: {len(results2)}")
    for r in results2[:3]:
        print(f"  - {r.get('source')}: {r.get('url', '')[:80]}")

if __name__ == "__main__":
    asyncio.run(test())
