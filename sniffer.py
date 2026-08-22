#!/usr/bin/env python3
"""
PornHao batch scraper – generates M3U with tvg attributes
Usage: python pornhao_batch.py --models "penny-barber,sheena-ryder" --max 20 --group-title "⭐ Favorites" [--headless]
"""

import re
import time
import json
import logging
import argparse
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

BASE_URL = "https://pornhao.com"
HEADLESS = False          # can be overridden by --headless
SCROLL_PAUSE = 2
REQUEST_DELAY = 1

def get_driver(headless=False):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def scroll_to_load_all(driver, max_scrolls=50):
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    while scroll_count < max_scrolls:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight - 200);")
            time.sleep(SCROLL_PAUSE)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                log.info("Reached bottom of page.")
                break
        last_height = new_height
        scroll_count += 1
        log.info(f"Scrolled {scroll_count} times, current height: {new_height}")
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

def extract_video_urls(driver):
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/video/')]"))
        )
    except:
        log.warning("No video links found on page.")
        return []

    links = driver.find_elements(By.XPATH, "//a[contains(@href, '/video/')]")
    video_urls = []
    seen = set()
    for a in links:
        href = a.get_attribute('href')
        if href and href not in seen:
            full_url = urljoin(BASE_URL, href)
            if full_url.startswith(BASE_URL) and '/video/' in full_url:
                seen.add(full_url)
                video_urls.append(full_url)

    if not video_urls:
        links = driver.find_elements(By.XPATH, "//a[@data-href and contains(@data-href, '/video/')]")
        for a in links:
            href = a.get_attribute('data-href')
            if href:
                full_url = urljoin(BASE_URL, href)
                if full_url not in seen:
                    seen.add(full_url)
                    video_urls.append(full_url)

    log.info(f"Found {len(video_urls)} video page URLs.")
    return video_urls

def get_model_avatar(driver):
    try:
        selectors = [
            "img.avatar",
            "img.model-avatar",
            "div.avatar img",
            "img[alt*='model']",
            "img.photo"
        ]
        for sel in selectors:
            try:
                img = driver.find_element(By.CSS_SELECTOR, sel)
                src = img.get_attribute('src')
                if src and src.startswith('http'):
                    return src
            except:
                continue
        imgs = driver.find_elements(By.TAG_NAME, "img")
        for img in imgs:
            src = img.get_attribute('src')
            if src and 'avatar' in src.lower():
                return src
        return ""
    except Exception as e:
        log.warning(f"Could not extract avatar: {e}")
        return ""

def get_video_info(video_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': BASE_URL + '/'
    }
    try:
        r = requests.get(video_url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning(f"Failed to fetch {video_url}: {e}")
        return None

    soup = BeautifulSoup(r.text, 'html.parser')

    title = "Unknown"
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)
        title = re.sub(r'\s*[-|]\s*PornHao.*$', '', title).strip()
        if not title:
            title = "Unknown"

    thumbnail = ""
    og_img = soup.find('meta', property='og:image')
    if og_img and og_img.get('content'):
        thumbnail = og_img['content']
    if not thumbnail:
        video_tag = soup.find('video')
        if video_tag and video_tag.get('poster'):
            thumbnail = video_tag['poster']
    if not thumbnail:
        img = soup.find('img', class_=re.compile(r'thumb|poster|cover|preview'))
        if img and img.get('src'):
            thumbnail = img['src']
    if not thumbnail:
        player_div = soup.find('div', class_=re.compile(r'video[-_]player|player[-_]container'))
        if player_div:
            img = player_div.find('img')
            if img and img.get('src'):
                thumbnail = img['src']
    if thumbnail and not thumbnail.startswith('http'):
        thumbnail = urljoin(BASE_URL, thumbnail)

    video_tag = soup.find('video')
    if video_tag and video_tag.get('src'):
        stream_url = video_tag['src']
        stream_url = stream_url.replace('&amp;', '&')
        return {'url': stream_url, 'title': title, 'thumbnail': thumbnail}

    m3u8_pattern = re.compile(r'(https?://[^\s,"\']+\.m3u8[^\s,"\']*)')
    match = m3u8_pattern.search(r.text)
    if match:
        return {'url': match.group(1), 'title': title, 'thumbnail': thumbnail}

    mp4_pattern = re.compile(r'(https?://[^\s,"\']+\.mp4[^\s,"\']*)')
    match = mp4_pattern.search(r.text)
    if match:
        return {'url': match.group(1), 'title': title, 'thumbnail': thumbnail}

    log.warning(f"No valid stream URL found in {video_url}")
    return None

