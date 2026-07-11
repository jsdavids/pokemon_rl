"""Phase 5: local-LLM orchestration over the MCP tools.

Assistant runs the chat loop against any OpenAI-compatible endpoint
(Ollama by default; llama.cpp/vLLM/LM Studio are config strings).
routing holds the fixture set + eval that turns model choice into a
measured bake-off instead of a vibe.
"""

from pokechamp.llm.assistant import (
    Assistant,
    OllamaCompleter,
    ROUTING_PROMPT,
    tool_schemas,
)
from pokechamp.llm.routing import FIXTURES, evaluate_routing

__all__ = [
    "Assistant", "FIXTURES", "OllamaCompleter", "ROUTING_PROMPT",
    "evaluate_routing", "tool_schemas",
]
