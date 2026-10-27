"""The module that talks to a model.

An OpenAI-compatible API client and nothing else. No model is loaded here, no
weights live in this project, and no server is started, watched or stopped: the
endpoint is yours to run, and this only needs to reach it. Point ``LLM_BASE_URL``
at anything that speaks the OpenAI wire format and give it ``LLM_API_KEY`` and
``LLM_MODEL``.

``complete_structured`` is the only place a request leaves the project, so the
endpoint quirks stay here rather than reaching the prompt code. Every value comes
from ``config``; see .env.example.
"""

import json
import types
from typing import Union, get_args, get_origin

from openai import AsyncOpenAI, BadRequestError, UnprocessableEntityError
from pydantic import BaseModel

from job_seeker_ai import config
from job_seeker_ai.exceptions import LLMConfigError

# The SDK raises on an empty key rather than accepting one, so an incomplete
# configuration falls back to a port nobody listens on here. Building the client
# must not be what reports the problem: ``check_config`` is what names the
# variable to set, and it runs before any request is attempted.
_UNCONFIGURED_URL = "http://127.0.0.1:9/v1"
_UNCONFIGURED_KEY = "unset"

aclient = AsyncOpenAI(
    base_url=config.LLM_BASE_URL or _UNCONFIGURED_URL,
    api_key=config.LLM_API_KEY or _UNCONFIGURED_KEY,
)

# Rejections that mean "this endpoint will not take this response_format", as
# opposed to an auth or connection failure that retrying would only repeat.
_UNSUPPORTED_SCHEMA = (BadRequestError, UnprocessableEntityError)


def check_config():
    """Fail early, naming the variable to set, when the client cannot work.

    Called before a pass starts, so a forgotten setting surfaces as one clear
    message instead of a 401 part-way through jobs that have already been billed.
    """
    missing = [
        name
        for name, value in (
            ("LLM_BASE_URL", config.LLM_BASE_URL),
            ("LLM_API_KEY", config.LLM_API_KEY),
            ("LLM_MODEL", config.LLM_MODEL),
        )
        if not value
    ]
    if missing:
        raise LLMConfigError(
            f"{', '.join(missing)} must be set: this step is an OpenAI-compatible "
            f"API client with no default endpoint - see .env.example"
        )


async def complete_structured(schema, messages, *, temperature: float, max_tokens: int):
    """Send one chat request and return the reply parsed into ``schema``.

    The only place a model request leaves this project, so the endpoint
    differences stay here instead of reaching the prompt code:

    * the reply is normally already parsed, because ``response_format`` carries
      the Pydantic schema as a strict ``json_schema``;
    * an endpoint that rejects that schema is retried once in ``json_object``
      mode, which is much weaker - the flag only says "reply with json" and
      nothing about the fields - so that attempt also carries a worked example of
      the shape. The reply is then validated here.

    Failures propagate unchanged; the caller knows whether they are fatal.
    """
    request = {
        "model": config.LLM_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        response = await aclient.chat.completions.parse(
            **request,
            messages=messages,
            response_format=schema,
        )
    except _UNSUPPORTED_SCHEMA:
        response = await aclient.chat.completions.create(
            **request,
            # A fresh list: the caller's messages are theirs, and this nudge
            # belongs to the weaker path alone.
            messages=[*messages, {"role": "user", "content": _json_object_instruction(schema)}],
            response_format={"type": "json_object"},
        )

    return _parse_reply(response.choices[0].message, schema)


def _json_object_instruction(schema) -> str:
    """The prompt a ``json_object`` endpoint needs, which ``json_schema`` does not.

    ``response_format={"type": "json_object"}`` is far weaker than a schema: it
    says "reply with json" and nothing about the fields. DeepSeek's documentation
    is explicit that the caller makes up the difference - put the word json in the
    prompt and show an example of the desired format - and the same holds on other
    hosts. The example is derived from ``schema`` so it cannot drift from the model
    it describes.
    """
    example = json.dumps(_json_example(schema), ensure_ascii=False)
    return f"Return the desired output json in exactly this shape:\n{example}\n\nNo prose and no markdown fences."


def _json_example(annotation):
    """A skeleton value for ``annotation``, in the shape a reply should have.

    Placeholders, not content: the example is there to show the structure, and
    plausible-looking strings would only invite the model to copy them.
    """
    origin = get_origin(annotation)
    arguments = get_args(annotation)

    if origin is Union or origin is types.UnionType:
        # Optional[X] and friends: show the interesting side, never None.
        options = [argument for argument in arguments if argument is not type(None)]
        return _json_example(options[0]) if options else None
    if origin in (list, set, frozenset, tuple):
        return [_json_example(arguments[0])] if arguments else []
    if origin is dict:
        return {}

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {name: _json_example(field.annotation) for name, field in annotation.model_fields.items()}

    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    return "..."


def _parse_reply(message, schema):
    """Turn one reply into ``schema``, whichever shape the endpoint returned.

    Three cases, in order of preference: the SDK already parsed it; the plain-JSON
    fallback left the object in ``content``; and a reasoning model can leave it in
    ``reasoning_content`` while ``content`` stays empty.
    """
    parsed = getattr(message, "parsed", None)
    if parsed is not None:
        return parsed

    for field_name in ("content", "reasoning_content"):
        raw = getattr(message, field_name, None)
        if raw:
            return schema.model_validate(json.loads(raw))

    raise ValueError(f"the model returned no usable object (refusal: {getattr(message, 'refusal', None)!r})")
