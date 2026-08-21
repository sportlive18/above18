import re
import os
import json
import time
import logging
import argparse
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

BASE_URL = "https://kaamdesi.com"
DELAY = 0.5           # reduced from 2s
BATCH_PAGES = 20
REFRESH_PAGES = 2
MAX_PAGE_LIMIT = 300
MAX_WORKERS = 6       # parallel video-page fetches per listing page
IFRAME_WORKERS = 3    # parallel iframe fetches

STATE_FILE = "scraper_state.json"
PLAYLIST_JSON = "playlist.json"
PLAYLIST_M3U = "playlist.m3u"

# Thread-local scraper so each thread has its own session
_local = threading.local()


# ==================== STATE ====================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'last_page': 0, 'completed': False, 'total_videos': 0,
            'last_run': '', 'run_count': 0, 'last_mode': ''}


def save_state(state):
    state['last_run'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    state['run_count'] = state.get('run_count', 0) + 1
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    log.info(f"💾 State: page={state['last_page']}, completed={state['completed']}, videos={state['total_videos']}")


def load_existing():
    if os.path.exists(PLAYLIST_JSON):
        try:
            with open(PLAYLIST_JSON, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def dedup(entries):
    seen = {}
    for e in entries:
        url = e.get('stream_url', '')
        if url:
            seen[url] = e
    return list(seen.values())


# ==================== HTTP ====================

def make_scraper():
    s = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': BASE_URL + '/',
    })
    return s


def get_scraper():
    """Return a thread-local scraper instance."""
    if not hasattr(_local, 'scraper'):
        _local.scraper = make_scraper()
    return _local.scraper


def fetch(s, url, ref=None, tries=3):
    h = {}
    if ref:
        h['Referer'] = ref
    for attempt in range(tries):
        try:
            r = s.get(url, timeout=20, headers=h, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
            log.warning(f"HTTP {r.status_code}: {url[:80]}")
        except Exception as e:
            log.warning(f"Attempt {attempt+1}: {e}")
        time.sleep(DELAY * (attempt + 1))
    return None


# ==================== PAGE CHECKS ====================

def is_page_valid(html):
    if not html:
        return False
    # Fast string check before full parse
    if '404' in html[:2000] or 'not found' in html[:2000].lower():
        soup = BeautifulSoup(html, 'lxml')
        title = soup.find('title')
        if title:
            t = title.get_text(strip=True).lower()
            if '404' in t or 'not found' in t:
                return False
    return True


def has_next_page(html, current_page):
    soup = BeautifulSoup(html, 'lxml')
    nxt = current_page + 1
    for sel in [f'a[href*="/page/{nxt}"]', 'a.next', 'a.nextpostslink',
                '.pagination a.next', '.nav-links a.next', 'a[rel="next"]',
                '.next a', 'li.next a', '.wp-pagenavi a.nextpostslink']:
        if soup.select_one(sel):
            return True
    max_p = current_page
    for link in soup.select('.pagination a, .nav-links a, .wp-pagenavi a'):
        m = re.search(r'/page/(\d+)', link.get('href', ''))
        if m:
            max_p = max(max_p, int(m.group(1)))
        t = link.get_text(strip=True)
        if t.isdigit():
            max_p = max(max_p, int(t))
    return max_p > current_page


# ==================== EXTRACTION ====================

def get_listings(html, page_url):
    soup = BeautifulSoup(html, 'lxml')
    items = []
    seen = set()
    bd = urlparse(BASE_URL).netloc.replace('www.', '')

    for sel in ['article', '.post', '.video-item', '.entry', '.item',
                '.thumb-block', '.video-block', '.post-item', '.hentry', '.type-post']:
        for block in soup.select(sel):
            link = block.find('a', href=True)
            if not link:
                continue
            href = urljoin(page_url, link['href'].strip())
            if not is_content_url(href, bd) or href in seen:
                continue
            title = extract_block_title(block)
            thumb = extract_block_thumbnail(block, page_url)
            if title:
                seen.add(href)
                items.append({'page_url': href, 'title': title, 'thumbnail': thumb})

    for h_tag in soup.select('h1 a, h2 a, h3 a, h4 a, .entry-title a, .post-title a'):
        href = urljoin(page_url, h_tag.get('href', '').strip())
        if not is_content_url(href, bd) or href in seen:
            continue
        title = h_tag.get_text(strip=True)
        if not title or len(title) < 3:
            continue
        parent = h_tag.find_parent(['article', 'div', 'li', 'section'])
        thumb = extract_block_thumbnail(parent, page_url) if parent else ''
        seen.add(href)
        items.append({'page_url': href, 'title': title, 'thumbnail': thumb})

    for a_tag in soup.find_all('a', href=True):
        href = urljoin(page_url, a_tag['href'].strip())
        if not is_content_url(href, bd) or href in seen:
            continue
        img = a_tag.find('img')
        if not img:
            continue
        title = (img.get('alt', '') or img.get('title', '') or a_tag.get('title', '')).strip()
        if not title or len(title) < 3:
            continue
        thumb = get_img_src(img, page_url)
        seen.add(href)
        items.append({'page_url': href, 'title': title, 'thumbnail': thumb})

    for item in items:
        item['title'] = clean_title(item['title'])
    return items


def extract_block_title(block):
    if not block:
        return ''
    for h in block.find_all(['h1', 'h2', 'h3', 'h4']):
        t = h.get_text(strip=True)
        if t and len(t) >= 3:
            return t
    for sel in ['.entry-title', '.post-title', '.title', '.video-title']:
        el = block.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) >= 3:
                return t
    fl = block.find('a', href=True)
    if fl:
        t = fl.get('title', '').strip()
        if t and len(t) >= 3:
            return t
    img = block.find('img')
    if img:
        alt = (img.get('alt', '') or img.get('title', '')).strip()
        if alt and len(alt) >= 3:
            return alt
    if fl:
        t = fl.get_text(strip=True)
        if t and len(t) >= 5:
            return t
    return ''


