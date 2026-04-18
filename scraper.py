#!/usr/bin/env python3
"""
HITMaal Video Scraper (Fresh Mode)
- Always overwrite JSON with latest scraped data
- Pagination supported
- Stops safely on 404
"""

import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
from urllib.parse import urljoin

# ==========================
# CONFIG
# ==========================
BASE_URL = "https://hmaal.net/"
JSON_FILE = "hitmall.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# ==========================
# FETCH PAGE
# ==========================
def fetch_page(url):
    print(f"Fetching: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"Error: {e}")
        return None

# ==========================
# EXTRACT EPISODES
# ==========================
def extract_episodes(html):
    soup = BeautifulSoup(html, "html.parser")
    episodes = []

    cards = soup.select("a.video")

    for card in cards:
        title = card.get("title", "").strip()
        link = urljoin(BASE_URL, card.get("href", "").strip())

        duration = ""
        upload_time = ""
        thumbnail = ""

        duration_elem = card.find("span", class_="time")
        if duration_elem:
            duration = duration_elem.get_text(strip=True)

        ago_elem = card.find("span", class_="ago")
        if ago_elem:
            upload_time = ago_elem.get_text(strip=True)

        # Extract thumbnail from inline style
        style = card.get("style", "")
        match = re.search(r'url\(["\']?(.*?)["\']?\)', style)
        if match:
            thumbnail = match.group(1)

        episodes.append({
            "title": title,
            "duration": duration,
            "upload_time": upload_time,
            "link": link,
            "thumbnail": thumbnail
        })

    return episodes

# ==========================
# SCRAPE ALL PAGES
# ==========================
def scrape_all_pages():
    page = 1
    all_episodes = []

    while True:
        url = BASE_URL if page == 1 else f"{BASE_URL}page/{page}/"
        html = fetch_page(url)

        if html is None:
            print("Stopping (404 or error)")
            break

        episodes = extract_episodes(html)

        if not episodes:
            print("No more videos found")
            break

        print(f"Page {page}: {len(episodes)} videos")
        all_episodes.extend(episodes)

        page += 1

    return all_episodes

# ==========================
# SAVE (OVERWRITE JSON)
# ==========================
def save_data(episodes):
    data = {
        "source": BASE_URL,
        "created_at": datetime.now().isoformat(),
        "total": len(episodes),
        "episodes": episodes
    }

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(episodes)} latest videos")

# ==========================
# MAIN
# ==========================
def main():
    print("🔥 HITMaal Scraper (Fresh Mode)")
    print("=" * 40)

    episodes = scrape_all_pages()
    print(f"Total scraped: {len(episodes)}")

    save_data(episodes)

    print("✅ DONE (Old JSON replaced)")

# ==========================
if __name__ == "__main__":
    main()
