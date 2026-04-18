import requests
from bs4 import BeautifulSoup
import re
import urllib.parse
import json
import time
import os

BASE_URL = "https://desihub.org"
TOTAL_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# 🔹 Clean title
def clean_title(text):
    # remove "Featured image for"
    text = re.sub(r"^Featured image for\s*", "", text, flags=re.IGNORECASE)

    # remove unwanted words
    banned = [
        "fucked", "sex", "pussy", "blowjob",
        "xxx", "porn", "nude", "adult", "explicit",
        "dick", "boob", "masturbating", "sucking"
    ]

    for word in banned:
        text = re.sub(word, "", text, flags=re.IGNORECASE)

    # remove extra spaces
    return re.sub(r"\s+", " ", text).strip()


# 🔹 Decode Next.js image URL
def get_real_image(src):
    try:
        match = re.search(r"url=(.*?)&", src)
        if match:
            return urllib.parse.unquote(match.group(1))
        return src
    except:
        return src


# 🔹 Get embed link
def get_embed(page_url):
    try:
        res = requests.get(page_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        iframe = soup.find("iframe")
        if iframe and iframe.get("src"):
            return iframe["src"]

        video = soup.find("video")
        if video:
            source = video.find("source")
            if source and source.get("src"):
                return source["src"]

        return None

    except Exception as e:
        print("❌ Embed error:", e)
        return None


# 🔹 Scrape one page
def scrape_page(url):
    results = []

    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        for img in soup.find_all("img"):
            alt = img.get("alt")
            src = img.get("src")

            if not alt or not src:
                continue

            title = clean_title(alt)
            image = get_real_image(src)

            parent = img.find_parent("a")
            if parent and parent.get("href"):
                page_url = BASE_URL + parent.get("href")

                embed = get_embed(page_url)

                results.append({
                    "title": title,
                    "image": image,
                    "page": page_url,
                    "embed": embed
                })

                print("✅", title)

                time.sleep(0.3)

    except Exception as e:
        print("❌ Page error:", e)

    return results


# 🔥 Scrape all pages
def scrape_all():
    all_data = []

    for page in range(1, TOTAL_PAGES + 1):
        print(f"\n📄 Scraping Page {page}...\n")

        if page == 1:
            url = BASE_URL
        else:
            url = f"{BASE_URL}/page/{page}"

        data = scrape_page(url)
        all_data.extend(data)

    return all_data


# ▶️ RUN
if __name__ == "__main__":
    try:
        data = scrape_all()

        # create api folder
        os.makedirs("api", exist_ok=True)

        # save JSON
        with open("api/desihub.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print("\n💾 Saved to api/desihub.json")

    except Exception as e:
        print("❌ Fatal Error:", e)
