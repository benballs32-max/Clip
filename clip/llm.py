"""One place to call Claude for structured output via a tool."""
import anthropic


def call_tool(client: "anthropic.Anthropic", model: str, tool: dict, prompt: str, max_tokens: int):
    """Return (tool_input_dict, usage). Some models reject a forced tool_choice, so fall back to auto."""
    base = dict(model=model, max_tokens=max_tokens, tools=[tool],
                messages=[{"role": "user", "content": f"{prompt}\n\nAnswer by calling the `{tool['name']}` tool."}])
    try:
        resp = client.messages.create(**base, tool_choice={"type": "tool", "name": tool["name"]})
    except anthropic.BadRequestError as e:
        if "tool_choice" not in str(e):
            raise
        resp = client.messages.create(**base, tool_choice={"type": "auto"})
    block = next((b for b in resp.content if b.type == "tool_use"), None)
    return (block.input if block else {}), resp.usage
