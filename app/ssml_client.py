"""
Custom Edge TTS client that supports raw SSML.
Based on edge_tts.Communicate but skips text escaping/wrapping.
"""

import asyncio
import json
import ssl
import time
import uuid
from typing import (
    AsyncGenerator,
    Dict,
    Optional,
    Tuple,
    Union,
)

import aiohttp
import certifi
from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL
from edge_tts.drm import DRM
from edge_tts.exceptions import (
    NoAudioReceived,
    UnexpectedResponse,
    UnknownResponse,
    WebSocketError,
)
from edge_tts.typing import TTSChunk


def get_headers_and_data(
    data: bytes, header_length: int
) -> Tuple[Dict[bytes, bytes], bytes]:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    headers = {}
    for line in data[:header_length].split(b"\r\n"):
        key, value = line.split(b":", 1)
        headers[key] = value

    return headers, data[header_length + 2 :]


def connect_id() -> str:
    return str(uuid.uuid4()).replace("-", "")


def date_to_string() -> str:
    return time.strftime(
        "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()
    )


def ssml_headers_plus_data(request_id: str, timestamp: str, ssml: str) -> str:
    return (
        f"X-RequestId:{request_id}\r\n"
        "Content-Type:application/ssml+xml\r\n"
        f"X-Timestamp:{timestamp}Z\r\n"
        "Path:ssml\r\n\r\n"
        f"{ssml}"
    )


class SSMLCommunicate:
    """
    Communicate with the service using raw SSML.
    """

    def __init__(
        self,
        ssml: str,
        *,
        connector: Optional[aiohttp.BaseConnector] = None,
        proxy: Optional[str] = None,
        connect_timeout: int = 10,
        receive_timeout: int = 60,
        output_format: str = "audio-24khz-48kbitrate-mono-mp3",
    ):
        self.ssml = ssml
        self.proxy = proxy
        self.connector = connector
        self.output_format = output_format
        self.session_timeout = aiohttp.ClientTimeout(
            total=None,
            connect=None,
            sock_connect=connect_timeout,
            sock_read=receive_timeout,
        )
        self.state = {
            "stream_was_called": False,
        }

    async def __stream(self) -> AsyncGenerator[TTSChunk, None]:
        async def send_command_request() -> None:
            # We assume simple config for SSML
            await websocket.send_str(
                f"X-Timestamp:{date_to_string()}\r\n"
                "Content-Type:application/json; charset=utf-8\r\n"
                "Path:speech.config\r\n\r\n"
                '{"context":{"synthesis":{"audio":{"metadataoptions":{'
                '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"'
                '},"outputFormat":"' + self.output_format + '"}}}}\r\n'
            )

        async def send_ssml_request() -> None:
            await websocket.send_str(
                ssml_headers_plus_data(
                    connect_id(),
                    date_to_string(),
                    self.ssml,
                )
            )

        audio_was_received = False
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())

        async with aiohttp.ClientSession(
            connector=self.connector,
            trust_env=True,
            timeout=self.session_timeout,
        ) as session, session.ws_connect(
            f"{WSS_URL}&ConnectionId={connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
            compress=15,
            proxy=self.proxy,
            headers=WSS_HEADERS,
            ssl=ssl_ctx,
        ) as websocket:
            await send_command_request()
            await send_ssml_request()

            async for received in websocket:
                if received.type == aiohttp.WSMsgType.TEXT:
                    encoded_data: bytes = received.data.encode("utf-8")
                    parameters, data = get_headers_and_data(
                        encoded_data, encoded_data.find(b"\r\n\r\n")
                    )
                    path = parameters.get(b"Path", None)
                    
                    if path == b"turn.end":
                        break
                    elif path not in (b"response", b"turn.start", b"audio.metadata"):
                        pass # Ignore unknown paths for now

                elif received.type == aiohttp.WSMsgType.BINARY:
                    if len(received.data) < 2:
                        continue
                        
                    header_length = int.from_bytes(received.data[:2], "big")
                    if header_length > len(received.data):
                        continue

                    parameters, data = get_headers_and_data(
                        received.data, header_length
                    )

                    if parameters.get(b"Path") != b"audio":
                        continue

                    content_type = parameters.get(b"Content-Type", None)
                    if content_type is None and len(data) == 0:
                        continue
                        
                    if len(data) > 0:
                        audio_was_received = True
                        yield {"type": "audio", "data": data}

                elif received.type == aiohttp.WSMsgType.ERROR:
                    raise WebSocketError(
                        received.data if received.data else "Unknown error"
                    )

            if not audio_was_received:
                raise NoAudioReceived(
                    "No audio was received. Please verify that your parameters are correct."
                )

    async def stream(self) -> AsyncGenerator[TTSChunk, None]:
        if self.state["stream_was_called"]:
            raise RuntimeError("stream can only be called once.")
        self.state["stream_was_called"] = True

        try:
            async for message in self.__stream():
                yield message
        except aiohttp.ClientResponseError as e:
            if e.status != 403:
                raise
            DRM.handle_client_response_error(e)
            async for message in self.__stream():
                yield message

    async def save(self, audio_fname: Union[str, bytes]) -> None:
        with open(audio_fname, "wb") as audio:
            async for message in self.stream():
                if message["type"] == "audio":
                    audio.write(message["data"])
