import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('web_search')

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")

import urllib.parse
import re
import ollama

def search_web(query, max_results=5):
    """
    Searches the web using a local SearXNG instance.
    Returns a string summary of the top results.
    """
    logger.info(f"Searching web for: {query}")
    
    try:
        # Construct the URL for the SearXNG JSON API
        # We use format=json to get structured data
        url = f"{SEARXNG_URL}/search"
        params = {
            "q": query,
            "format": "json",
            "engines": "google,bing,duckduckgo,brave" # Choose some common engines
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        results = data.get("results", [])
        
        if not results:
            return "No results found for that query."
            
        # Format the top results into a concise string for the LLM
        formatted_results = []
        for i, res in enumerate(results[:max_results]):
            title = res.get("title", "No Title")
            link = res.get("url", "No URL")
            snippet = res.get("content", "No snippet available.")
            
            # Clean snippet (sometimes contains HTML or extra whitespace)
            snippet = snippet.replace("\n", " ").strip()
            
            formatted_results.append(f"{i+1}. {title}\n   URL: {link}\n   Snippet: {snippet}")
            
        return "\n\n".join(formatted_results)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to SearXNG: {e}")
        return f"Error: Failed to connect to SearXNG at {SEARXNG_URL}. Make sure it is running and accessible."
    except Exception as e:
        logger.error(f"Unexpected error during search: {e}")
        return f"Error: An unexpected error occurred during the search: {str(e)}"

if __name__ == "__main__":
    # Quick test if run directly
    test_query = "Who is the CEO of Google?"
    print(f"Testing search for: {test_query}")
    print("-" * 20)
    print(search_web(test_query))

async def summarize_scrape(md_content, query, debug_callback=None):
    """
    Cleans raw markdown and uses Ollama to extract
    highly relevant information related to the specific query.
    """
    logger.info(f"Summarizing scrape for query: '{query}'")
    if debug_callback:
        debug_callback({"type": "debug_event", "category": "scraping", "content": f"Summarizing large scrape for target query: '{query}'..."})
        
    # Quick cleaning: strip image tags to save tokens
    clean_md = re.sub(r'!\[.*?\]\(.*?\)', '', md_content)
    # Basic truncation to ensure we don't blow up Ollama context
    if len(clean_md) > 24000: # ~6K context size allowance roughly
        clean_md = clean_md[:24000]
        
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert data extractor. The user will provide raw markdown scraped from a website and a target query. "
                "Your objective is to extract ONLY the facts and data relevant to the query. "
                "Strip out all ads, boilerplate, navigation text, and unrelated content. "
                "Output your extraction as a concise, dense report (max 500 words)."
            )
        },
        {
            "role": "user",
            "content": f"Query: {query}\n\nRaw Scrape Data:\n{clean_md}"
        }
    ]
    
    try:
        client = ollama.AsyncClient()
        response = await client.chat(model=OLLAMA_MODEL, messages=messages, stream=True)
        
        summary = ""
        async for chunk in response:
            if hasattr(chunk, "model_dump"):
                chunk = chunk.model_dump()
            content_chunk = chunk.get("message", {}).get("content", "")
            summary += content_chunk
            if debug_callback and content_chunk:
                debug_callback({"type": "debug_stream", "category": "scraping", "content": content_chunk})
                
        if debug_callback:
            debug_callback({"type": "debug_event", "category": "scraping", "content": "\n[Summarization Complete]\n"})
            
        return summary
    except Exception as e:
        logger.error(f"Error in summarize_scrape: {e}")
        if debug_callback:
            debug_callback({"type": "debug_event", "category": "error", "content": f"Failed to summarize: {str(e)}"})
        return md_content[:2000] + "... [Failed to summarize, truncated]"

async def scrape_url(url, query=None, debug_callback=None):
    """
    Scrapes a URL using Firecrawl and returns a clean, relevant summary.
    If query is provided, it extracts info only relevant to the query.
    """
    logger.info(f"Scraping URL with Firecrawl: {url}")
    
    try:
        # Firecrawl /v1/scrape API
        scrape_endpoint = f"{FIRECRAWL_URL}/v1/scrape"
        headers = {"Content-Type": "application/json"}
        if FIRECRAWL_API_KEY:
            headers["Authorization"] = f"Bearer {FIRECRAWL_API_KEY}"
            
        payload = {"url": url, "formats": ["markdown"]}
        
        # NOTE: requests is blocky, ideally we'd use httpx, but for script simplicity we wrap in asyncio or rely on sync requests taking time.
        response = requests.post(scrape_endpoint, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        if not data.get("success"):
            return f"Error: Firecrawl failed to scrape {url}. Reason: {data.get('error', 'Unknown')}"
            
        md_content = data.get("data", {}).get("markdown", "")
        if not md_content:
            return f"No readable content extracted from {url}"
            
        if query:
            summary = await summarize_scrape(md_content, query, debug_callback=debug_callback)
            return f"--- SCRAPED & SUMMARIZED CONTENT FROM {url} ---\n{summary}\n--- END SUMMARY ---"
        else:
            CHAR_LIMIT = 4000
            if len(md_content) > CHAR_LIMIT:
                md_content = md_content[:CHAR_LIMIT] + "... [Content Truncated due to length without specific query]"
            return f"--- SCRAPED CONTENT FROM {url} ---\n{md_content}\n--- END SCRAPED CONTENT ---"
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to Firecrawl: {e}")
        return f"Error: Failed to connect to Firecrawl at {FIRECRAWL_URL}. Make sure it is running."
    except Exception as e:
        logger.error(f"Unexpected error during scrape: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"
