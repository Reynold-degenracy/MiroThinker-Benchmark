# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

"""
Multimodal message builder for constructing messages with images, audio, and video content.
Supports building OpenAI-compatible multimodal message formats.
"""

import base64
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Supported file extensions and their MIME types
IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Audio extensions map to format strings (not MIME types) because OpenAI's
# input_audio API expects format strings like "mp3", not full MIME types.
# This is consistent with AUDIO_FORMATS in python_mcp_server.py
AUDIO_EXTENSIONS = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "m4a",
}

VIDEO_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}

# File size threshold for direct base64 encoding (20MB)
MAX_DIRECT_ENCODING_SIZE = 20 * 1024 * 1024


@dataclass
class MultimodalContent:
    """
    Represents multimodal content that can be passed to an LLM.
    
    Attributes:
        text_content: Optional text content
        images: List of image data (base64 encoded with mime type)
        audio: List of audio data (base64 encoded with format)
        video: List of video data (base64 encoded with mime type)
    """
    text_content: Optional[str] = None
    images: List[Dict[str, str]] = field(default_factory=list)
    audio: List[Dict[str, str]] = field(default_factory=list)
    video: List[Dict[str, str]] = field(default_factory=list)
    
    def has_multimodal_content(self) -> bool:
        """Check if this content has any multimodal elements."""
        return bool(self.images or self.audio or self.video)
    
    def is_empty(self) -> bool:
        """Check if content is empty."""
        return not self.text_content and not self.has_multimodal_content()


def get_mime_type_for_image(file_path: str) -> Optional[str]:
    """Get MIME type for an image file."""
    ext = os.path.splitext(file_path)[1].lower()
    return IMAGE_EXTENSIONS.get(ext)


def get_audio_format(file_path: str) -> Optional[str]:
    """Get audio format for an audio file."""
    ext = os.path.splitext(file_path)[1].lower()
    return AUDIO_EXTENSIONS.get(ext)


def get_mime_type_for_video(file_path: str) -> Optional[str]:
    """Get MIME type for a video file."""
    ext = os.path.splitext(file_path)[1].lower()
    return VIDEO_EXTENSIONS.get(ext)


def is_image_file(file_path: str) -> bool:
    """Check if file is a supported image format."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in IMAGE_EXTENSIONS


def is_audio_file(file_path: str) -> bool:
    """Check if file is a supported audio format."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in AUDIO_EXTENSIONS


def is_video_file(file_path: str) -> bool:
    """Check if file is a supported video format."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext in VIDEO_EXTENSIONS


def is_multimodal_file(file_path: str) -> bool:
    """Check if file is a supported multimodal format (image, audio, or video)."""
    return is_image_file(file_path) or is_audio_file(file_path) or is_video_file(file_path)


def should_use_direct_encoding(file_path: str) -> bool:
    """
    Determine if a file should be directly base64 encoded and sent to LLM.
    
    Returns True for small multimodal files (< 20MB).
    Returns False for large files (should use caption/transcription instead).
    """
    if not is_multimodal_file(file_path):
        return False
    
    try:
        file_size = os.path.getsize(file_path)
        return file_size < MAX_DIRECT_ENCODING_SIZE
    except (OSError, IOError):
        return False


def read_file_as_base64(file_path: str) -> Optional[str]:
    """
    Read a file and return its base64 encoded content.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Base64 encoded string, or None if file cannot be read
    """
    try:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except (OSError, IOError) as e:
        logger.error(f"Failed to read file {file_path}: {e}")
        return None


def build_image_content(file_path: str, base64_data: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Build image content block for multimodal message.
    
    Args:
        file_path: Path to the image file
        base64_data: Optional pre-encoded base64 data
        
    Returns:
        Image content dict in OpenAI format, or None if failed
    """
    mime_type = get_mime_type_for_image(file_path)
    if not mime_type:
        logger.warning(f"Unsupported image format: {file_path}")
        return None
    
    if base64_data is None:
        base64_data = read_file_as_base64(file_path)
        if base64_data is None:
            return None
    
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{base64_data}"
        }
    }


