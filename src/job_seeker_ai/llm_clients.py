"""LM Studio lifecycle and the OpenAI-compatible client.

The enrichment step runs against a *local* model: no cloud API key exists
anywhere in this project. LM Studio is driven through its ``lms`` CLI, which
must be on PATH, and exposes an OpenAI-compatible endpoint that the official
SDK can talk to.

All three values (endpoint, key, model) come from ``config`` and can be
overridden by environment variables - see .env.example.
"""

import subprocess
import time

from openai import OpenAI

from job_seeker_ai import config

# The dummy key is required by the SDK but ignored by LM Studio.
client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)


def start_agent():
    """Load the model and start the LM Studio API server."""
    try:
        print(f"Loading Model '{config.LLM_MODEL}' ...")
        # Optionally append "--gpu max" to offload to GPU for faster reasoning.
        subprocess.run(["lms", "load", config.LLM_MODEL, "--gpu", "max"], check=True)
        time.sleep(1)

        print("Starting LM API servers...")
        subprocess.run(["lms", "server", "start"], check=True)
        time.sleep(1)

        print("Ready")
    except Exception as e:
        print(f"Error: {e}")
        raise


def stop_agent():
    """Stop the API server and unload the model to free RAM/VRAM."""
    print("\nCleaning up resources...")
    try:
        subprocess.run(["lms", "server", "stop"], check=True)
        time.sleep(1)

        subprocess.run(["lms", "unload", config.LLM_MODEL], check=True)
        time.sleep(1)
        print("Cleanup complete.")
    except Exception as e:
        print(f"Error during cleanup: {e}")
