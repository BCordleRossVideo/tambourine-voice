"""Custom logging observer for pipeline events.

Filters frames by source to avoid duplicate logs as frames propagate through the pipeline.
"""

import time

from pipecat.frames.frames import (
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    UserSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.services.llm_service import LLMService
from pipecat.services.stt_service import STTService
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport

from processors.turn_controller import TurnController
from utils.logger import logger


class PipelineLogObserver(BaseObserver):
    """Observer that logs key pipeline events at INFO level.

    Uses source filtering to log each event only once:
    - StartFrame: logged when reaching output transport (end of pipeline)
    - Audio/Speech frames: logged when from input transport (origin)
    - Transcription: logged when from STT service (origin)
    - LLM response: logged when from LLM service (origin)
    - RTVI messages: logged when from output transport (being sent)

    Also tracks per-turn latency breakdown:
    - Turn start → first transcription (STT latency)
    - Turn start → turn stop (turn detection overhead)
    - LLM TTFB and total generation time
    - Full turn duration (start → LLM complete)

    Logs at DEBUG level:
    - Other frames (excluding noisy UserSpeakingFrame and MetricsFrame)
    """

    def __init__(self) -> None:
        """Initialize the observer."""
        super().__init__()
        self._llm_accumulator: str = ""
        self._is_accumulating: bool = False
        self._audio_frame_count: int = 0
        # Track speaking state to deduplicate speech events from multiple sources
        self._is_speaking: bool = False
        # LLM latency tracking
        self._llm_start_time: float = 0.0
        self._llm_first_token_time: float = 0.0
        self._llm_has_first_token: bool = False
        # Per-turn lifecycle timing (from TurnController frames)
        self._turn_start_time: float = 0.0
        self._turn_stop_time: float = 0.0
        self._first_stt_time: float = 0.0
        self._has_first_stt: bool = False
        self._in_turn: bool = False
        self._llm_model_name: str = "unknown"
        self._llm_provider_name: str = "unknown"

    async def on_push_frame(self, data: FramePushed) -> None:
        """Handle frame push events and log key pipeline activities.

        Args:
            data: The frame push event data containing source, frame, and other info.
        """
        src = data.source
        frame = data.frame

        match (frame, src):
            # Log pipeline start when it reaches the output transport (end of pipeline)
            case (StartFrame(), BaseOutputTransport()):
                logger.success("Pipeline started")

            # Log audio frames from input transport (periodic sampling)
            case (InputAudioRawFrame() as f, BaseInputTransport()):
                self._audio_frame_count += 1
                if self._audio_frame_count % 500 == 0:
                    logger.info(
                        f"Audio frame #{self._audio_frame_count}: "
                        f"{len(f.audio)} bytes, {f.sample_rate}Hz, {f.num_channels}ch"
                    )

            # Log transcription from STT service and track first-transcription timing
            case (TranscriptionFrame() as f, STTService()):
                logger.info(f"TRANSCRIPTION: '{f.text}'")
                if self._in_turn and not self._has_first_stt:
                    self._first_stt_time = time.monotonic()
                    self._has_first_stt = True

            # Track turn lifecycle from TurnController (recording start/stop)
            case (UserStartedSpeakingFrame(), TurnController()):
                self._turn_start_time = time.monotonic()
                self._has_first_stt = False
                self._in_turn = True

            case (UserStoppedSpeakingFrame(), TurnController()):
                self._turn_stop_time = time.monotonic()

            # Log speech start from input transport (where VAD runs)
            # Use state tracking to deduplicate - same event may come from multiple sources
            case (UserStartedSpeakingFrame(), BaseInputTransport()) if not self._is_speaking:
                self._is_speaking = True
                logger.info("Speech started")

            # Log speech stop from input transport
            case (UserStoppedSpeakingFrame(), BaseInputTransport()) if self._is_speaking:
                self._is_speaking = False
                logger.info("Speech stopped")

            # Accumulate and log LLM response from LLM service
            # Use LLMTextFrame (not TextFrame) - this is what LLM services output
            case (LLMFullResponseStartFrame(), LLMService() as llm_service):
                self._llm_provider_name = type(llm_service).__name__
                self._llm_model_name = getattr(llm_service, "model_name", "unknown")
                logger.info(
                    f"LLM processing with {self._llm_provider_name} "
                    f"(model: {self._llm_model_name})"
                )
                self._llm_accumulator = ""
                self._is_accumulating = True
                self._llm_start_time = time.monotonic()
                self._llm_has_first_token = False

            case (LLMTextFrame() as f, LLMService()) if self._is_accumulating:
                if not self._llm_has_first_token:
                    self._llm_first_token_time = time.monotonic()
                    self._llm_has_first_token = True
                self._llm_accumulator += f.text

            case (LLMFullResponseEndFrame(), LLMService()):
                self._is_accumulating = False
                llm_end_time = time.monotonic()
                if self._llm_accumulator.strip():
                    ttfb_ms = (
                        (self._llm_first_token_time - self._llm_start_time) * 1000
                        if self._llm_has_first_token
                        else 0
                    )
                    generation_ms = (llm_end_time - self._llm_start_time) * 1000
                    logger.info(
                        f"Cleaned text: '{self._llm_accumulator.strip()}' "
                        f"(TTFB: {ttfb_ms:.0f}ms, total: {generation_ms:.0f}ms)"
                    )

                    # Emit structured per-turn latency summary
                    if self._in_turn:
                        self._emit_turn_latency_summary(llm_end_time, ttfb_ms, generation_ms)

                self._llm_accumulator = ""
                self._in_turn = False

            # Log RTVI server messages when sent from output transport
            case (RTVIServerMessageFrame() as f, BaseOutputTransport()):
                logger.info(f"Sending to client: {f.data}")

            # Log other frames at debug level (skip noisy ones)
            case _ if not isinstance(
                frame, UserSpeakingFrame | MetricsFrame | TextFrame | LLMTextFrame
            ):
                logger.debug(f"Frame: {type(frame).__name__}")

    def _emit_turn_latency_summary(
        self,
        llm_end_time: float,
        llm_ttfb_ms: float,
        llm_generation_ms: float,
    ) -> None:
        """Emit a structured per-turn latency breakdown.

        Logs a summary line with timing for each stage of the turn pipeline,
        making it easy to compare performance across models and configurations.
        """
        turn_total_ms = (llm_end_time - self._turn_start_time) * 1000
        turn_detection_ms = (self._turn_stop_time - self._turn_start_time) * 1000

        stt_latency_ms = (
            (self._first_stt_time - self._turn_start_time) * 1000
            if self._has_first_stt
            else 0
        )

        logger.info(
            f"TURN LATENCY | "
            f"stt: {stt_latency_ms:.0f}ms | "
            f"turn_detection: {turn_detection_ms:.0f}ms | "
            f"llm_ttfb: {llm_ttfb_ms:.0f}ms | "
            f"llm_total: {llm_generation_ms:.0f}ms | "
            f"total: {turn_total_ms:.0f}ms | "
            f"model: {self._llm_model_name} | "
            f"provider: {self._llm_provider_name}"
        )
