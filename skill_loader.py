"""
skill_loader.py - Standalone utilities for discovering and loading Skills.
Kept separate to avoid circular imports between agent.py and tools.py.
"""
import os
import re

def _get_skills_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills')

def _parse_skill_frontmatter(content):
    """Parses YAML frontmatter from a SKILL.md file. Returns (name, description)."""
    match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return None, None
    frontmatter = match.group(1)
    name, description = None, None
    for line in frontmatter.splitlines():
        if line.startswith('name:'):
            name = line.split(':', 1)[1].strip()
        elif line.startswith('description:'):
            description = line.split(':', 1)[1].strip()
    return name, description

def load_skill_summaries():
    """Returns a list of (name, description) tuples from all SKILL.md frontmatters."""
    skills_dir = _get_skills_dir()
    summaries = []
    if not os.path.exists(skills_dir):
        return summaries
    for root, dirs, files in os.walk(skills_dir):
        for file in files:
            if file == "SKILL.md":
                with open(os.path.join(root, file), 'r') as f:
                    content = f.read()
                name, description = _parse_skill_frontmatter(content)
                if name:
                    summaries.append((name, description))
    return summaries

def get_skill_content(skill_name):
    """Reads and returns the full content of a named skill's SKILL.md."""
    skills_dir = _get_skills_dir()
    if not os.path.exists(skills_dir):
        return f"Skill '{skill_name}' not found."
    for root, dirs, files in os.walk(skills_dir):
        if "SKILL.md" in files:
            with open(os.path.join(root, "SKILL.md"), 'r') as f:
                content = f.read()
            name, _ = _parse_skill_frontmatter(content)
            if name and name.lower() == skill_name.lower():
                return content
    return f"Skill '{skill_name}' not found."
