import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

KNOWN_HOSTS = {
    'FileKeeper': ['filekeeper.net', 'filekeeper'],
    'Pixeldrain': ['pixeldrain.com'],
    'DataNodes': ['datanodes.to', 'datanodes'],
    'FuckingFast': ['fuckingfast.co', 'fuckingfast'],
    'MultiUpload': ['multiupload.net', 'multiupload'],
    'FileRacer': ['fileracer.com', 'fileracer'],
    'MediaFire': ['mediafire.com'],
    'Gofile': ['gofile.io'],
    'Krakenfiles': ['krakenfiles.com'],
    'FastClick': ['fastclick.to']
}

class FitGirlParser:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)

    def parse_url(self, url):
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            return {'success': False, 'error': f"Failed to fetch page: {e}"}

        soup = BeautifulSoup(resp.text, 'html.parser')

        title_el = soup.find('h1', class_='entry-title') or soup.find('h1') or soup.find('title')
        title = title_el.get_text(strip=True) if title_el else "FitGirl Download"
        clean_title = re.sub(r'[\\/*?:"<>|]', '', title).replace('\u2013', '-').strip()

        content = soup.find('div', class_='entry-content') or soup

        all_links = []
        for a in content.find_all('a', href=True):
            href = a['href'].strip()
            text = a.get_text(strip=True)
            if href and not href.startswith('#') and not href.startswith('javascript:'):
                all_links.append({'url': href, 'text': text})

        categorized = self._categorize_links(all_links)

        return {
            'success': True,
            'title': clean_title,
            'raw_title': title,
            'source_url': url,
            'categorized_links': categorized,
            'total_found': sum(len(links) for links in categorized.values())
        }

    def _categorize_links(self, links):
        hosts = {
            'FileKeeper': [],
            'Pixeldrain': [],
            'DataNodes': [],
            'FuckingFast': [],
            'MultiUpload': [],
            'FileRacer': [],
            'MediaFire': [],
            'Gofile': [],
            'Krakenfiles': [],
            'FastClick': [],
            'Pastebin / Pastes': [],
            'Other Hosters': []
        }

        seen_urls = set()
        filekeeper_queue = []

        for link_obj in links:
            url = link_obj['url']
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if any(domain in url for domain in ['fitgirl-repacks.site/category', 'fitgirl-repacks.site/page', 'fitgirl-repacks.site/tag', 'wordpress.org', 'disqus.com', 'twitter.com', 'facebook.com', 'donate']):
                continue

            matched_host = None
            for host_name, domains in KNOWN_HOSTS.items():
                if any(dom in url for dom in domains):
                    matched_host = host_name
                    break

            if matched_host == 'Pixeldrain':
                direct_url = self.resolve_pixeldrain(url)
                filename = self.infer_filename_from_url(direct_url)
                hosts['Pixeldrain'].append({
                    'original': url,
                    'direct': direct_url,
                    'filename': filename,
                    'text': link_obj['text'] or filename
                })
            elif matched_host == 'FileKeeper':
                clean_fn = self.infer_filename_from_url(url)
                if not clean_fn.endswith(('.rar', '.bin', '.zip', '.exe', '.part')):
                    if link_obj['text'] and '.' in link_obj['text']:
                        clean_fn = link_obj['text']
                item = {
                    'original': url,
                    'direct': None,
                    'filename': clean_fn,
                    'text': link_obj['text'] or clean_fn
                }
                filekeeper_queue.append(item)
                hosts['FileKeeper'].append(item)
            elif matched_host:
                filename = self.infer_filename_from_url(url)
                hosts[matched_host].append({
                    'original': url,
                    'direct': url,
                    'filename': filename,
                    'text': link_obj['text'] or filename
                })
            elif any(p in url for p in ['paste.fitgirl-repacks.site', 'filecrypt.cc', 'pastebin.com', 'paste']):
                hosts['Pastebin / Pastes'].append({
                    'original': url,
                    'direct': url,
                    'filename': 'paste_link',
                    'text': link_obj['text'] or 'Paste Link'
                })
            elif re.search(r'\.(bin|rar|zip|7z|exe|iso|doi|001|002)(\?|$)', url, re.I):
                filename = self.infer_filename_from_url(url)
                hosts['Other Hosters'].append({
                    'original': url,
                    'direct': url,
                    'filename': filename,
                    'text': link_obj['text'] or filename
                })

        # Thread-safe robust resolution for FileKeeper direct download links
        if filekeeper_queue:
            def _resolve_item(item):
                direct = self.resolve_filekeeper(item['original'])
                if direct:
                    item['direct'] = direct

            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(_resolve_item, filekeeper_queue))

            # Second pass retry for any missed items
            unresolved = [item for item in filekeeper_queue if not item['direct'] or 'filekeeper.net' in item['direct']]
            if unresolved:
                time.sleep(1)
                with ThreadPoolExecutor(max_workers=3) as executor:
                    list(executor.map(_resolve_item, unresolved))

            # Fallback to original URL only if absolutely necessary
            for item in filekeeper_queue:
                if not item['direct']:
                    item['direct'] = item['original']

        return {k: v for k, v in hosts.items() if v}

    @staticmethod
    def resolve_pixeldrain(url):
        match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url)
        if match:
            file_id = match.group(1)
            return f"https://pixeldrain.com/api/file/{file_id}?download"
        return url

    @staticmethod
    def resolve_filekeeper(url, max_retries=3):
        """
        Thread-safe countdown bypass & direct link generator for FileKeeper.
        Always returns direct dlproxy/tunnel download URL.
        """
        s = requests.Session()
        s.headers.update(HEADERS)
        s.headers['Referer'] = 'https://fitgirl-repacks.site/'

        for attempt in range(max_retries):
            try:
                r = s.get(url, timeout=12)
                if r.status_code in [429, 503]:
                    time.sleep(1.5 * (attempt + 1))
                    continue

                soup = BeautifulSoup(r.text, 'html.parser')
                el = soup.find(id='download-countdown')
                if not el:
                    time.sleep(1)
                    continue

                code = el.get('data-code', '')
                payload = {
                    'op': 'download2',
                    'id': code,
                    'rand': '',
                    'referer': '',
                    'method_free': '',
                    'down_direct': '1'
                }
                resp = s.post("https://filekeeper.net/download", data=payload, allow_redirects=False, timeout=12)
                if resp.status_code == 302 and 'Location' in resp.headers:
                    loc = resp.headers['Location']
                    if 'dlproxy' in loc or 'tunnel' in loc or 'download' in loc:
                        return loc
                time.sleep(1)
            except Exception:
                time.sleep(1)

        return None

    @staticmethod
    def infer_filename_from_url(url):
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        filename = path.split('/')[-1]
        if not filename or len(filename) < 3:
            filename = "fitgirl_part.bin"
        filename = filename.split('?')[0]
        return urllib.parse.unquote(filename)
