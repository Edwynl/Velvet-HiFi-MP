#!/usr/bin/env python3
"""Test Discogs API integration."""

import asyncio
import sys
sys.path.insert(0, '.')

import enrichment

async def test_discogs():
    print("Testing Discogs API...\n")

    # Test 1: Search for a famous album
    print("1. Testing search_discogs('Michael Jackson', 'Thriller'):")
    results = await enrichment.search_discogs("Michael Jackson", "Thriller", limit=5)
    print(f"   Found {len(results)} results")
    for r in results[:3]:
        print(f"   - {r.get('title')} ({r.get('year')}) - {r.get('format')}")

    # Test 2: Get master release
    print("\n2. Testing search_discogs_master('Pink Floyd', 'Dark Side of the Moon'):")
    master = await enrichment.search_discogs_master("Pink Floyd", "Dark Side of the Moon")
    if master:
        print(f"   Found master: {master.get('title')} ({master.get('year')})")
        print(f"   Genres: {master.get('genres')}")
        print(f"   Styles: {master.get('styles')}")
        print(f"   Track count: {len(master.get('tracklist', []))}")
    else:
        print("   No master release found")

    # Test 3: Get specific release details
    if results:
        discogs_id = results[0].get('discogs_id')
        print(f"\n3. Testing get_discogs_release({discogs_id}):")
        release = await enrichment.get_discogs_release(discogs_id)
        if release:
            print(f"   Title: {release.get('title')}")
            print(f"   Year: {release.get('year')}")
            print(f"   Country: {release.get('country')}")
            print(f"   Formats: {release.get('formats')}")
            print(f"   Labels: {release.get('labels')}")
            print(f"   Tracklist: {len(release.get('tracklist', []))} tracks")

    print("\n✓ Discogs API is working!")

if __name__ == "__main__":
    asyncio.run(test_discogs())
