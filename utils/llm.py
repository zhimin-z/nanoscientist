"""OpenRouter LLM integration using OpenAI SDK format"""
import os
import yaml
from openai import OpenAI


def get_openrouter_client():
    """Initialize OpenRouter client"""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key
    )


def call_llm(prompt, model=None, max_tokens=4096, temperature=0.7):
    """
    Call OpenRouter API with given prompt

    Args:
        prompt: The prompt to send
        model: Model to use (defaults to OPENROUTER_MODEL env var)
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature

    Returns:
        str: The model's response text
    """
    client = get_openrouter_client()
    model = model or os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature
    )

    return response.choices[0].message.content


def parse_yaml(text):
    """
    Extract and parse YAML from LLM response

    Handles responses with ```yaml code blocks or raw YAML

    Args:
        text: LLM response containing YAML

    Returns:
        dict: Parsed YAML content
    """
    # Try to extract YAML from code block
    if "```yaml" in text:
        yaml_str = text.split("```yaml")[1].split("```")[0].strip()
    elif "```" in text:
        yaml_str = text.split("```")[1].split("```")[0].strip()
    else:
        yaml_str = text.strip()

    return yaml.safe_load(yaml_str)
