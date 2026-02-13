"""Strips chain-of-thought <think> blocks from LLM output.

Safety net for when a thinking model (e.g., qwen-3-32b) is accidentally selected.
These models emit <think>...</think> reasoning blocks before the actual response,
which pollute the cleaned transcription output.

Pipeline position:
    LLMSwitcher → ThinkingStripper → LLMAssistantAggregator
"""

from __future__ import annotations

import re
from typing import Any

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from utils.logger import logger

# Matches <think>...</think> blocks including content, greedy across newlines
_THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# Matches an incomplete opening <think> tag at the end of accumulated text
# (the closing </think> hasn't arrived yet)
_INCOMPLETE_THINK_PATTERN = re.compile(r"<think>(?:(?!</think>).)*$", re.DOTALL)


class ThinkingStripper(FrameProcessor):
    """Strips <think>...</think> blocks from streaming LLM responses.

    Buffers LLMTextFrame chunks during a response, strips any thinking blocks
    from the accumulated text, and re-emits the cleaned text at response end.
    No-op for non-thinking models (simple passthrough when no <think> detected).
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the thinking stripper."""
        super().__init__(**kwargs)
        self._is_accumulating: bool = False
        self._accumulated_text: str = ""
        self._has_thinking: bool = False
        # Buffer non-LLMTextFrame frames received during accumulation to preserve ordering
        self._buffered_frames: list[tuple[Frame, FrameDirection]] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process frames, buffering LLM text to strip thinking blocks."""
        await super().process_frame(frame, direction)

        match frame:
            case LLMFullResponseStartFrame():
                self._accumulated_text = ""
                self._has_thinking = False
                self._is_accumulating = True
                self._buffered_frames = []
                await self.push_frame(frame, direction)

            case LLMTextFrame() as text_frame if self._is_accumulating:
                self._accumulated_text += text_frame.text
                # Detect thinking blocks early to avoid buffering when not needed
                if "<think>" in self._accumulated_text:
                    self._has_thinking = True

                if not self._has_thinking:
                    # No thinking detected — pass through immediately for low latency
                    await self.push_frame(frame, direction)

            case LLMFullResponseEndFrame():
                self._is_accumulating = False

                if self._has_thinking:
                    # Strip complete <think>...</think> blocks
                    cleaned = _THINK_BLOCK_PATTERN.sub("", self._accumulated_text)
                    # Also strip any incomplete <think> block (missing closing tag)
                    cleaned = _INCOMPLETE_THINK_PATTERN.sub("", cleaned)
                    cleaned = cleaned.strip()

                    stripped_length = len(self._accumulated_text) - len(cleaned)
                    logger.warning(
                        f"Thinking model output detected and stripped "
                        f"({stripped_length} chars removed) — consider switching "
                        f"to a non-CoT model for lower latency"
                    )

                    # Re-emit cleaned text as a single frame
                    if cleaned:
                        await self.push_frame(LLMTextFrame(text=cleaned), direction)

                    # Flush any buffered non-text frames
                    for buffered_frame, buffered_direction in self._buffered_frames:
                        await self.push_frame(buffered_frame, buffered_direction)
                    self._buffered_frames = []

                self._accumulated_text = ""
                await self.push_frame(frame, direction)

            case _ if self._is_accumulating and self._has_thinking:
                # Buffer non-text frames while we're accumulating thinking output
                # so they're emitted after the cleaned text
                self._buffered_frames.append((frame, direction))

            case _:
                await self.push_frame(frame, direction)