def build_audio_content(file_path: str, base64_data: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Build audio content block for multimodal message.
    
    Args:
        file_path: Path to the audio file
        base64_data: Optional pre-encoded base64 data
        
    Returns:
        Audio content dict in OpenAI format, or None if failed
    """
    audio_format = get_audio_format(file_path)
    if not audio_format:
        logger.warning(f"Unsupported audio format: {file_path}")
        return None
    
    if base64_data is None:
        base64_data = read_file_as_base64(file_path)
        if base64_data is None:
            return None
    
    return {
        "type": "input_audio",
        "input_audio": {
            "data": base64_data,
            "format": audio_format
        }
    }


def build_video_content(file_path: str, base64_data: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Build video content block for multimodal message.
    
    Args:
        file_path: Path to the video file
        base64_data: Optional pre-encoded base64 data
        
    Returns:
        Video content dict in OpenAI format (using image_url type), or None if failed
    """
    mime_type = get_mime_type_for_video(file_path)
    if not mime_type:
        logger.warning(f"Unsupported video format: {file_path}")
        return None
    
    if base64_data is None:
        base64_data = read_file_as_base64(file_path)
        if base64_data is None:
            return None
    
    # Videos are sent using image_url type with video MIME type
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{base64_data}"
        }
    }


def build_multimodal_content_from_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Build appropriate multimodal content block based on file type.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Content dict in appropriate format, or None if unsupported/failed
    """
    if is_image_file(file_path):
        return build_image_content(file_path)
    elif is_audio_file(file_path):
        return build_audio_content(file_path)
    elif is_video_file(file_path):
        return build_video_content(file_path)
    else:
        logger.warning(f"Unsupported multimodal file type: {file_path}")
        return None


def build_multimodal_message(
    text: Optional[str] = None,
    multimodal_contents: Optional[List[Dict[str, Any]]] = None
) -> Union[str, List[Dict[str, Any]]]:
    """
    Build a message content that can contain text and/or multimodal elements.
    
    Args:
        text: Optional text content
        multimodal_contents: Optional list of multimodal content blocks
        
    Returns:
        If only text: returns the text string
        If multimodal: returns a list of content blocks
    """
    if not multimodal_contents:
        return text or ""
    
    content = []
    
    # Add text first if present
    if text:
        content.append({"type": "text", "text": text})
    
    # Add multimodal content
    content.extend(multimodal_contents)
    
    return content


def build_multimodal_user_message(
    text: Optional[str] = None,
    multimodal_contents: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Build a complete user message with multimodal content.
    
    Args:
        text: Optional text content
        multimodal_contents: Optional list of multimodal content blocks
        
    Returns:
        Complete message dict with role="user"
    """
    content = build_multimodal_message(text, multimodal_contents)
    return {
        "role": "user",
        "content": content
    }


class MultimodalMessageBuilder:
    """
    Helper class for building messages with multimodal content.
    Provides a fluent interface for adding content.
    """
    
    def __init__(self):
        self._text: Optional[str] = None
        self._contents: List[Dict[str, Any]] = []
    
    def add_text(self, text: str) -> "MultimodalMessageBuilder":
        """Add or append text content."""
        if self._text:
            self._text += "\n" + text
        else:
            self._text = text
        return self
    
    def add_image(self, file_path: str) -> "MultimodalMessageBuilder":
        """Add image from file path."""
        content = build_image_content(file_path)
        if content:
            self._contents.append(content)
        return self
    
    def add_image_base64(self, base64_data: str, mime_type: str = "image/jpeg") -> "MultimodalMessageBuilder":
        """Add image from base64 data."""
        self._contents.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_data}"
            }
        })
        return self
    
    def add_audio(self, file_path: str) -> "MultimodalMessageBuilder":
        """Add audio from file path."""
        content = build_audio_content(file_path)
        if content:
            self._contents.append(content)
        return self
    
    def add_audio_base64(self, base64_data: str, audio_format: str = "mp3") -> "MultimodalMessageBuilder":
        """Add audio from base64 data."""
        self._contents.append({
            "type": "input_audio",
            "input_audio": {
                "data": base64_data,
                "format": audio_format
            }
        })
        return self
    
    def add_video(self, file_path: str) -> "MultimodalMessageBuilder":
        """Add video from file path."""
        content = build_video_content(file_path)
        if content:
            self._contents.append(content)
        return self
    
    def add_video_base64(self, base64_data: str, mime_type: str = "video/mp4") -> "MultimodalMessageBuilder":
        """Add video from base64 data."""
        self._contents.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_data}"
            }
        })
        return self
    
    def add_file(self, file_path: str) -> "MultimodalMessageBuilder":
        """Add multimodal content from file, auto-detecting type."""
        content = build_multimodal_content_from_file(file_path)
        if content:
            self._contents.append(content)
        return self
    
    def build(self) -> Union[str, List[Dict[str, Any]]]:
        """Build the message content."""
        return build_multimodal_message(self._text, self._contents if self._contents else None)
    
    def build_message(self, role: str = "user") -> Dict[str, Any]:
        """Build a complete message dict."""
        return {
            "role": role,
            "content": self.build()
        }
    
    def has_multimodal_content(self) -> bool:
        """Check if builder has any multimodal content."""
        return len(self._contents) > 0