def extract_block_thumbnail(block, page_url):
    if not block:
        return ''
    for img in block.find_all('img'):
        src = get_img_src(img, page_url)
        if src and not is_tiny(src):
            return src
    for el in block.find_all(True, style=True):
        m = re.search(r'background(?:-image)?\s*:\s*url\(["\']?([^"\')\s]+)', el.get('style', ''), re.I)
        if m:
            return urljoin(page_url, m.group(1))
    for el in block.find_all(True):
        for attr in ['data-thumb', 'data-thumbnail', 'data-bg', 'data-image',
                     'data-src', 'data-lazy-src', 'data-original', 'data-poster']:
            val = el.get(attr, '').strip()
            if val and not is_tiny(val):
                return urljoin(page_url, val)
    return ''


def get_img_src(img, page_url):
    for attr in ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-thumb']:
        val = img.get(attr, '').strip()
        if val and not val.startswith('data:'):
            return urljoin(page_url, val)
    return ''


def is_tiny(url):
    ul = url.lower()
    return any(t in ul for t in ['1x1', 'spacer', 'blank', 'pixel', 'loading',
                                  'spinner', 'placeholder', 'avatar', 'icon',
                                  'logo', 'gravatar', 'emoji', 'smilies', 'wp-includes'])


def is_content_url(url, bd):
    parsed = urlparse(url)
    if bd not in parsed.netloc:
        return False
    path = parsed.path.lower().rstrip('/')
    if not path or path == '/':
        return False
    skip = ['/page', '/category', '/tag', '/author', '/wp-admin', '/wp-content',
            '/wp-includes', '/wp-json', '/feed', '/login', '/register', '/search',
            '/contact', '/about', '/privacy', '/terms', '/dmca', '/sitemap',
            '/comments', '/trackback', '/xmlrpc', '/wp-login', '/cdn-cgi']
    if any(path.startswith(s) or path == s for s in skip):
        return False
    skip_ext = ['.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
                '.xml', '.txt', '.pdf', '.zip', '.mp4', '.mp3']
    if any(path.endswith(ext) for ext in skip_ext):
        return False
    return True


def clean_title(title):
    if not title:
        return ''
    title = ' '.join(title.split())
    for sep in [' - ', ' | ', ' – ', ' — ', ' :: ']:
        if sep in title:
            parts = title.split(sep)
            title = max(parts, key=len)
    return title.strip('| -–—·•>»').strip()[:250]


# ==================== MP4 FINDING ====================

