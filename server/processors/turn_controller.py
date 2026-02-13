"""Turn controller for dictation recording lifecycle.

Controls turn boundaries and coordinates timing between:
- STT services (via VADUserStoppedSpeakingFrame upstream for manual stop)
- LLMUserAggregator (via UserStartedSpeakingFrame/UserStoppedSpeakingFrame)

Does NOT buffer transcriptions - the LLMUserAggregator handles that.
This processor manages:
- Recording start/stop from RTVI client
- STT finalization signaling
- Draining timeout for late transcriptions
- Empty recording detection

Uses a state machine pattern with tagged unions for explicit state management:
- IdleState: Not recording
- RecordingState: Actively recording, transcriptions pass through
- WaitingForSTTState: Stop received, waiting for STT to catch up
- DrainingState: Speech stopped, draining late transcriptions
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

from protocol.messages import RecordingCompleteMessage
from utils.logger import logger

if TYPE_CHECKING:
    from processors.context_manager import DictationContextManager

# Short timeout for waiting for the VAD speech-stopped signal after stop-recording.
# If VAD doesn't fire within this window (common when audio stream cuts off immediately),
# we transition to DrainingState anyway. Keep this short — it's just a grace period.
DEFAULT_STT_WAIT_TIMEOUT_SECONDS: Final[float] = 0.3

# Adaptive timeout for draining late transcriptions after speech stops.
# Resets each time a new TranscriptionFrame arrives. This is the user-configurable
# timeout exposed via the API (controls how long to wait for slow STT providers).
DEFAULT_DRAINING_TIMEOUT_SECONDS: Final[float] = 0.5

# Minimum word count for a draining-phase transcription to be accepted.
# Very short transcriptions (1 word) arriving during draining are often garbled
# audio artifacts from the mic closing. Longer transcriptions are passed through.
MIN_DRAINING_TRANSCRIPTION_WORDS: Final[int] = 2


# =============================================================================
# State Machine Types
# =============================================================================


@dataclass(frozen=True)
class IdleState:
    """Not recording. Waiting for start-recording message."""

    pass


@dataclass(frozen=True)
class RecordingState:
    """Actively recording. Transcriptions pass through to aggregator."""

    has_content: bool = False


@dataclass(frozen=True)
class WaitingForSTTState:
    """Stop-recording received, waiting for VAD to signal speech has stopped.

    This state is entered when stop-recording arrives. We wait for
    VADUserStoppedSpeakingFrame to ensure all pending STT transcriptions
    have been delivered before signaling turn end.
    """

    has_content: bool
    direction: FrameDirection


@dataclass(frozen=True)
class DrainingState:
    """Speech stopped, draining any remaining transcriptions from STT.

    This state is entered after VADUserStoppedSpeakingFrame is received while
    in WaitingForSTTState. We wait briefly for late-arriving transcriptions
    before signaling turn end. Uses an adaptive timeout that resets when
    transcriptions arrive.
    """

    has_content: bool
    direction: FrameDirection


# Tagged union of all possible states
State = IdleState | RecordingState | WaitingForSTTState | DrainingState


# =============================================================================
# Turn Controller
# =============================================================================


class TurnController(FrameProcessor):
    """Controls turn boundaries for dictation recording.

    Uses a state machine to manage the recording lifecycle explicitly.
    State transitions are handled via pattern matching, making invalid
    states unrepresentable.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the turn controller."""
        super().__init__(**kwargs)
        self._state: State = IdleState()
        self._timeout_task: asyncio.Task[None] | None = None
        self._draining_task: asyncio.Task[None] | None = None
        self._draining_event: asyncio.Event = asyncio.Event()
        # Short timeout for waiting for VAD speech-stopped signal (internal, not user-facing)
        self._stt_wait_timeout = DEFAULT_STT_WAIT_TIMEOUT_SECONDS
        # Adaptive timeout for draining late transcriptions (user-configurable via API)
        self._draining_timeout = DEFAULT_DRAINING_TIMEOUT_SECONDS
        # Timestamp of last transcription received during recording (for garbled detection)
        self._last_transcription_time: float = 0.0
        # Context manager for reset coordination (set from main.py)
        self._context_manager: DictationContextManager | None = None

    def set_context_manager(self, context_manager: DictationContextManager) -> None:
        """Set the context manager for context reset coordination.

        Args:
            context_manager: The DictationContextManager to use for context resets.
        """
        self._context_manager = context_manager

    def set_transcription_timeout(self, seconds: float) -> None:
        """Set the draining timeout for late transcriptions.

        This controls how long to wait for late-arriving STT transcriptions
        after speech stops. The adaptive drain resets this timer each time a
        new transcription arrives.

        Args:
            seconds: Timeout in seconds to wait for late transcriptions.
                     Increase for slower STT providers.
        """
        self._draining_timeout = seconds
        logger.info(f"Draining timeout set to {seconds}s")

    def get_transcription_timeout(self) -> float:
        """Get the current draining timeout for late transcriptions."""
        return self._draining_timeout

    async def cleanup(self) -> None:
        """Clean up processor resources including internal tasks.

        Called by pipecat when the pipeline is being shut down.
        Cancels any pending timeout or draining tasks.
        """
        self._cancel_timeout()
        self._cancel_draining()
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process frames using state machine pattern.

        Transcriptions are passed through to the downstream LLMGateFilter during
        recording states. This processor only tracks whether content arrived for
        empty detection. The LLMGateFilter handles the actual LLM bypass logic.
        """
        await super().process_frame(frame, direction)

        match frame:
            case VADUserStoppedSpeakingFrame():
                await self._handle_speech_stopped(direction)
                await self.push_frame(frame, direction)

            case TranscriptionFrame(text=text) if text:
                accepted = await self._handle_transcription(frame, direction)
                # Pass accepted transcriptions through during recording states
                # LLMGateFilter will decide whether to gate them for the aggregator
                if accepted:
                    match self._state:
                        case RecordingState() | WaitingForSTTState() | DrainingState():
                            await self.push_frame(frame, direction)

            case _:
                # Pass through all other frames unchanged
                await self.push_frame(frame, direction)

    # =========================================================================
    # Public API for RTVI Event Handler
    # =========================================================================

    async def start_recording(self) -> None:
        """Start recording - called from RTVI on_client_message handler."""
        await self._handle_start_recording()

    async def stop_recording(self, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        """Stop recording - called from RTVI on_client_message handler."""
        await self._handle_stop_recording(direction)

    # =========================================================================
    # State Transition Handlers
    # =========================================================================

    async def _handle_start_recording(self) -> None:
        """Transition to RecordingState from any state."""
        # Cancel any pending tasks from previous states
        self._cancel_timeout()
        self._cancel_draining()

        # Reset context for new recording (no conversation history for dictation)
        if self._context_manager:
            self._context_manager.reset_context_for_new_recording()

        logger.info("Start-recording received, entering RecordingState")
        self._state = RecordingState()

        # Signal user turn start to downstream processors
        # LLMGateFilter will decide whether to pass this to the aggregator
        await self.push_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    async def _handle_stop_recording(self, direction: FrameDirection) -> None:
        """Handle stop-recording based on current state."""
        match self._state:
            case RecordingState(has_content=has_content):
                logger.info(
                    f"Stop-recording received, waiting for STT to finalize "
                    f"(has_content: {has_content})"
                )
                # Signal STT to finalize any pending transcription
                await self.push_frame(VADUserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
                self._state = WaitingForSTTState(
                    has_content=has_content,
                    direction=direction,
                )
                self._timeout_task = asyncio.create_task(self._stt_timeout_handler(direction))

            case WaitingForSTTState():
                # Already waiting - ignore duplicate stop
                logger.warning("Stop-recording received while already waiting for STT")

            case IdleState():
                # Not recording - send empty response
                logger.warning("Stop-recording received while idle")
                await self._emit_empty_response(direction)

            case DrainingState():
                # Already draining - ignore
                logger.warning("Stop-recording received while draining")

    async def _handle_speech_stopped(self, direction: FrameDirection) -> None:
        """Handle speech stopped from VAD based on current state."""
        match self._state:
            case WaitingForSTTState(has_content=has_content) as state:
                # Speech stopped while waiting - enter draining state to catch
                # late transcriptions that may still be coming from STT
                self._cancel_timeout()
                logger.info(f"Speech stopped, entering draining state (has_content: {has_content})")
                self._state = DrainingState(
                    has_content=has_content,
                    direction=state.direction,
                )
                # Start draining task with adaptive timeout
                self._draining_event.clear()
                self._draining_task = asyncio.create_task(
                    self._draining_task_handler(state.direction)
                )
            case RecordingState():
                # Normal speech stopped during recording - ignore
                # (speech can start/stop multiple times during a recording session)
                pass
            case IdleState():
                pass  # Ignore when idle
            case DrainingState():
                pass  # Already draining, ignore

    async def _handle_transcription(
        self, frame: TranscriptionFrame, direction: FrameDirection
    ) -> bool:
        """Track that content arrived and signal draining if needed.

        Returns:
            True if the transcription was accepted, False if it was rejected
            (e.g., garbled late transcription during draining).
        """
        _ = direction  # Unused, kept for consistency with other handlers

        match self._state:
            case RecordingState():
                self._state = RecordingState(has_content=True)
                self._last_transcription_time = time.monotonic()
                logger.debug(f"Transcription received: '{frame.text}'")
                return True

            case WaitingForSTTState() as state:
                self._state = WaitingForSTTState(
                    has_content=True,
                    direction=state.direction,
                )
                self._last_transcription_time = time.monotonic()
                logger.info(f"Transcription while waiting: '{frame.text}'")
                return True

            case DrainingState() as state:
                # Reject likely garbled transcriptions: very short text arriving
                # during draining is often mic-close artifacts
                word_count = len(frame.text.split())
                if word_count < MIN_DRAINING_TRANSCRIPTION_WORDS:
                    gap_ms = (time.monotonic() - self._last_transcription_time) * 1000
                    logger.warning(
                        f"Rejected likely garbled draining transcription "
                        f"({word_count} words, {gap_ms:.0f}ms gap): '{frame.text}'"
                    )
                    return False

                self._state = DrainingState(
                    has_content=True,
                    direction=state.direction,
                )
                self._last_transcription_time = time.monotonic()
                # Signal draining task to reset timeout
                self._draining_event.set()
                logger.info(f"Late transcription during draining: '{frame.text}'")
                return True

            case IdleState():
                logger.warning(f"Transcription while idle: '{frame.text}'")
                return False

    # =========================================================================
    # Timeout Handler
    # =========================================================================

    async def _stt_timeout_handler(self, direction: FrameDirection) -> None:
        """Background task that transitions to DrainingState after a short VAD grace period.

        The VAD speech-stopped signal often never arrives (e.g., when audio stream
        cuts off immediately on stop-recording). Instead of waiting the full timeout
        and ending the turn, we transition to DrainingState to catch any late
        transcriptions via the adaptive drain.
        """
        try:
            await asyncio.sleep(self._stt_wait_timeout)
            # Only act if still in WaitingForSTT state
            match self._state:
                case WaitingForSTTState(has_content=has_content) as state:
                    logger.info(
                        f"VAD signal not received after {self._stt_wait_timeout}s, "
                        f"entering draining state (has_content: {has_content})"
                    )
                    # Transition to DrainingState instead of ending turn directly.
                    # This ensures late transcriptions are still captured.
                    self._state = DrainingState(
                        has_content=has_content,
                        direction=state.direction,
                    )
                    self._draining_event.clear()
                    self._draining_task = asyncio.create_task(
                        self._draining_task_handler(direction)
                    )
                case _:
                    pass  # State changed, nothing to do
        except asyncio.CancelledError:
            pass  # Cancelled by speech stopped or new recording

    def _cancel_timeout(self) -> None:
        """Cancel any pending timeout task."""
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            self._timeout_task = None

    # =========================================================================
    # Draining Handler
    # =========================================================================

    async def _draining_task_handler(self, direction: FrameDirection) -> None:
        """Wait for late transcriptions with adaptive timeout, then signal turn end.

        Uses an event-based pattern: waits for the draining timeout, but
        resets the timer each time a transcription arrives (signaled via
        _draining_event). Signals turn end when the timeout expires with no
        new transcriptions.

        Uses the user-configurable draining timeout to handle slow STT providers.
        """
        try:
            while True:
                await asyncio.wait_for(
                    self._draining_event.wait(),
                    timeout=self._draining_timeout,
                )
                # Transcription arrived - clear event and wait again
                self._draining_event.clear()
        except TimeoutError:
            # No transcription for draining timeout - signal turn end now
            match self._state:
                case DrainingState(has_content=has_content) as state:
                    if has_content:
                        logger.info("Draining complete, signaling turn end")
                        await self._emit_turn_end(state.direction)
                    else:
                        logger.info("Draining complete with no content, sending empty")
                        await self._emit_empty_response(direction)
                    self._state = IdleState()
                case _:
                    pass  # State changed, nothing to do
        except asyncio.CancelledError:
            pass  # Cancelled by new recording

    def _cancel_draining(self) -> None:
        """Cancel any pending draining task."""
        if self._draining_task and not self._draining_task.done():
            self._draining_task.cancel()
            self._draining_task = None
        self._draining_event.clear()

    # =========================================================================
    # Output Helpers
    # =========================================================================

    async def _emit_turn_end(self, direction: FrameDirection) -> None:
        """Signal end of user turn to downstream processors.

        Emits UserStoppedSpeakingFrame to signal turn end. The LLMGateFilter
        decides whether to pass this to the aggregator or emit raw transcription.
        """
        await self.push_frame(UserStoppedSpeakingFrame(), direction)

    async def _emit_empty_response(self, direction: FrameDirection) -> None:
        """Send an empty response message to the client."""
        frame = RTVIServerMessageFrame(data=RecordingCompleteMessage(hasContent=False).model_dump())
        await self.push_frame(frame, direction)
