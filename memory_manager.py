"""
memory_manager.py

Handles within-session context management for the CalendarAgent:
  - Token estimation
  - Tool result pruning (prevents large JSON payloads from bloating context)
  - Recursive summarization (compresses old turns when nearing the token limit)

No persistence between sessions — this is ephemeral, in-memory only.
"""

import logging
import ollama

logger = logging.getLogger("memory_manager")

# Default thresholds
COMPRESSION_THRESHOLD = 6000   # ~75% of 8K context window
MIN_RECENT_MESSAGES = 8        # raw messages always preserved at the tail
TOOL_RESULT_CHAR_LIMIT = 2000  # characters before a tool result is pruned


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
        char_limit = 6000 if tool_name in ["research_agent", "scrape_url"] else self.tool_result_char_limit
        
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

    async def generate_brief(self) -> str:
        """
        Extracts a very brief summary of the user's implicit constraints, goals, 
        and key entities from the current active memory. Useful to pass to sub-agents.
        """
        non_system = [m for m in self.messages if m["role"] != "system" or m.get("is_memory")]
        if not non_system:
            return "No prior context."
            
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
            client = ollama.AsyncClient()
            response = await client.chat(model=self.model, messages=brief_messages, stream=False)
            if hasattr(response, "model_dump"):
                response = response.model_dump()
            brief = response.get("message", {}).get("content", "").strip()
            return brief if brief else "No immediate context extracted."
        except Exception as e:
            logger.error(f"Failed to generate brief: {e}")
            return "Failed to generate context brief."

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
                    "You are a concise memory summarizer for an AI calendar assistant. "
                    "Compress the conversation transcript into a short, dense memory block. "
                    "Capture: events created/modified/deleted, what the user asked, "
                    "outstanding tasks, and any key preferences or constraints mentioned. "
                    "Be factual and brief. No pleasantries. "
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

        try:
            client = ollama.AsyncClient()
            response = await client.chat(
                model=self.model,
                messages=summarizer_messages,
                stream=False,
            )
            if hasattr(response, "model_dump"):
                response = response.model_dump()

            summary_text = response.get("message", {}).get("content", "").strip()

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