# Precompile patterns once
_MP4_PATTERNS = [
    re.compile(r'(https?://[^\s"\'<>\\\)]+\.mp4[^\s"\'<>\\\)]*)', re.I),
    re.compile(r'(https?:\\/\\/[^\s"\'<>]+\.mp4[^\s"\'<>]*)', re.I),
    re.compile(r'(//[^\s"\'<>\\\)]+\.mp4[^\s"\'<>\\\)]*)', re.I),
    re.compile(r'(https?%3A%2F%2F[^\s"\'<>&]+\.mp4[^\s"\'<>&]*)', re.I),
    re.compile(r'(https?://server\d+\.mmsbee\d*\.[a-z]+/[^\s"\'<>\\\)]+\.mp4)', re.I),
]
_SCRIPT_MP4_PATTERN = re.compile(r'''["'](https?://[^"']+\.mp4[^"']*)["']''', re.I)
_SCRIPT_ESC_PATTERN = re.compile(r'''["'](https?:\\/\\/[^"']+\.mp4[^"']*)["']''', re.I)


def find_mp4(html, page_url):
    found = set()

    # Fast regex scan on raw HTML (no parse needed)
    for p in _MP4_PATTERNS:
        for m in p.findall(html):
            url = m.replace('\\/', '/')
            if url.startswith('//'):
                url = 'https:' + url
            if '%3A' in url:
                url = unquote(url)
            url = clean_url(url)
            if url:
                found.add(url)

    # Parse once, reuse soup
    soup = BeautifulSoup(html, 'lxml')

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if '.mp4' in href.lower():
            found.add(clean_url(urljoin(page_url, href)))

    for tag in soup.find_all(['video', 'source']):
        for attr in ['src', 'data-src', 'data-lazy-src']:
            val = tag.get(attr, '').strip()
            if val and '.mp4' in val.lower():
                found.add(clean_url(urljoin(page_url, val)))

    for tag in soup.find_all(True):
        for a in ['href', 'src', 'data-src', 'data-url', 'data-file',
                  'data-video', 'data-mp4', 'content', 'value']:
            val = tag.get(a, '')
            if isinstance(val, str) and '.mp4' in val.lower():
                found.add(clean_url(urljoin(page_url, val)))

    for script in soup.find_all('script'):
        txt = script.string or ''
        if not txt or '.mp4' not in txt.lower():
            continue
        for m in _SCRIPT_MP4_PATTERN.findall(txt):
            found.add(clean_url(m.replace('\\/', '/')))
        for m in _SCRIPT_ESC_PATTERN.findall(txt):
            found.add(clean_url(m.replace('\\/', '/')))

    found.discard('')
    return list(found)


def _fetch_iframe(args):
    """Fetch a single iframe URL and return its mp4s. Used in thread pool."""
    iurl, page_url, depth = args
    s = get_scraper()
    time.sleep(0.3)
    ih = fetch(s, iurl, ref=page_url, tries=2)
    if not ih:
        return []
    results = find_mp4(ih, iurl)
    # One level of nested iframes only
    if depth < 2:
        nested = _collect_iframes(ih, iurl, depth + 1)
        with ThreadPoolExecutor(max_workers=IFRAME_WORKERS) as ex:
            for r in ex.map(_fetch_iframe, nested):
                results.extend(r)
    return results


def _collect_iframes(html, page_url, depth):
    """Return list of (iurl, page_url, depth) tuples for iframes in html."""
    soup = BeautifulSoup(html, 'lxml')
    tasks = []
    skip = ['google', 'facebook', 'twitter', 'doubleclick', 'adsense', 'disqus']
    for iframe in soup.find_all('iframe'):
        src = (iframe.get('src') or iframe.get('data-src') or iframe.get('data-lazy-src') or '').strip()
        if not src or src.startswith(('about:', 'javascript:')):
            continue
        iurl = urljoin(page_url, src)
        if any(s in iurl.lower() for s in skip):
            continue
        if '.mp4' in iurl.lower():
            # Direct mp4 link in src — handle inline
            tasks.append((iurl, page_url, depth))
            continue
        tasks.append((iurl, page_url, depth))
    return tasks


def find_iframe_mp4(html, page_url):
    """Parallel iframe mp4 discovery (replaces recursive sequential version)."""
    tasks = _collect_iframes(html, page_url, 0)
    if not tasks:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=IFRAME_WORKERS) as ex:
        for r in ex.map(_fetch_iframe, tasks):
            results.extend(r)
    return results


def page_title(html, fallback=''):
    soup = BeautifulSoup(html, 'lxml')
    for sel in ['.entry-title', '.post-title', 'h1.title', 'h1', 'h2.title']:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) >= 3:
                return clean_title(t)
    og = soup.find('meta', property='og:title')
    if og:
        t = og.get('content', '').strip()
        if t and len(t) >= 3:
            return clean_title(t)
    tt = soup.find('title')
    if tt:
        t = tt.get_text(strip=True)
        if t and len(t) >= 3:
            return clean_title(t)
    return fallback


