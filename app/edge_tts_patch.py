import edge_tts.communicate
from edge_tts.communicate import mkssml as original_mkssml
from edge_tts.communicate import Communicate as OriginalCommunicate
from edge_tts.data_classes import TTSConfig
from edge_tts.communicate import escape, remove_incompatible_characters, split_text_by_byte_length

# 1. Patch mkssml to handle raw_ssml flag
def patched_mkssml(tc: TTSConfig, escaped_text: str) -> str:
    if getattr(tc, 'raw_ssml', False):
        return escaped_text
    return original_mkssml(tc, escaped_text)

edge_tts.communicate.mkssml = patched_mkssml

# 2. Patch Communicate.__init__ to accept raw_ssml
def patched_init(
    self,
    text: str,
    voice: str = "en-US-AriaNeural",
    *,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    boundary: str = "SentenceBoundary",
    connector = None,
    proxy: str = None,
    connect_timeout: int = 10,
    receive_timeout: int = 60,
    raw_ssml: bool = False  # New argument
):
    # Call original init with dummy text to set up structure
    # We can't call original init easily because it processes text immediately.
    # So we have to copy-paste logic or be clever.
    
    # Let's replicate the init logic but handle raw_ssml
    self.tts_config = TTSConfig(voice, rate, volume, pitch, boundary)
    self.tts_config.raw_ssml = raw_ssml  # Store flag in config

    if not isinstance(text, str):
        raise TypeError("text must be str")

    if raw_ssml:
        # Don't escape, don't remove chars, don't split (assume user handles it)
        # We wrap it in a list as expected by stream()
        self.texts = [text]
    else:
        # Original logic
        self.texts = split_text_by_byte_length(
            escape(remove_incompatible_characters(text)),
            4096,
        )

    if proxy is not None and not isinstance(proxy, str):
        raise TypeError("proxy must be str")
    self.proxy = proxy

    if not isinstance(connect_timeout, int):
        raise TypeError("connect_timeout must be int")
    if not isinstance(receive_timeout, int):
        raise TypeError("receive_timeout must be int")
        
    import aiohttp
    self.session_timeout = aiohttp.ClientTimeout(
        total=None,
        connect=None,
        sock_connect=connect_timeout,
        sock_read=receive_timeout,
    )

    if connector is not None and not isinstance(connector, aiohttp.BaseConnector):
        raise TypeError("connector must be aiohttp.BaseConnector")
    self.connector = connector

    self.state = {
        "partial_text": b"",
        "offset_compensation": 0,
        "last_duration_offset": 0,
        "stream_was_called": False,
    }

# Apply patch
edge_tts.communicate.Communicate.__init__ = patched_init
edge_tts.Communicate = edge_tts.communicate.Communicate # Ensure export is updated if needed

def apply_patch():
    """Explicitly apply patch (idempotent)"""
    edge_tts.communicate.mkssml = patched_mkssml
    edge_tts.communicate.Communicate.__init__ = patched_init
