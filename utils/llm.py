"""OpenRouter LLM integration using OpenAI SDK format"""
import os
import json
import re
from openai import OpenAI


def get_openrouter_client():
    """Initialize OpenRouter client"""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers={
            "HTTP-Referer": "https://github.com/mini-researcher-agent",
            "X-Title": "Mini Researcher Agent"
        }
    )


def call_llm(prompt, model=None, max_tokens=4096, temperature=0.7, system_prompt=None):
    """Call OpenRouter API with given prompt and optional system message."""
    client = get_openrouter_client()
    model = model or os.getenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")

    print(f"[API] Calling {model}... ", end="", flush=True)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=120.0
    )

    result = response.choices[0].message.content
    finish_reason = response.choices[0].finish_reason
    if finish_reason == "length":
        print(f"Done (TRUNCATED - hit max_tokens={max_tokens})")
    else:
        print("Done")
    return result


# ---------------------------------------------------------------------------
# JSON parsing (for small control blocks)
# ---------------------------------------------------------------------------

def _clean_json(json_str):
    """Remove trailing commas before } and ] (common LLM mistake)."""
    result = []
    in_str = False
    i = 0
    while i < len(json_str):
        c = json_str[i]
        if c == '\\' and in_str:
            result.append(c)
            if i + 1 < len(json_str):
                result.append(json_str[i + 1])
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        if not in_str and c == ',':
            j = i + 1
            while j < len(json_str) and json_str[j] in ' \t\n\r':
                j += 1
            if j < len(json_str) and json_str[j] in ('}', ']'):
                i += 1
                continue
        result.append(c)
        i += 1
    return ''.join(result)


def _extract_json_object(text, start=0):
    """Extract a JSON object by tracking brace depth (handles ``` in strings)."""
    pos = text.find('{', start)
    if pos == -1:
        return None

    depth = 0
    in_str = False
    i = pos
    while i < len(text):
        c = text[i]
        if c == '\\' and in_str:
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[pos:i + 1]
        i += 1
    # Truncated - return from { to end
    return text[pos:]


def _repair_truncated_json(json_str):
    """Close unclosed structures in truncated JSON."""
    stripped = json_str.rstrip()

    in_string = False
    last_good = 0
    i = 0
    while i < len(stripped):
        c = stripped[i]
        if c == '\\' and in_string:
            i += 2
            continue
        if c == '"':
            in_string = not in_string
        if not in_string:
            last_good = i
        i += 1

    if in_string:
        stripped = stripped[:last_good + 1]

    stripped = stripped.rstrip()
    while stripped and stripped[-1] in (',', ':'):
        stripped = stripped[:-1].rstrip()

    if stripped.endswith('"'):
        pos = len(stripped) - 2
        while pos >= 0:
            if stripped[pos] == '"' and (pos == 0 or stripped[pos - 1] != '\\'):
                break
            pos -= 1
        if pos >= 0:
            before = stripped[:pos].rstrip()
            if before and before[-1] in (',', '{'):
                stripped = before.rstrip(',').rstrip()

    stack = []
    in_str = False
    i = 0
    while i < len(stripped):
        c = stripped[i]
        if c == '\\' and in_str:
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c in ('{', '['):
                stack.append(c)
            elif c == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
            elif c == ']':
                if stack and stack[-1] == '[':
                    stack.pop()
        i += 1

    closers = ""
    for opener in reversed(stack):
        closers += '}' if opener == '{' else ']'
    return stripped + closers


def parse_json(text):
    """Extract and parse JSON from LLM response (brace-depth tracking)."""
    def _try(s):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_clean_json(s))
        except json.JSONDecodeError:
            pass
        return None

    start = 0
    marker = text.find("```json")
    if marker != -1:
        start = marker + len("```json")

    json_str = _extract_json_object(text, start)
    if json_str:
        r = _try(json_str)
        if r is not None:
            return r
        repaired = _repair_truncated_json(json_str)
        if repaired:
            r = _try(repaired)
            if r is not None:
                print("  [PARSE] Repaired JSON successfully")
                return r

    if start > 0:
        json_str = _extract_json_object(text, 0)
        if json_str:
            r = _try(json_str)
            if r is not None:
                return r
            repaired = _repair_truncated_json(json_str)
            if repaired:
                r = _try(repaired)
                if r is not None:
                    return r

    raise ValueError(f"Could not parse JSON. First 300 chars: {text[:300]}")


# ---------------------------------------------------------------------------
# Section-based response format: small JSON control + raw file content
# ---------------------------------------------------------------------------
# Format:
#   ```json
#   {"status": "done", "rounds_used": 1, "reasoning": "..."}
#   ```
#   ===FILE: filename.ext===
#   raw content here (no escaping)
#   ===FILE: another.ext===
#   more content
#   ===END===

FILE_SECTION_RE = re.compile(r'===FILE:\s*(.+?)\s*===\n(.*?)(?=\n===FILE:|\n===END===|$)', re.DOTALL)


def parse_response(text):
    """
    Parse LLM response in section-based format.

    Returns:
        dict with 'control' (parsed JSON) and 'files' (dict of filename->content)
    """
    control = parse_json(text)
    files = {}
    for match in FILE_SECTION_RE.finditer(text):
        filename = match.group(1).strip()
        content = match.group(2).strip()
        files[filename] = content
    return {"control": control, "files": files}


# Backward compat
parse_yaml = parse_json
parse_structured = parse_json
