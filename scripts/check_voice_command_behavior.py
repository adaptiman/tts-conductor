"""Focused behavior checks for voice command dedupe and mute strategy wiring.

Run:
    /home/adaptiman/tts-conductor/.venv/bin/python scripts/check_voice_command_behavior.py
"""

import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from voice_commands import (
    BotSpeakingUserMuteStrategy,
    StrategyUserMuteProcessor,
    VoiceCommandProcessor,
)


def _interim(text: str) -> InterimTranscriptionFrame:
    return InterimTranscriptionFrame(
        text=text,
        user_id="test-user",
        timestamp="2026-03-24T00:00:00Z",
    )


def _final(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(
        text=text,
        user_id="test-user",
        timestamp="2026-03-24T00:00:01Z",
        finalized=True,
    )


async def _exercise_command_emit_source() -> None:
    commands: list[str] = []
    processor = VoiceCommandProcessor(
        on_command=commands.append,
        command_emit_source="turn_stop",
    )

    async def _push_noop(_frame, _direction=FrameDirection.DOWNSTREAM):
        return None

    processor.push_frame = _push_noop  # type: ignore[method-assign]

    await processor.process_frame(_interim("next"), FrameDirection.DOWNSTREAM)
    await processor.process_frame(_final("next"), FrameDirection.DOWNSTREAM)

    assert commands == ["next"], f"Expected one final command emit, got: {commands}"


async def _exercise_destructive_debounce() -> None:
    commands: list[str] = []
    processor = VoiceCommandProcessor(
        on_command=commands.append,
        command_emit_source="final",
        destructive_debounce_seconds=3.0,
        normal_debounce_seconds=0.1,
    )

    async def _push_noop(_frame, _direction=FrameDirection.DOWNSTREAM):
        return None

    processor.push_frame = _push_noop  # type: ignore[method-assign]

    await processor.process_frame(_final("delete"), FrameDirection.DOWNSTREAM)
    await processor.process_frame(_final("delete"), FrameDirection.DOWNSTREAM)

    assert commands == ["delete"], f"Delete should debounce duplicate emits, got: {commands}"


async def _exercise_bot_speaking_mute() -> None:
    strategy = BotSpeakingUserMuteStrategy()
    mute_processor = StrategyUserMuteProcessor(mute_strategies=[strategy])

    forwarded: list[str] = []

    async def _capture_push(frame, _direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            forwarded.append(frame.text)

    mute_processor.push_frame = _capture_push  # type: ignore[method-assign]

    await mute_processor.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await mute_processor.process_frame(_interim("next"), FrameDirection.DOWNSTREAM)
    await mute_processor.process_frame(_final("next"), FrameDirection.DOWNSTREAM)
    await mute_processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await mute_processor.process_frame(_final("next"), FrameDirection.DOWNSTREAM)

    assert forwarded == ["next"], f"Only post-bot-speech transcript should pass, got: {forwarded}"


async def main() -> None:
    await _exercise_command_emit_source()
    await _exercise_destructive_debounce()
    await _exercise_bot_speaking_mute()
    print("All focused voice behavior checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
