"""
LLM service factory for creating configured LLM clients.
"""
import json

from typing import Optional

from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.openai import OpenAIModelSettings
from pydantic_ai.models.fallback import FallbackModel
from openai import OpenAI
from openai.types.chat import ChatCompletion

from codewiki.src.config import Config


def _fix_stringified_json_arrays(args_str: str) -> str:
    """
    Fix tool call arguments where JSON arrays are incorrectly stringified.

    Some models (e.g., glm-5) return arguments like:
        {"view_range": "[1, 100]"}

    This function converts it to:
        {"view_range": [1, 100]}
    """
    if not args_str:
        return args_str

    try:
        args = json.loads(args_str)
        if not isinstance(args, dict):
            return args_str

        modified = False
        for key, value in args.items():
            if isinstance(value, str) and value.strip().startswith('[') and value.strip().endswith(']'):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        args[key] = parsed
                        modified = True
                except (json.JSONDecodeError, ValueError):
                    pass

        if modified:
            return json.dumps(args)
        return args_str
    except json.JSONDecodeError:
        return args_str


class AutoResponseFixOpenAIModel(OpenAIModel):
    """
    OpenAIModel subclass that automatically fixes non-standard API response formatting.

    Some OpenAI-compatible APIs (e.g., glm-5, z.ai) return tool call arguments where
    JSON arrays are incorrectly stringified:
        {"view_range": "[1, 100]"}  # Wrong
    instead of:
        {"view_range": [1, 100]}    # Correct

    This model automatically fixes such responses before validation.
    """

    def _process_response(self, response: ChatCompletion | str):
        """Process response and fix stringified JSON arrays in tool calls."""
        if isinstance(response, ChatCompletion) and response.choices:
            choice = response.choices[0]
            if choice.message.tool_calls:
                for tool_call in choice.message.tool_calls:
                    if hasattr(tool_call, 'function') and tool_call.function:
                        tool_call.function.arguments = _fix_stringified_json_arrays(
                            tool_call.function.arguments
                        )

        return super()._process_response(response)


def create_main_model(config: Config) -> AutoResponseFixOpenAIModel:
    """Create the main LLM model from configuration."""
    return AutoResponseFixOpenAIModel(
        model_name=config.main_model,
        provider=OpenAIProvider(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key
        ),
        settings=OpenAIModelSettings(
            temperature=0.0,
            max_tokens=config.max_tokens
        )
    )


def create_fallback_model(config: Config) -> AutoResponseFixOpenAIModel:
    """Create the fallback LLM model from configuration."""
    return AutoResponseFixOpenAIModel(
        model_name=config.fallback_model,
        provider=OpenAIProvider(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key
        ),
        settings=OpenAIModelSettings(
            temperature=0.0,
            max_tokens=config.max_tokens
        )
    )


def create_fallback_models(config: Config) -> FallbackModel:
    """Create fallback models chain from configuration."""
    main = create_main_model(config)
    fallback = create_fallback_model(config)
    return FallbackModel(main, fallback)


def create_openai_client(config: Config) -> OpenAI:
    """Create OpenAI client from configuration."""
    return OpenAI(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key
    )


def call_llm(
    prompt: str,
    config: Config,
    model: Optional[str] = None,
    temperature: float = 0.0
) -> str:
    """
    Call LLM with the given prompt.

    Args:
        prompt: The prompt to send
        config: Configuration containing LLM settings
        model: Model name (defaults to config.main_model)
        temperature: Temperature setting

    Returns:
        LLM response text

    Raises:
        ValueError: If LLM returns no choices or no content
    """
    if model is None:
        model = config.main_model

    client = create_openai_client(config)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=config.max_tokens
    )

    if not response.choices:
        raise ValueError("LLM returned no choices")

    content = response.choices[0].message.content
    if content is None:
        raise ValueError("LLM returned no content")

    return content