def scrape_model(model_name, max_videos=None, headless=False):
    model_url = urljoin(BASE_URL, f"/models/{model_name}/")
    log.info(f"Scraping model: {model_url}")

    driver = get_driver(headless=headless)
    try:
        driver.get(model_url)
        time.sleep(3)
        avatar_url = get_model_avatar(driver)
        log.info(f"Model avatar: {avatar_url if avatar_url else 'not found'}")

        scroll_to_load_all(driver)
        video_page_urls = extract_video_urls(driver)
        log.info(f"Total video pages found: {len(video_page_urls)}")
    finally:
        driver.quit()

    if not video_page_urls:
        log.warning(f"No videos found for model {model_name}.")
        return []

    if max_videos:
        video_page_urls = video_page_urls[:max_videos]

    results = []
    for idx, v_url in enumerate(video_page_urls, 1):
        log.info(f"[{idx}/{len(video_page_urls)}] Processing: {v_url}")
        info = get_video_info(v_url)
        if info:
            logo = info.get('thumbnail') or avatar_url
            results.append({
                "model": model_name,
                "title": info['title'],
                "page_url": v_url,
                "stream_url": info['url'],
                "thumbnail": logo
            })
            log.info(f"  Found stream: {info['url'][:80]}... thumbnail: {logo[:60] if logo else 'none'}")
        else:
            log.warning(f"  No stream found for {v_url}")
        time.sleep(REQUEST_DELAY)

    log.info(f"Successfully extracted {len(results)} streams for {model_name}.")
    return results

def scrape_models(model_names, max_videos=None, headless=False):
    all_results = []
    for model in model_names:
        log.info(f"\n{'='*60}")
        log.info(f"Processing model: {model}")
        log.info(f"{'='*60}")
        results = scrape_model(model.strip(), max_videos, headless=headless)
        all_results.extend(results)
        time.sleep(2)
    return all_results

def save_playlist(entries, json_file="playlist.json", m3u_file="playlist.m3u",
                  group_title="PornHao", display_prefix=""):
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    m3u_lines = ['#EXTM3U', '']
    for e in entries:
        model = e.get('model', 'unknown')
        title = e.get('title', model)
        stream = e.get('stream_url', '')
        logo = e.get('thumbnail', '')

        tvg_id = model.lower()
        tvg_name = model.replace('-', ' ').title()
        display_name = f"{display_prefix}{title}" if display_prefix else title
        group = group_title

        extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{tvg_name}" tvg-logo="{logo}" group-title="{group}",{display_name}'
        m3u_lines.append(extinf)
        m3u_lines.append(stream)
        m3u_lines.append('')

    with open(m3u_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(m3u_lines))

    log.info(f"Saved {len(entries)} entries to {json_file} and {m3u_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch scrape from pornhao.com and output M3U with tvg attributes")
    parser.add_argument('--models', default='violet-myers',
                        help='Comma‑separated list of model names (e.g. violet-myers,penny-barber)')
    parser.add_argument('--max', type=int, default=None, help='Maximum videos per model')
    parser.add_argument('--group-title', default='⭐ Favorites', help='Group title for M3U')
    parser.add_argument('--display-prefix', default='⭐ ', help='Prefix for display names')
    parser.add_argument('--output-json', default='playlist.json', help='Output JSON file')
    parser.add_argument('--output-m3u', default='playlist.m3u', help='Output M3U file')
    parser.add_argument('--headless', action='store_true', help='Run Chrome in headless mode')
    args = parser.parse_args()

    model_list = [m.strip() for m in args.models.split(',')]
    log.info(f"Will process {len(model_list)} models: {model_list}")

    entries = scrape_models(model_list, max_videos=args.max, headless=args.headless)
    if entries:
        save_playlist(entries, json_file=args.output_json, m3u_file=args.output_m3u,
                      group_title=args.group_title, display_prefix=args.display_prefix)
    else:
        log.warning("No videos found.")
