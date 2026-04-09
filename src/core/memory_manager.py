"""
memory_manager.py

Handles within-session context management for the CalendarAgent:
  - Token estimation
  - Tool result pruning (prevents large JSON payloads from bloating context)
  - Recursive summarization (compresses old turns when nearing the token limit)

No persistence between sessions — this is ephemeral, in-memory only.
"""

import os
import logging
import ollama
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("core.memory_manager")

# Default thresholds
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
COMPRESSION_THRESHOLD = int(OLLAMA_NUM_CTX * 0.8)   # 80% of context window
MIN_RECENT_MESSAGES = 10        # raw messages always preserved at the tail
TOOL_RESULT_CHAR_LIMIT = 12000  # characters before a tool result is pruned (~3k tokens)


def estimate_tokens(text) -> int:
    """Rough estimate: 1 token ≈ 4 characters."""
    if not text:
        return 0
    return len(str(text)) // 4


class MemoryManager:
    """
    Manages the active message list for a single CalendarAgent session.

    Responsibilities:
      - Maintain the ordered list of messages.
      - Track estimated token usage.
      - Prune oversized tool results before they are committed.
      - Trigger recursive summarization when the token budget is exceeded.
    """

    def __init__(
        self,
        model: str,
        compression_threshold: int = COMPRESSION_THRESHOLD,
        min_recent: int = MIN_RECENT_MESSAGES,
        tool_result_char_limit: int = TOOL_RESULT_CHAR_LIMIT,
    ):
        self.model = model
        self.compression_threshold = compression_threshold
        self.min_recent = min_recent
        self.tool_result_char_limit = tool_result_char_limit
        self.messages: list[dict] = []
        self.compression_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, message: dict):
        """Add a message to the active context. Automatically prunes tool results."""
        if message.get("role") == "tool":
            message = self._maybe_prune_tool_result(message)
        if "tokens" not in message:
            message["tokens"] = estimate_tokens(message.get("content", ""))
        self.messages.append(message)

    def get_total_tokens(self) -> int:
        return sum(m.get("tokens", 0) for m in self.messages)

    def needs_compression(self) -> bool:
        return self.get_total_tokens() >= self.compression_threshold

    def reset(self, system_message: dict):
        """Clear all messages and seed with the system prompt."""
        if "tokens" not in system_message:
            system_message["tokens"] = estimate_tokens(system_message.get("content", ""))
        self.messages = [system_message]
        self.compression_count = 0

    # ------------------------------------------------------------------
    # Tool Result Pruning
    # ------------------------------------------------------------------

    def _maybe_prune_tool_result(self, message: dict) -> dict:
        """
        If a tool result is longer than the character limit, replace its content
        with a truncated preview and a note. This prevents calendar API dumps
        from consuming thousands of tokens.
        """
        content = message.get("content", "")
        tool_name = message.get("name", "unknown")
        
        # dynamic limits based on tool
        research_limit = OLLAMA_NUM_CTX * 2 # Allow more for research/scrapes if ctx is large
        char_limit = research_limit if tool_name in ["research_agent", "scrape_url"] else self.tool_result_char_limit
        
        if len(content) > char_limit:
            preview = content[: char_limit]
            pruned_note = (
                f"[Tool result from '{tool_name}' was truncated — "
                f"{len(content)} chars → {char_limit} shown]\n\n"
                f"{preview}\n...[truncated]"
            )
            logger.info(
                f"Pruned tool result from '{tool_name}': "
                f"{len(content)} → {len(pruned_note)} chars"
            )
            message = dict(message)  # don't mutate the original
            message["content"] = pruned_note
        return message

    async def generate_brief(self):
        """
        Extracts a very brief summary of the user's implicit constraints, goals, 
        and key entities from the current active memory. Useful to pass to sub-agents.
        """
        non_system = [m for m in self.messages if m["role"] != "system" or m.get("is_memory")]
        if not non_system:
            yield {"type": "brief_result", "content": "No prior context."}
            return
            
        transcript_parts = []
        for m in non_system[-10:]: # just look at the recent tail
            role = m["role"].upper()
            content = m.get("content", "")
            if len(content) > 1000:
                content = content[:1000] + "..."
            transcript_parts.append(f"{role}: {content}")
            
        transcript = "\n".join(transcript_parts)
        
        brief_messages = [
            {
                "role": "system",
                "content": (
                    "You are a context extractor. Look at the recent conversation transcript. "
                    "Extract ONLY the user's active goals, constraints (like dates, timezone, preferences), "
                    "and any key entities being discussed. output as a short 2-3 sentence context brief. no pleasantries."
                )
            },
            {
                "role": "user",
                "content": f"Transcript:\n{transcript}"
            }
        ]
        
        try:
            yield {"type": "debug_event", "category": "briefing", "content": f"Extracting Context Brief for sub-agent..."}
            yield {"type": "debug_event", "category": "briefing", "content": f"--- Internal Prompt ---\n{transcript}\n-----------------------"}
                
            client = ollama.AsyncClient()
            response = await client.chat(
                model=self.model, 
                messages=brief_messages, 
                stream=True,
                options={"num_ctx": OLLAMA_NUM_CTX}
            )
            
            brief = ""
            async for chunk in response:
                if hasattr(chunk, "model_dump"):
                    chunk = chunk.model_dump()
                content_chunk = chunk.get("message", {}).get("content", "")
                brief += content_chunk
                if content_chunk:
                    yield {"type": "debug_stream", "category": "briefing", "content": content_chunk}
                    
            yield {"type": "debug_event", "category": "briefing", "content": f"\n[Briefing Complete]\n"}
                
            yield {"type": "brief_result", "content": brief.strip() if brief.strip() else "No immediate context extracted."}
        except Exception as e:
            logger.error(f"Failed to generate brief: {e}")
            yield {"type": "debug_event", "category": "error", "content": f"Failed to generate brief: {e}"}
            yield {"type": "brief_result", "content": "Failed to generate context brief."}

    # ------------------------------------------------------------------
    # Recursive Summarization
    # ------------------------------------------------------------------

    async def compress_history(self):
        """
        Summarize the oldest portion of the conversation to reclaim context space.

        Layout after compression:
          [0]  system prompt (always anchored)
          [1]  memory summary block (compressed from old turns)
          [-N] min_recent raw messages (anchored at tail)
        """
        system_msgs = [
            m for m in self.messages if m["role"] == "system" and not m.get("is_memory")
        ]
        non_system = [
            m for m in self.messages if m["role"] != "system" or m.get("is_memory")
        ]

        if len(non_system) <= self.min_recent:
            logger.info("Not enough non-system messages to compress, skipping.")
            return

        to_summarize = non_system[: -self.min_recent]
        to_keep_raw = non_system[-self.min_recent :]

        if not to_summarize:
            return

        logger.info(f"Compressing {len(to_summarize)} messages into memory summary...")

        # Build a plain-text transcript for the summarizer LLM
        transcript_parts = []
        for m in to_summarize:
            role = m["role"].upper()
            content = m.get("content", "")
            if m.get("is_memory"):
                transcript_parts.append(f"[PRIOR MEMORY SUMMARY]\n{content}")
            elif role == "TOOL":
                transcript_parts.append(f"TOOL ({m.get('name', 'unknown')}): {content}")
            else:
                transcript_parts.append(f"{role}: {content}")
        transcript = "\n\n".join(transcript_parts)

        summarizer_messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert context compressor. You will be given a transcript of a conversation. "
                    "Your job is to produce a dense, factual summary of the core facts, constraints, "
                    "user preferences, and state of the conversation.\n"
                    "Omit pleasantries. Retain specific dates, names, or actionable details.\n"
                    "If the conversation includes previous compressed memory, integrate it into the new summary.\n"
                    "Use bullet points under clear headings where helpful."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Summarize this conversation history into a concise memory block:\n\n"
                    f"{transcript}"
                ),
            },
        ]
        
        yield {"type": "debug_event", "category": "compression", "content": f"--- Internal Prompt ---\n{transcript}\n-----------------------"}

        try:
            client = ollama.AsyncClient()
            response = await client.chat(
                model=self.model,
                messages=summarizer_messages,
                stream=True,
                options={"num_ctx": OLLAMA_NUM_CTX}
            )
            
            summary_text = ""
            async for chunk in response:
                if hasattr(chunk, "model_dump"):
                    chunk = chunk.model_dump()
                content_chunk = chunk.get("message", {}).get("content", "")
                summary_text += content_chunk
                if content_chunk:
                    yield {"type": "debug_stream", "category": "compression", "content": content_chunk}

            summary_text = summary_text.strip()
            
            yield {"type": "debug_event", "category": "compression", "content": "\n[Compression Complete]\n"}

            if not summary_text:
                logger.warning("Compression produced an empty summary, skipping replacement.")
                return

            summary_msg = {
                "role": "system",
                "content": (
                    f"[Conversation Memory — compressed after {len(to_summarize)} messages]\n\n"
                    f"{summary_text}"
                ),
                "tokens": estimate_tokens(summary_text),
                "is_memory": True,
            }

            # Rebuild: original system prompts + new summary + raw recent tail
            self.messages = system_msgs + [summary_msg] + to_keep_raw
            self.compression_count += 1

            logger.info(
                f"Compression #{self.compression_count} complete. "
                f"Context now ~{self.get_total_tokens()} estimated tokens."
            )

        except Exception as e:
            logger.error(f"Memory compression failed: {e}. Continuing without compressing.")
