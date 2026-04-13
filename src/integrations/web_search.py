import os
import requests
import logging
import time
import uuid
import asyncio
from core.prompt_loader import load_prompt
from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger('integrations.web_search')

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))

import urllib.parse
import re
import ollama

async def search_web(query, max_results=5):
    """
    Searches the web using a local SearXNG instance.
    Returns a string summary of the top results.
    """
    logger.info(f"Searching web for: {query}")
    
    try:
        # Construct the URL for the SearXNG JSON API
        url = f"{SEARXNG_URL}/search"
        params = {
            "q": query,
            "format": "json",
            "engines": "google,bing,duckduckgo,brave"
        }
        
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        results = data.get("results", [])
        
        if not results:
            return "No results found for that query."
            
        formatted_results = []
        for i, res in enumerate(results[:max_results]):
            title = res.get("title", "No Title")
            link = res.get("url", "No URL")
            snippet = res.get("content", "No snippet available.")
            snippet = snippet.replace("\n", " ").strip()
            formatted_results.append(f"{i+1}. {title}\n   URL: {link}\n   Snippet: {snippet}")
            
        return "\n\n".join(formatted_results)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error connecting to SearXNG: {e}")
        return f"Error: Failed to connect to SearXNG at {SEARXNG_URL}. Make sure it is running and accessible."
    except Exception as e:
        logger.error(f"Unexpected error during search: {e}")
        return f"Error: An unexpected error occurred during the search: {str(e)}"

async def summarize_scrape(md_content, query, debug_callback=None, trace_id=""):
    """
    Cleans raw markdown and uses Ollama to extract
    highly relevant information related to the specific query.
    """
    logger.info(f"[{trace_id}] Starting summarization for query: '{query}'")
    start_time = time.time()
    
    if debug_callback:
        debug_callback({"type": "debug_event", "category": "scraping", "content": f"Summarizing content for: '{query}'..."})
        
    clean_md = re.sub(r'!\[.*?\]\(.*?\)', '', md_content)
    max_chars = OLLAMA_NUM_CTX * 3
    if len(clean_md) > max_chars:
        logger.info(f"[{trace_id}] Truncating markdown from {len(clean_md)} to {max_chars} characters.")
        clean_md = clean_md[:max_chars]
        
    system_prompt = load_prompt("summarize_scrape.md")

    
    messages = [
        {
            "role": "system",
            "content": system_prompt
        },


        {
            "role": "user",
            "content": f"Query: {query}\n\nRaw Scrape Data:\n{clean_md}"
        }
    ]
    
    try:
        client = ollama.AsyncClient()
        logger.info(f"[{trace_id}] Requesting Ollama stream (Model: {OLLAMA_MODEL})")
        
        response = await client.chat(
            model=OLLAMA_MODEL, 
            messages=messages, 
            stream=True,
            options={"num_ctx": OLLAMA_NUM_CTX}
        )
        
        summary = ""
        chunk_count = 0
        last_log_time = time.time()
        
        async for chunk in response:
            if hasattr(chunk, "model_dump"):
                chunk = chunk.model_dump()
            content_chunk = chunk.get("message", {}).get("content", "")
            summary += content_chunk
            chunk_count += 1
            
            # Log progress every 50 chunks or 5 seconds
            current_time = time.time()
            if chunk_count % 50 == 0 or (current_time - last_log_time) > 5.0:
                logger.info(f"[{trace_id}] Summarization progress: {len(summary)} chars generated ({chunk_count} chunks)...")
                last_log_time = current_time
            
            if debug_callback and content_chunk:
                debug_callback({"type": "debug_stream", "category": "scraping", "content": content_chunk})
                
        duration = time.time() - start_time
        logger.info(f"[{trace_id}] Summarization complete. Duration: {duration:.2f}s, Total Length: {len(summary)} chars.")
        
        if debug_callback:
            debug_callback({"type": "debug_event", "category": "scraping", "content": f"\n[Summarization Complete in {duration:.1f}s]\n"})
            
        return summary
    except Exception as e:
        logger.error(f"[{trace_id}] Error in summarize_scrape: {e}")
        if debug_callback:
            debug_callback({"type": "debug_event", "category": "error", "content": f"Failed to summarize: {str(e)}"})
        return md_content[:2000] + "... [Failed to summarize, truncated]"

async def scrape_url(url, query=None, debug_callback=None):
    """
    Scrapes a URL using Firecrawl and returns a clean, relevant summary.
    If query is provided, it extracts info only relevant to the query.
    """
    trace_id = f"sc_{uuid.uuid4().hex[:6]}"
    logger.info(f"[{trace_id}] Initiating scrape for: {url}")
    start_time = time.time()
    
    if debug_callback:
        debug_callback({"type": "debug_event", "category": "scraping", "content": f"Scraping URL: {url}..."})
    
    try:
        scrape_endpoint = f"{FIRECRAWL_URL}/v1/scrape"
        headers = {"Content-Type": "application/json"}
        if FIRECRAWL_API_KEY:
            headers["Authorization"] = f"Bearer {FIRECRAWL_API_KEY}"
            
        payload = {"url": url, "formats": ["markdown"]}
        
        logger.info(f"[{trace_id}] Sending POST request to Firecrawl...")
        response = await asyncio.to_thread(requests.post, scrape_endpoint, json=payload, headers=headers, timeout=60)
        
        scrape_duration = time.time() - start_time
        logger.info(f"[{trace_id}] Firecrawl response received. Status: {response.status_code}, Duration: {scrape_duration:.2f}s")
        
        response.raise_for_status()
        
        data = response.json()
        if not data.get("success"):
            error_msg = data.get('error', 'Unknown error')
            logger.error(f"[{trace_id}] Firecrawl failed: {error_msg}")
            return f"Error: Firecrawl failed to scrape {url}. Reason: {error_msg}"
            
        md_content = data.get("data", {}).get("markdown", "")
        if not md_content:
            logger.warning(f"[{trace_id}] Firecrawl returned success but empty markdown.")
            return f"No readable content extracted from {url}"
            
        logger.info(f"[{trace_id}] Extracted {len(md_content)} chars of markdown.")
        
        if query:
            summary = await summarize_scrape(md_content, query, debug_callback=debug_callback, trace_id=trace_id)
            return f"--- SCRAPED & SUMMARIZED CONTENT FROM {url} ---\n{summary}\n--- END SUMMARY ---"
        else:
            CHAR_LIMIT = OLLAMA_NUM_CTX * 2 
            if len(md_content) > CHAR_LIMIT:
                md_content = md_content[:CHAR_LIMIT] + f"... [Content Truncated at {CHAR_LIMIT} chars]"
            return f"--- SCRAPED CONTENT FROM {url} ---\n{md_content}\n--- END SCRAPED CONTENT ---"
        
    except requests.exceptions.Timeout:
        logger.error(f"[{trace_id}] Timeout connecting to Firecrawl after {time.time() - start_time:.1f}s")
        return f"Error: Firecrawl timed out while scraping {url}. The site might be too slow or blocking requests."
    except requests.exceptions.RequestException as e:
        logger.error(f"[{trace_id}] Connection error to Firecrawl: {e}")
        return f"Error: Failed to connect to Firecrawl. Make sure the service is running at {FIRECRAWL_URL}."
    except Exception as e:
        logger.error(f"[{trace_id}] Unexpected error during scrape: {e}")
        return f"Error: An unexpected error occurred: {str(e)}"
