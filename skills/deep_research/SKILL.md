---
name: Deep Research
description: Spawns a dedicated sub-agent to perform multi-step, multi-source research on complex topics. Use when the user asks to "research" something, needs a comprehensive report, or the question requires synthesizing information from multiple sources.
---

# Deep Research Skill

When the user asks for thorough investigation of a topic, use the `deep_research` tool to spawn a specialized sub-agent.

## When to Use

- The user explicitly asks to "research" something
- The question requires synthesizing information across multiple sources
- A simple `search_web` call would be insufficient
- The user wants a comprehensive report or deep-dive

## How It Works

The `deep_research` tool spawns a sub-agent that:
1. Autonomously searches the web across multiple queries
2. Scrapes and reads specific pages in depth
3. Cross-references and synthesizes findings
4. Produces a final, structured report

## Usage

Call the `deep_research` tool with a clear, specific query:
- Be as specific as possible in the `query` argument
- The sub-agent will return a comprehensive written report
- Present that report directly to the user without heavy paraphrasing
