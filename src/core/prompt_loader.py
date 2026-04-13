import os
import logging

logger = logging.getLogger("core.prompt_loader")

import re

def load_prompt(filename):
    """
    Loads a prompt from the prompts/ directory.
    Strips YAML frontmatter if present and returns the body.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    prompts_dir = os.path.join(base_dir, 'prompts')
    file_path = os.path.join(prompts_dir, filename)
    
    try:
        if not os.path.exists(file_path):
            logger.error(f"Prompt file not found: {file_path}")
            return f"Error: Prompt file '{filename}' missing."
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            
        # Regex to match YAML frontmatter (between --- and ---)
        # We look for --- at the very start, followed by anything, then another ---
        frontmatter_pattern = re.compile(r'^---\s*\n.*?\n---\s*\n', re.DOTALL)
        body = frontmatter_pattern.sub('', content).strip()
        
        return body
    except Exception as e:
        logger.error(f"Error loading prompt {filename}: {e}")
        return f"Error loading prompt '{filename}': {str(e)}"

