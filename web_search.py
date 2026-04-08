import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('web_search')

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

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
