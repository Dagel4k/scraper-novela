import os
import time
import random
from tqdm import tqdm
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

class NovelScraper:
    def __init__(self, book_url, output_dir):
        self.book_url = book_url
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def get_chapter_list(self):
        print(f"Fetching chapter list from {self.book_url} using Playwright...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Use a realistic User-Agent
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            })
            
            try:
                page.goto(self.book_url, wait_until="networkidle", timeout=30000)
                # Wait for the catalog to appear
                page.wait_for_selector('div.catalog', timeout=10000)
                html = page.content()
                soup = BeautifulSoup(html, 'lxml')
                
                links = soup.select('div.catalog ul li a')
                if not links:
                    links = soup.select('li > a[href*="/txt/"]')
                
                chapters = []
                for i, link in enumerate(links):
                    title = link.text.strip()
                    url = link.get('href')
                    if url and ('txt' in url or '30966' in url):
                        if url.startswith('/'):
                            url = "https://www.69shuba.com" + url
                        elif not url.startswith('http'):
                            url = self.book_url.rstrip('/') + '/' + url
                        
                        chapters.append({
                            'index': i + 1,
                            'title': title,
                            'url': url
                        })
                
                print(f"Found {len(chapters)} chapters.")
                return chapters
            except Exception as e:
                print(f"Failed to fetch chapter list: {e}")
                return []
            finally:
                browser.close()

    def scrape_chapter(self, page, chapter):
        filename = os.path.join(self.output_dir, f"cn_{chapter['index']:04d}.txt")
        if os.path.exists(filename):
            return True

        try:
            page.goto(chapter['url'], wait_until="domcontentloaded", timeout=20000)
            # Try to click away any overlays if they appear (rare on 69shu but possible)
            
            # Content selector: "div.txtnav"
            content_element = page.query_selector('div.txtnav')
            if not content_element:
                # Retry once after a short wait
                time.sleep(2)
                content_element = page.query_selector('div.txtnav')
            
            if not content_element:
                print(f"Warning: Could not find content for chapter {chapter['index']}")
                return False

            text = content_element.inner_text().strip()
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"Chapter {chapter['index']}: {chapter['title']}\n\n")
                f.write(text)
            
            return True
        except Exception as e:
            print(f"Error scraping chapter {chapter['index']}: {e}")
            return False

    def run(self, start_chapter=1, end_chapter=None):
        chapters = self.get_chapter_list()
        
        if not chapters:
            return

        if end_chapter is None:
            end_chapter = len(chapters)
        
        target_chapters = [c for c in chapters if start_chapter <= c['index'] <= end_chapter]
        
        print(f"Downloading {len(target_chapters)} chapters...")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
            page = context.new_page()
            
            for chapter in tqdm(target_chapters):
                success = self.scrape_chapter(page, chapter)
                if success:
                    # Random delay to look human
                    time.sleep(random.uniform(1.0, 3.0))
                else:
                    # Longer wait on failure
                    time.sleep(5)
            
            browser.close()

if __name__ == "__main__":
    BOOK_ID = "30966"
    BOOK_URL = f"https://www.69shuba.com/book/{BOOK_ID}/"
    OUTPUT_DIR = "data/cn_raws"
    
    scraper = NovelScraper(BOOK_URL, OUTPUT_DIR)
    # Scrape first 50 chapters for testing alignment
    scraper.run(start_chapter=1, end_chapter=50)
