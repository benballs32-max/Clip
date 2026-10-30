"""One place to call Claude for structured output via a tool."""
from types import SimpleNamespace

import anthropic


def call_tool(client: "anthropic.Anthropic", model: str, tool: dict, prompt: str, max_tokens: int, images=None):
    """Return (tool_input_dict, usage). Some models reject a forced tool_choice, so fall back to auto,
    and if the model answers in prose instead of calling the tool, ask once or twice more."""
    kw = dict(model=model, max_tokens=max_tokens, tools=[tool])
    text = f"{prompt}\n\nAnswer by calling the `{tool['name']}` tool."
    content = text
    if images:  # [(label, jpeg_bytes)], each preceded by its label so the model knows what it is looking at
        import base64
        content = []
        for label, data in images:
            content += [{"type": "text", "text": label},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(data).decode()}}]
        content.append({"type": "text", "text": text})
    msgs = [{"role": "user", "content": content}]
    used = SimpleNamespace(input_tokens=0, output_tokens=0)

    def ask(choice):
        r = client.messages.create(**kw, messages=msgs, tool_choice=choice)
        used.input_tokens += r.usage.input_tokens
        used.output_tokens += r.usage.output_tokens
        return r

    try:
        resp = ask({"type": "tool", "name": tool["name"]})
    except anthropic.BadRequestError as e:
        if "tool_choice" not in str(e):
            raise
        resp = ask({"type": "auto"})
    for _ in range(3):
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if block:
            return block.input, used
        msgs += [{"role": "assistant", "content": resp.content or [{"type": "text", "text": "(no answer)"}]},
                 {"role": "user", "content": f"Please give your answer now by calling the `{tool['name']}` tool."}]
        resp = ask({"type": "auto"})
    return {}, used