def page_thumb(html, page_url, fallback=''):
    soup = BeautifulSoup(html, 'lxml')
    og = soup.find('meta', property='og:image')
    if og:
        v = og.get('content', '').strip()
        if v:
            return urljoin(page_url, v)
    tw = soup.find('meta', attrs={'name': 'twitter:image'})
    if tw:
        v = tw.get('content', '').strip()
        if v:
            return urljoin(page_url, v)
    vid = soup.find('video')
    if vid and vid.get('poster'):
        return urljoin(page_url, vid['poster'].strip())
    for sel in ['.entry-content', '.post-content', '.content', 'article', 'main']:
        c = soup.select_one(sel)
        if c:
            for img in c.find_all('img'):
                src = get_img_src(img, page_url)
                if src and not is_tiny(src):
                    return src
    return fallback


def pick_best(urls):
    if not urls:
        return None
    scored = []
    for url in urls:
        sc = 0
        ul = url.lower()
        if 'mmsbee' in ul: sc += 100
        if '/uploads/' in ul: sc += 50
        if '/myfiless/' in ul: sc += 50
        if re.search(r'/\d+\.mp4', ul): sc += 30
        if 'thumb' in ul or 'preview' in ul or 'sample' in ul: sc -= 100
        scored.append((sc, url))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def clean_url(url):
    if not url:
        return ''
    url = url.replace('\\/', '/').replace('\\u0026', '&').replace('&amp;', '&')
    url = url.strip().strip('"').strip("'").strip()
    url = re.sub(r'[\s"\'<>\\}\]\);,]+$', '', url)
    if url.startswith('//'):
        url = 'https:' + url
    if url.startswith('http://') or url.startswith('https://'):
        return url
    return ''


# ==================== SAVE ====================

