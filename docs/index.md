# glia

A small library for building LLM agents. Every model call, tool call and state
change is a plain object that can be logged, saved and replayed. The agent loop
is a single file.

The name comes from glial cells, which support and connect neurons.

## Why

Agent frameworks tend to hide the control flow, which makes them hard to debug.
glia keeps the loop small enough to read. Tools, structured outputs, streaming,
parallel tool execution, context compaction, checkpoints, guardrails, a
human-in-the-loop approval gate, subagents and evaluations written as tests are
optional modules on top of it.

## Install

```bash
pip install glia-agents               # core — no dependencies
pip install "glia-agents[anthropic]"  # + the Claude provider
```

The distribution is `glia-agents`; the import stays `import glia`.

## 30 seconds

```python
import asyncio
from glia import Agent, tool
from glia.providers import ClaudeLLM

@tool
async def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return {"Paris": "18°C, cloudy"}.get(city, "unknown")

async def main():
    agent = Agent(ClaudeLLM(), tools=[get_weather], system="Be concise.")
    result = await agent.run("What's the weather in Paris?")
    print(result.output)   # the answer
    print(result.usage)    # what it cost

asyncio.run(main())
```

No API key? Every example runs offline with the deterministic `EchoLLM`
provider — same code, no network.

## Next

- [Getting started](getting-started.md) — install, first agent, streaming, offline testing
- [Guide](guide.md) — every primitive, with code
- [Architecture](ARCHITECTURE.md) — how the whole thing works
- [Strategy](STRATEGY.md) — market analysis and positioning
