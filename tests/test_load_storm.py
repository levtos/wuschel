"""New protected-storm recording; the ten b25 recordings stay frozen."""

import os

import pytest

from core_contracts.errors import LoadProtected
from core_contracts.observation_block import IngestQueue

from .test_declarative import observation
from .test_replay import FIXTURES, Replay, canonical


async def test_guarded_storm_replay(config):
    replay = Replay()
    await replay.start()
    runtime = replay.runtime
    try:
        config["sources"][0]["source_type"] = "event"
        await replay.activate(config)
        original_queue = runtime.queue
        runtime.queue = IngestQueue(8)
        states = [runtime.load_guard.state]
        try:
            for index in range(12):
                runtime.ingest(
                    observation(runtime, bool(index % 2)).model_copy(
                        update={"ingest_seq": index + 1}
                    )
                )
            assert runtime.queue.qsize() == 12 and not runtime.latest
            runtime.commit_samples.clear()
            runtime.sample_load()
            replay.clock.advance(runtime.operations.load_enter_hold_s)
            runtime.sample_load()
            states.append(runtime.load_guard.state)
            assert states[-1] == "geschützt"
            with pytest.raises(LoadProtected):
                await runtime.submit("retention_preview")
            seen = []
            sequences = []
            while not runtime.queue.empty():
                work = runtime.queue.get_nowait()
                await runtime.process(work.operation, work.payload)
                runtime.queue.task_done()
                seen.append(
                    runtime.state.tables["source_observation_current"]["binding.a"]["ingest_seq"]
                )
                sequences.append(runtime.state.publication_seq)
                replay.collect()
            assert seen == list(range(1, 13))
            assert sequences == list(range(sequences[0], sequences[0] + 12))
            assert not runtime.overflow and not runtime.resubscribe.is_set()
        finally:
            runtime.queue = original_queue
        runtime.commit_samples.clear()
        runtime.sample_load()
        replay.clock.advance(runtime.operations.load_recover_hold_s)
        runtime.sample_load()
        states.append(runtime.load_guard.state)
        replay.collect()
        assert states == ["normal", "geschützt", "normal"]
        replay.results.append(
            {
                "load_states": states,
                "protected_ingest_sequences": seen,
                "publication_sequences": sequences,
                "reconnect_requested": runtime.resubscribe.is_set(),
                "heavy_operation": "load_protected",
            }
        )
        actual = canonical(await replay.output())
        path = FIXTURES / "event_storm_protected.json"
        if os.environ.get("WUSCHEL_REPLAY_RECORD") == "1":
            path.write_text(actual, encoding="utf-8", newline="\n")
        else:
            assert actual == path.read_text(encoding="utf-8")
    finally:
        await runtime.stop()
