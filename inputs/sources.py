"""Async text stream; speech dependencies are loaded only for audio inputs."""

import asyncio
import threading

from shared.config import AssistantConfig
from shared.reporting import log

DEFAULT_TEXT = "新增放飞前出任务，模式为原地起飞"


def next_transcript(recordings):
    # StopIteration must not propagate through asyncio's thread future.
    return next(recordings, None)


async def input_texts(args: AssistantConfig):
    if args.file is None and not args.mic:
        yield args.text if args.text is not None else DEFAULT_TEXT
        return
    from . import speech

    log("ASR", "加载本地 Paraformer；本次会话只加载一次")
    asr_model = await asyncio.to_thread(speech.load_model, args.asr_model_dir)
    if args.file is not None:
        text = await asyncio.to_thread(speech.transcribe_file, asr_model, args.file)
        log("ASR", "转写：" + text)
        yield text
        return

    stop = threading.Event()
    recordings = speech.iter_microphone_transcripts(asr_model, stop_event=stop)
    try:
        while True:
            pending = asyncio.create_task(asyncio.to_thread(next_transcript, recordings))
            try:
                text = await asyncio.shield(pending)
            except asyncio.CancelledError:
                stop.set()
                await pending  # Wait for the recorder to restore the terminal and close audio.
                raise
            if text is None:
                break
            log("ASR", "转写：" + text)
            yield text
    finally:
        stop.set()
        recordings.close()


