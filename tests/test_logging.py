import asyncio
import sys
import os
import logging

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from integrations.web_search import scrape_url

async def test_scrape():
    # Setup logging to console
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    url = "https://example.com"
    query = "What is this website about?"
    
    def debug_cb(event):
        print(f"DEBUG EVENT: {event}")
        
    print(f"Starting test scrape for {url}")
    result = await scrape_url(url, query=query, debug_callback=debug_cb)
    print("\nRESULT:")
    print(result[:500] + "...")

if __name__ == "__main__":
    asyncio.run(test_scrape())
