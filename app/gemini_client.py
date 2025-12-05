import os
from google import genai
from google.genai import types

# Store configuration globally
_api_key: str | None = None
_proxy: str | None = None

def init_client(api_key: str, http_proxy: str | None = None):
    """Initialize the Gemini configuration."""
    global _api_key, _proxy
    
    _api_key = api_key
    _proxy = http_proxy
    
    # Configure proxy environment variables if provided
    if http_proxy:
        os.environ["HTTP_PROXY"] = http_proxy
        os.environ["HTTPS_PROXY"] = http_proxy
        # Also set for aiohttp/httpx specifically if needed, but env vars are usually enough
        # for libraries that support standard proxy env vars.

def create_client() -> genai.Client | None:
    """Create a new Gemini client instance using stored config."""
    if not _api_key:
        return None
        
    # Create a fresh client. This ensures any internal session/loop binding 
    # happens in the current context (e.g. inside TtsWorker's loop).
    return genai.Client(api_key=_api_key)

def reset_client():
    """Reset the Gemini configuration and environment."""
    global _api_key, _proxy
    
    _api_key = None
    _proxy = None
    
    # Clean up environment variables
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