def save_playlist(entries):
    with open(PLAYLIST_JSON, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    m3u = ['#EXTM3U', '']
    for e in entries:
        th = e.get('thumbnail', '')
        m3u.append(f'#EXTINF:-1 tvg-logo="{th}",{e["title"]}')
        m3u.append(f'#EXTVLCOPT:http-referrer={e["page_url"]}')
        m3u.append(e['stream_url'])
        m3u.append('')
    with open(PLAYLIST_M3U, 'w', encoding='utf-8') as f:
        f.write('\n'.join(m3u))

    log.info(f"💾 Saved {len(entries)} videos")


# ==================== PROCESS ONE VIDEO PAGE ====================

def process_item(item, page_url, existing_urls, lock):
    """Fetch one video page and return an entry dict or None. Thread-safe."""
    s = get_scraper()
    time.sleep(DELAY)

    ih = fetch(s, item['page_url'], ref=page_url)
    if not ih:
        log.warning(f"  ❌ Fetch failed: {item['title'][:40]}")
        return None

    pt = page_title(ih, fallback=item['title'])
    pth = page_thumb(ih, item['page_url'], fallback=item.get('thumbnail', ''))

    mp4s = find_mp4(ih, item['page_url'])
    mp4s.extend(find_iframe_mp4(ih, item['page_url']))
    mp4s = list(set(filter(None, mp4s)))

    if not mp4s:
        log.warning(f"  ❌ No MP4: {pt[:45]}")
        return None

    best = pick_best(mp4s)
    if not best:
        return None

    with lock:
        if best in existing_urls:
            log.info(f"  ⏭ Duplicate: {pt[:45]}")
            return None
        existing_urls.add(best)

    log.info(f"  ✅ {pt[:50]}")
    log.info(f"     {best[:70]}")
    return {
        'title': pt,
        'stream_url': best,
        'page_url': item['page_url'],
        'thumbnail': pth
    }


# ==================== SCRAPE ====================

def scrape_pages(scraper, start_page, num_pages, existing_urls):
    new_entries = []
    current = start_page
    end = start_page + num_pages - 1
    reached_end = False
    empty_streak = 0
    lock = threading.Lock()

    while current <= end and current <= MAX_PAGE_LIMIT:
        page_url = f"{BASE_URL}/page/{current}/" if current > 1 else BASE_URL + "/"

        log.info(f"\n{'='*50}")
        log.info(f"📄 PAGE {current} (scraping {start_page}→{end})")
        log.info(f"{'='*50}")

        html = fetch(scraper, page_url)
        if not html or not is_page_valid(html):
            log.info(f"🛑 Page {current} invalid/404 — END OF SITE")
            reached_end = True
            break

        listings = get_listings(html, page_url)
        log.info(f"Found {len(listings)} content links")

        if not listings:
            empty_streak += 1
            log.info(f"Empty page (streak: {empty_streak})")
            if empty_streak >= 3:
                log.info("🛑 3 empty pages — END")
                reached_end = True
                break
            current += 1
            time.sleep(DELAY)
            continue
        else:
            empty_streak = 0

        # ── Parallel fetch of all video pages on this listing page ──
        page_entries = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {
                ex.submit(process_item, item, page_url, existing_urls, lock): item
                for item in listings
            }
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    page_entries.append(result)

        new_entries.extend(page_entries)
        log.info(f"\n  Page {current}: +{len(page_entries)} videos (batch total: {len(new_entries)})")

        if not has_next_page(html, current):
            log.info(f"🛑 No next page after {current} — END OF SITE")
            reached_end = True
            break

        current += 1
        time.sleep(DELAY)

    last_done = current if reached_end else current - 1
    return new_entries, last_done, reached_end


# ==================== MAIN ====================

def main():
    parser = argparse.ArgumentParser()
    global MAX_WORKERS
    parser.add_argument('--mode', choices=['batch', 'refresh', 'reset'], default='batch')
    parser.add_argument('--start-page', type=int, default=None,
                        help='Force start from this page number')
    parser.add_argument('--workers', type=int, default=MAX_WORKERS,
                        help=f'Parallel workers per listing page (default: {MAX_WORKERS})')
    args = parser.parse_args()

    MAX_WORKERS = args.workers

    mode = args.mode
    state = load_state()

    log.info(f"{'='*60}")
    log.info(f"MODE: {mode.upper()} | WORKERS: {MAX_WORKERS}")
    log.info(f"State: last_page={state['last_page']}, completed={state['completed']}, videos={state['total_videos']}")
    log.info(f"{'='*60}")

    if mode == 'reset':
        log.info("🔄 FULL RESET")
        state = {'last_page': 0, 'completed': False, 'total_videos': 0,
                 'last_run': '', 'run_count': 0, 'last_mode': 'reset'}
        existing = []
    else:
        existing = load_existing()

    log.info(f"Existing playlist: {len(existing)} videos")
    existing_urls = set(e['stream_url'] for e in existing if e.get('stream_url'))

    scraper = make_scraper()
    new_entries = []
    last_page = state.get('last_page', 0)
    reached_end = state.get('completed', False)

    if mode == 'batch' or mode == 'reset':
        if reached_end and mode != 'reset':
            log.info("✅ Already completed all pages → switching to refresh")
            mode = 'refresh'
        else:
            if args.start_page is not None:
                start = args.start_page
                log.info(f"📌 Forced start page: {start}")
            else:
                start = last_page + 1

            log.info(f"📦 BATCH: pages {start} → {start + BATCH_PAGES - 1}")
            new_entries, last_page, reached_end = scrape_pages(
                scraper, start, BATCH_PAGES, existing_urls
            )

    if mode == 'refresh':
        log.info(f"🔄 REFRESH: checking first {REFRESH_PAGES} pages")
        new_entries, _, _ = scrape_pages(
            scraper, 1, REFRESH_PAGES, existing_urls
        )

    if mode == 'refresh':
        all_entries = new_entries + existing
    else:
        all_entries = existing + new_entries

    all_entries = dedup(all_entries)

    log.info(f"\n{'='*60}")
    log.info(f"📊 RESULTS")
    log.info(f"  Mode:        {mode}")
    log.info(f"  New:         {len(new_entries)}")
    log.info(f"  Total:       {len(all_entries)}")
    log.info(f"  Last page:   {last_page}")
    log.info(f"  Site done:   {reached_end}")
    log.info(f"{'='*60}")

    save_playlist(all_entries)

    if mode != 'refresh':
        state['last_page'] = last_page
        state['completed'] = reached_end
    state['total_videos'] = len(all_entries)
    state['last_mode'] = mode
    save_state(state)

    log.info(f"\n📋 PLAYLIST ({len(all_entries)}):")
    for i, e in enumerate(all_entries[:30]):
        log.info(f"  {i+1}. {e['title'][:50]} → {e['stream_url'][:60]}")
    if len(all_entries) > 30:
        log.info(f"  ... +{len(all_entries)-30} more")


if __name__ == '__main__':
    main()
