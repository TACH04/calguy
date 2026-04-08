"""
test_compression.py
Tests the MemoryManager within-session compression system.

Covers:
  - Compression triggers when token count exceeds the threshold
  - No compression when under the threshold
  - System prompt is always preserved after compression
  - Multiple compression cycles produce a single consolidated memory block
  - Large tool results are pruned before being added to context
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_manager import (
    MemoryManager,
    estimate_tokens,
    COMPRESSION_THRESHOLD,
    MIN_RECENT_MESSAGES,
    TOOL_RESULT_CHAR_LIMIT,
)

MODEL = os.getenv("OLLAMA_MODEL", "qwen3-coder:30b")
SYSTEM_PROMPT = "You are a helpful AI calendar assistant."


def make_manager():
    mm = MemoryManager(model=MODEL)
    mm.reset({"role": "system", "content": SYSTEM_PROMPT})
    return mm


def inject_large_history(mm: MemoryManager, n_messages: int = 30):
    """Inject n_messages large fake user/assistant pairs to bloat the token count."""
    filler = "This is a long fake message used to inflate the token count. " * 20
    for i in range(n_messages):
        mm.append({"role": "user", "content": f"[Turn {i+1}] {filler}"})
        mm.append({"role": "assistant", "content": f"[Response {i+1}] I understand. {filler}"})


# ─── Test 1: compression triggers ────────────────────────────────────────────

async def test_compression_triggers():
    print("=== Test: Compression triggers when over threshold ===")
    mm = make_manager()
    inject_large_history(mm, n_messages=15)

    total_before = mm.get_total_tokens()
    msg_count_before = len(mm.messages)
    print(f"Before compression: {msg_count_before} messages, ~{total_before} tokens")
    assert total_before >= COMPRESSION_THRESHOLD, \
        f"Expected tokens >= {COMPRESSION_THRESHOLD}, got {total_before}"

    await mm.compress_history()

    total_after = mm.get_total_tokens()
    msg_count_after = len(mm.messages)
    print(f"After compression:  {msg_count_after} messages, ~{total_after} tokens")

    assert msg_count_after < msg_count_before, "Expected fewer messages after compression"
    assert total_after < total_before, "Expected fewer tokens after compression"
    assert mm.compression_count == 1, f"Expected compression_count=1, got {mm.compression_count}"

    memory_blocks = [m for m in mm.messages if m.get("is_memory")]
    assert len(memory_blocks) == 1, f"Expected 1 memory block, found {len(memory_blocks)}"
    print(f"\nMemory summary preview:\n---\n{memory_blocks[0]['content'][:500]}\n---\n")

    non_system = [m for m in mm.messages if m["role"] != "system" or m.get("is_memory")]
    raw_recent = [m for m in non_system if not m.get("is_memory")]
    print(f"Raw recent messages preserved: {len(raw_recent)} (expected: {MIN_RECENT_MESSAGES})")
    assert len(raw_recent) == MIN_RECENT_MESSAGES, \
        f"Expected {MIN_RECENT_MESSAGES} raw recent messages, got {len(raw_recent)}"

    print("✅ PASSED\n")


# ─── Test 2: no compression below threshold ───────────────────────────────────

async def test_no_compression_when_under_threshold():
    print("=== Test: No compression when under threshold ===")
    mm = make_manager()
    mm.append({"role": "user", "content": "Hello"})
    mm.append({"role": "assistant", "content": "Hi there!"})

    total_before = mm.get_total_tokens()
    msg_count_before = len(mm.messages)
    assert total_before < COMPRESSION_THRESHOLD

    await mm.compress_history()  # not enough messages → should no-op

    assert mm.compression_count == 0, "Should not have compressed"
    assert len(mm.messages) == msg_count_before, "Message count should not have changed"
    print(f"Token count {total_before} below threshold {COMPRESSION_THRESHOLD} — correctly skipped.")
    print("✅ PASSED\n")


# ─── Test 3: system prompt preserved ─────────────────────────────────────────

async def test_system_prompt_always_preserved():
    print("=== Test: System prompt always preserved after compression ===")
    mm = make_manager()
    inject_large_history(mm, n_messages=15)

    original_system_content = mm.messages[0]["content"]
    await mm.compress_history()

    system_msgs = [m for m in mm.messages if m["role"] == "system" and not m.get("is_memory")]
    assert len(system_msgs) == 1, f"Expected 1 system prompt, found {len(system_msgs)}"
    assert system_msgs[0]["content"] == original_system_content, "System prompt content changed!"
    print("System prompt preserved correctly.")
    print("✅ PASSED\n")


# ─── Test 4: multiple compression cycles ─────────────────────────────────────

async def test_incremental_compression():
    print("=== Test: Multiple compression cycles (incremental) ===")
    mm = make_manager()

    for cycle in range(1, 3):
        inject_large_history(mm, n_messages=15)
        total = mm.get_total_tokens()
        print(f"Cycle {cycle}: before compression, ~{total} tokens, {len(mm.messages)} messages")
        await mm.compress_history()
        total = mm.get_total_tokens()
        print(f"Cycle {cycle}: after  compression, ~{total} tokens, {len(mm.messages)} messages")
        assert mm.compression_count == cycle

    memory_blocks = [m for m in mm.messages if m.get("is_memory")]
    print(f"Memory blocks after {mm.compression_count} compression(s): {len(memory_blocks)}")
    assert len(memory_blocks) == 1, "Should consolidate to 1 memory block"
    print("✅ PASSED\n")


# ─── Test 5: tool result pruning ─────────────────────────────────────────────

async def test_tool_result_pruning():
    print("=== Test: Large tool results are pruned ===")
    mm = make_manager()

    # Create a fake tool result that exceeds the char limit
    big_result = "x" * (TOOL_RESULT_CHAR_LIMIT + 5000)
    mm.append({"role": "tool", "name": "list_events", "content": big_result})

    tool_msgs = [m for m in mm.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    stored_content = tool_msgs[0]["content"]

    assert len(stored_content) < len(big_result), \
        f"Expected pruned content but got {len(stored_content)} chars"
    assert "[truncated]" in stored_content, "Expected truncation note in content"
    print(
        f"Original: {len(big_result)} chars → Stored: {len(stored_content)} chars "
        f"(limit: {TOOL_RESULT_CHAR_LIMIT})"
    )
    print("✅ PASSED\n")


async def test_small_tool_result_not_pruned():
    print("=== Test: Small tool results are NOT pruned ===")
    mm = make_manager()

    small_result = '{"status": "ok", "events": []}'
    mm.append({"role": "tool", "name": "list_events", "content": small_result})

    tool_msgs = [m for m in mm.messages if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == small_result, "Small result should be stored unchanged"
    print("Small tool result stored without modification.")
    print("✅ PASSED\n")


# ─── Runner ───────────────────────────────────────────────────────────────────

async def main():
    # Fast tests first (no LLM call)
    await test_no_compression_when_under_threshold()
    await test_tool_result_pruning()
    await test_small_tool_result_not_pruned()

    # LLM-dependent tests
    await test_compression_triggers()
    await test_system_prompt_always_preserved()
    await test_incremental_compression()

    print("=== All tests passed! ===")


if __name__ == "__main__":
    asyncio.run(main())
