import asyncio
import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import lubo.apps.desktop.preview as preview_module
from lubo.apps.desktop.preview import PreviewSession, PreviewState, PreviewUpdate
from lubo.apps.desktop.pyav_decoder import DecodedFrame, PreviewDecodeError
from lubo.core.models import RecordingTarget, StreamInfo


FRAME = DecodedFrame(width=1, height=1, rgba=b"\x01\x02\x03\x04")
OTHER_FRAME = DecodedFrame(width=1, height=1, rgba=b"\x05\x06\x07\x08")


def target(target_id="target-a"):
    return RecordingTarget(
        "https://example.invalid/watch?token=target-secret",
        id=target_id,
    )


def stream(is_live=True):
    return StreamInfo(
        platform_key="test",
        platform_name="Test",
        is_live=is_live,
        primary_url="https://media.invalid/live?token=stream-secret",
        headers={"Authorization": "header-secret"},
    )


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def worker_for(generation):
    return next(
        thread
        for thread in threading.enumerate()
        if thread.name == f"PreviewSession-{generation}"
    )


class Updates:
    def __init__(self):
        self.items = []
        self.condition = threading.Condition()

    def __call__(self, update):
        with self.condition:
            self.items.append(update)
            self.condition.notify_all()

    def wait_for(self, state, count=1, timeout=1.0):
        deadline = time.monotonic() + timeout
        with self.condition:
            while sum(item.state is state for item in self.items) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True

    def states(self):
        with self.condition:
            return [item.state for item in self.items]


class HoldingDecoder:
    def __init__(self, frame=FRAME, close_error=None):
        self.frame = frame
        self.close_error = close_error
        self.entered = threading.Event()
        self.close_calls = 0

    def frames(self, source, stop_event):
        self.entered.set()
        yield self.frame
        stop_event.wait(2.0)

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FailingDecoder:
    def __init__(self, clean_eof=False):
        self.clean_eof = clean_eof
        self.close_calls = 0

    def frames(self, source, stop_event):
        if self.clean_eof:
            return
        raise PreviewDecodeError("decoder-secret")
        yield

    def close(self):
        self.close_calls += 1


class BlockingDecoder:
    def __init__(self, *, yield_first=False, close_error=None):
        self.yield_first = yield_first
        self.close_error = close_error
        self.entered = threading.Event()
        self.release = threading.Event()
        self.close_calls = 0

    def frames(self, source, stop_event):
        if self.yield_first:
            yield FRAME
        self.entered.set()
        self.release.wait(2.0)
        raise PreviewDecodeError("late-decoder-secret")

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class PreviewValueTests(unittest.TestCase):
    def test_preview_state_and_update_are_immutable_values(self):
        self.assertIsInstance(PreviewState.IDLE, str)
        self.assertEqual(PreviewState.PLAYING.value, "playing")
        update = PreviewUpdate(1, "target-a", PreviewState.PLAYING, frame=FRAME)

        with self.assertRaises(FrozenInstanceError):
            update.state = PreviewState.FAILED

        with self.assertRaises(FrozenInstanceError):
            FRAME.width = 2

        self.assertFalse(hasattr(update, "__dict__"))


class PreviewSessionTests(unittest.TestCase):
    def test_live_start_resolves_connects_and_delivers_frames(self):
        decoder = HoldingDecoder()
        updates = Updates()

        async def resolver(source):
            self.assertEqual(source.id, "target-a")
            return stream()

        session = PreviewSession(resolver, decoder_factory=lambda: decoder)
        generation = session.start(target(), updates)
        self.addCleanup(session.close)

        self.assertTrue(updates.wait_for(PreviewState.PLAYING))
        self.assertEqual(generation, 1)
        self.assertEqual(session.target_id, "target-a")
        self.assertEqual(
            updates.states()[:3],
            [
                PreviewState.RESOLVING,
                PreviewState.CONNECTING,
                PreviewState.PLAYING,
            ],
        )
        playing = next(item for item in updates.items if item.state is PreviewState.PLAYING)
        self.assertIs(playing.frame, FRAME)
        self.assertTrue(all(item.generation == generation for item in updates.items))

    def test_offline_is_terminal_without_decoder_or_retry(self):
        updates = Updates()
        decoder_factory_calls = []

        async def resolver(source):
            return stream(is_live=False)

        session = PreviewSession(
            resolver,
            decoder_factory=lambda: decoder_factory_calls.append(True),
            retry_delays=(0.0, 0.0, 0.0),
        )
        session.start(target(), updates)
        self.addCleanup(session.close)

        self.assertTrue(updates.wait_for(PreviewState.OFFLINE))
        self.assertEqual(
            updates.states(),
            [PreviewState.RESOLVING, PreviewState.OFFLINE],
        )
        self.assertEqual(decoder_factory_calls, [])

    def test_resolver_failure_retries_exactly_three_times_then_fails(self):
        updates = Updates()
        attempts = 0

        async def resolver(source):
            nonlocal attempts
            attempts += 1
            raise RuntimeError(
                f"resolver-secret {source.url} header-secret stream-secret"
            )

        session = PreviewSession(resolver, retry_delays=(0.0, 0.0, 0.0))
        session.start(target(), updates)
        self.addCleanup(session.close)

        self.assertTrue(updates.wait_for(PreviewState.FAILED))
        self.assertEqual(attempts, 4)
        self.assertEqual(updates.states().count(PreviewState.RETRYING), 3)
        self.assertEqual(updates.states().count(PreviewState.RESOLVING), 4)
        diagnostics = repr(updates.items)
        for secret in (
            "resolver-secret",
            "target-secret",
            "header-secret",
            "stream-secret",
        ):
            self.assertNotIn(secret, diagnostics)

    def test_decoder_error_and_clean_eof_are_retryable(self):
        for clean_eof in (False, True):
            with self.subTest(clean_eof=clean_eof):
                updates = Updates()
                failed = FailingDecoder(clean_eof=clean_eof)
                holding = HoldingDecoder()
                decoders = iter((failed, holding))
                resolver_calls = 0

                async def resolver(source):
                    nonlocal resolver_calls
                    resolver_calls += 1
                    return stream()

                session = PreviewSession(
                    resolver,
                    decoder_factory=lambda: next(decoders),
                    retry_delays=(0.0,),
                )
                session.start(target(), updates)
                try:
                    self.assertTrue(updates.wait_for(PreviewState.PLAYING))
                    self.assertEqual(resolver_calls, 2)
                    self.assertEqual(
                        updates.states().count(PreviewState.RETRYING),
                        1,
                    )
                    self.assertEqual(failed.close_calls, 1)
                finally:
                    session.close()

    def test_stop_cancels_retry_delay_immediately(self):
        updates = Updates()

        async def resolver(source):
            raise RuntimeError("resolver-secret")

        session = PreviewSession(resolver, retry_delays=(5.0,), join_timeout=0.2)
        session.start(target(), updates)
        self.assertTrue(updates.wait_for(PreviewState.RETRYING))

        started = time.monotonic()
        session.stop()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5)
        self.assertEqual(updates.states()[-1], PreviewState.STOPPED)
        self.assertNotIn(PreviewState.FAILED, updates.states())

    def test_switch_target_drops_late_old_frame(self):
        old_entered = threading.Event()
        old_release = threading.Event()
        old_attempted = threading.Event()

        class LateDecoder:
            def frames(self, source, stop_event):
                old_entered.set()
                old_release.wait(2.0)
                old_attempted.set()
                yield FRAME

            def close(self):
                pass

        new_decoder = HoldingDecoder(OTHER_FRAME)
        decoders = iter((LateDecoder(), new_decoder))
        updates = Updates()

        async def resolver(source):
            return stream()

        session = PreviewSession(
            resolver,
            decoder_factory=lambda: next(decoders),
            retry_delays=(),
            join_timeout=0.02,
        )
        old_generation = session.start(target("old-target"), updates)
        self.assertTrue(old_entered.wait(1.0))
        old_worker = worker_for(old_generation)

        new_generation = session.start(target("new-target"), updates)
        self.assertGreater(new_generation, old_generation)
        old_release.set()
        self.addCleanup(session.close)

        self.assertTrue(updates.wait_for(PreviewState.PLAYING))
        self.assertTrue(old_attempted.wait(1.0))
        old_worker.join(1.0)
        self.assertFalse(old_worker.is_alive())
        playing = [item for item in updates.items if item.state is PreviewState.PLAYING]
        self.assertEqual([(item.target_id, item.frame) for item in playing], [
            ("new-target", OTHER_FRAME)
        ])

    def test_stop_during_resolve_emits_only_stopped_terminal(self):
        entered = threading.Event()
        release = threading.Event()
        updates = Updates()

        async def resolver(source):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            raise RuntimeError("late-resolver-secret")

        session = PreviewSession(resolver, retry_delays=(0.0,), join_timeout=0.02)
        generation = session.start(target(), updates)
        self.assertTrue(entered.wait(1.0))
        worker = worker_for(generation)

        session.stop()
        release.set()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(updates.states()[-1], PreviewState.STOPPED)
        self.assertNotIn(PreviewState.RETRYING, updates.states())
        self.assertNotIn(PreviewState.FAILED, updates.states())

    def test_stop_during_open_and_decode_suppresses_failures(self):
        for yield_first in (False, True):
            with self.subTest(yield_first=yield_first):
                decoder = BlockingDecoder(yield_first=yield_first)
                updates = Updates()

                async def resolver(source):
                    return stream()

                session = PreviewSession(
                    resolver,
                    decoder_factory=lambda: decoder,
                    retry_delays=(0.0,),
                    join_timeout=0.02,
                )
                generation = session.start(target(), updates)
                self.assertTrue(decoder.entered.wait(1.0))
                worker = worker_for(generation)

                session.stop()
                decoder.release.set()
                worker.join(1.0)

                self.assertFalse(worker.is_alive())
                self.assertEqual(updates.states()[-1], PreviewState.STOPPED)
                self.assertNotIn(PreviewState.RETRYING, updates.states())
                self.assertNotIn(PreviewState.FAILED, updates.states())
                self.assertEqual(decoder.close_calls, 1)

    def test_stop_is_bounded_when_callback_is_permanently_blocked(self):
        callback_entered = threading.Event()
        callback_release = threading.Event()
        stopped = threading.Event()
        items = []

        def callback(update):
            items.append(update)
            if update.state is PreviewState.RESOLVING:
                callback_entered.set()
                callback_release.wait(2.0)

        async def resolver(source):
            return stream()

        session = PreviewSession(
            resolver,
            decoder_factory=HoldingDecoder,
            join_timeout=0.05,
        )
        session.start(target(), callback)
        self.assertTrue(callback_entered.wait(1.0))

        stopper = threading.Thread(target=lambda: (session.stop(), stopped.set()))
        started = time.monotonic()
        stopper.start()
        try:
            self.assertTrue(stopped.wait(0.25))
            self.assertLess(time.monotonic() - started, 0.2)
            count_after_stop = len(items)
            time.sleep(0.05)
            self.assertEqual(len(items), count_after_stop)
            self.assertEqual(items[-1].state, PreviewState.STOPPED)
        finally:
            callback_release.set()
            stopper.join(1.0)

    def test_stop_is_bounded_when_stopped_callback_never_returns(self):
        stopped_entered = threading.Event()
        callback_release = threading.Event()
        stop_returned = threading.Event()
        updates = Updates()

        def callback(update):
            updates(update)
            if update.state is PreviewState.STOPPED:
                stopped_entered.set()
                callback_release.wait(2.0)

        async def resolver(source):
            return stream(is_live=False)

        session = PreviewSession(resolver, join_timeout=0.05)
        session.start(target(), callback)
        self.assertTrue(updates.wait_for(PreviewState.OFFLINE))
        stopper = threading.Thread(
            target=lambda: (session.stop(), stop_returned.set())
        )
        started = time.monotonic()
        stopper.start()
        try:
            self.assertTrue(stopped_entered.wait(1.0))
            self.assertTrue(stop_returned.wait(0.2))
            self.assertLess(time.monotonic() - started, 0.2)
        finally:
            callback_release.set()
            stopper.join(1.0)

    def test_stop_cancels_callback_admitted_before_delivery_starts(self):
        delivery_gap = threading.Event()
        delivery_release = threading.Event()
        stop_returned = threading.Event()
        updates = Updates()

        async def resolver(source):
            return stream()

        session = PreviewSession(resolver, join_timeout=0.05)
        original_deliver = session._deliver

        def delayed_deliver(callback, update):
            if update.state is PreviewState.RESOLVING:
                delivery_gap.set()
                delivery_release.wait(2.0)
            original_deliver(callback, update)

        session._deliver = delayed_deliver
        generation = session.start(target(), updates)
        self.assertTrue(delivery_gap.wait(1.0))
        worker = worker_for(generation)
        stopper = threading.Thread(
            target=lambda: (session.stop(), stop_returned.set())
        )
        stopper.start()
        try:
            self.assertTrue(stop_returned.wait(0.2))
            delivery_release.set()
            worker.join(1.0)
            self.assertFalse(worker.is_alive())
            self.assertFalse(any(
                update.generation == generation
                and update.state is PreviewState.RESOLVING
                for update in updates.items
            ))
            self.assertEqual(updates.states(), [PreviewState.STOPPED])
        finally:
            delivery_release.set()
            stopper.join(1.0)

    def test_stop_is_bounded_when_decoder_close_never_returns(self):
        close_entered = threading.Event()
        close_release = threading.Event()
        stop_returned = threading.Event()
        updates = Updates()

        class NeverClosingDecoder(BlockingDecoder):
            def close(self):
                self.close_calls += 1
                close_entered.set()
                close_release.wait(2.0)

        decoder = NeverClosingDecoder()

        async def resolver(source):
            return stream()

        session = PreviewSession(
            resolver,
            decoder_factory=lambda: decoder,
            join_timeout=0.05,
        )
        session.start(target(), updates)
        self.assertTrue(decoder.entered.wait(1.0))
        stopper = threading.Thread(
            target=lambda: (session.stop(), stop_returned.set())
        )
        started = time.monotonic()
        stopper.start()
        try:
            self.assertTrue(close_entered.wait(1.0))
            self.assertTrue(stop_returned.wait(0.2))
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual(updates.states()[-1], PreviewState.STOPPED)
        finally:
            close_release.set()
            decoder.release.set()
            stopper.join(1.0)

    def test_concurrent_starts_leave_only_one_current_run(self):
        original_stop = None
        stop_barrier = threading.Barrier(2)
        updates = Updates()
        decoder_lock = threading.Lock()
        decoders = []

        class ConcurrentDecoder(BlockingDecoder):
            def frames(self, source, stop_event):
                self.target_id = source.anchor_name
                self.stop_event = stop_event
                yield from super().frames(source, stop_event)

        async def resolver(source):
            return StreamInfo(
                platform_key="test",
                platform_name="Test",
                anchor_name=source.id,
                is_live=True,
                primary_url="https://media.invalid/live",
            )

        def decoder_factory():
            decoder = ConcurrentDecoder()
            with decoder_lock:
                decoders.append(decoder)
            return decoder

        session = PreviewSession(
            resolver,
            decoder_factory=decoder_factory,
            retry_delays=(),
            join_timeout=0.02,
        )
        original_stop = session.stop

        def synchronized_stop():
            original_stop()
            stop_barrier.wait(1.0)

        session.stop = synchronized_stop
        results = {}

        def launch(target_id):
            results[target_id] = session.start(target(target_id), updates)

        starters = [
            threading.Thread(target=launch, args=(target_id,))
            for target_id in ("concurrent-a", "concurrent-b")
        ]
        for starter in starters:
            starter.start()
        for starter in starters:
            starter.join(1.0)

        self.assertTrue(all(not starter.is_alive() for starter in starters))
        self.assertIn(session.target_id, results)
        current_target = session.target_id
        current_generation = results[current_target]
        self.assertTrue(wait_until(lambda: len(decoders) >= 1))
        self.assertTrue(any(decoder.entered.wait(1.0) for decoder in decoders))
        stale_decoders = [
            decoder
            for decoder in decoders
            if getattr(decoder, "target_id", None) != current_target
        ]
        stale_generation = next(
            generation
            for target_id, generation in results.items()
            if target_id != current_target
        )
        stale_worker = next(
            (
                thread
                for thread in threading.enumerate()
                if thread.name == f"PreviewSession-{stale_generation}"
            ),
            None,
        )
        for decoder in stale_decoders:
            decoder.release.set()
        if stale_worker is not None:
            stale_worker.join(1.0)
            self.assertFalse(stale_worker.is_alive())

        current_index = next(
            index
            for index, update in enumerate(updates.items)
            if update.generation == current_generation
        )
        stale_stopped = [
            update
            for update in updates.items[:current_index]
            if update.generation == stale_generation
            and update.state is PreviewState.STOPPED
        ]
        self.assertEqual(len(stale_stopped), 1)
        for decoder in stale_decoders:
            self.assertTrue(decoder.stop_event.is_set())
            self.assertGreaterEqual(decoder.close_calls, 1)
        self.assertFalse(any(
            update.generation == stale_generation
            for update in updates.items[current_index + 1 :]
        ))
        for decoder in decoders:
            decoder.release.set()
        session.stop = original_stop
        session.close()

    def test_concurrent_stop_waits_for_in_progress_stopped_update(self):
        close_entered = threading.Event()
        close_release = threading.Event()
        updates = Updates()

        class BlockingCloseDecoder(BlockingDecoder):
            def close(self):
                self.close_calls += 1
                self.release.set()
                close_entered.set()
                close_release.wait(1.0)

        decoder = BlockingCloseDecoder()

        async def resolver(source):
            return stream()

        session = PreviewSession(
            resolver,
            decoder_factory=lambda: decoder,
            join_timeout=0.5,
        )
        session.start(target(), updates)
        self.assertTrue(decoder.entered.wait(1.0))
        first_done = threading.Event()
        second_done = threading.Event()
        first = threading.Thread(target=lambda: (session.stop(), first_done.set()))
        second = threading.Thread(target=lambda: (session.stop(), second_done.set()))
        first.start()
        self.assertTrue(close_entered.wait(1.0))
        second.start()
        self.assertFalse(second_done.wait(0.05))

        close_release.set()
        self.assertTrue(first_done.wait(1.0))
        self.assertTrue(second_done.wait(1.0))
        first.join(1.0)
        second.join(1.0)

        self.assertEqual(updates.states().count(PreviewState.STOPPED), 1)
        self.assertEqual(updates.states()[-1], PreviewState.STOPPED)

    def test_worker_is_named_daemon_and_stop_join_is_bounded(self):
        entered = threading.Event()
        release = threading.Event()

        async def resolver(source):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return stream(is_live=False)

        session = PreviewSession(resolver, join_timeout=0.02)
        generation = session.start(target(), lambda update: None)
        self.assertTrue(entered.wait(1.0))
        worker = next(
            thread
            for thread in threading.enumerate()
            if thread.name == f"PreviewSession-{generation}"
        )

        started = time.monotonic()
        session.stop()
        elapsed = time.monotonic() - started
        release.set()
        worker.join(1.0)

        self.assertTrue(worker.daemon)
        self.assertLess(elapsed, 0.5)

    def test_many_frames_reuse_one_callback_dispatcher_thread(self):
        frame_count = 64
        created_thread_names = []
        created_threads = []
        real_thread = threading.Thread
        updates = Updates()

        class BurstDecoder:
            def frames(self, source, stop_event):
                for _ in range(frame_count):
                    yield FRAME
                stop_event.wait(2.0)

            def close(self):
                pass

        async def resolver(source):
            return stream()

        def tracked_thread(*args, **kwargs):
            created_thread_names.append(kwargs.get("name", ""))
            thread = real_thread(*args, **kwargs)
            created_threads.append(thread)
            return thread

        with patch.object(preview_module.threading, "Thread", tracked_thread):
            session = PreviewSession(
                resolver,
                decoder_factory=BurstDecoder,
                join_timeout=0.05,
            )
            session.start(target(), updates)
            self.assertTrue(
                updates.wait_for(PreviewState.PLAYING, count=frame_count)
            )
            session.stop()

        normal_callback_threads = [
            name
            for name in created_thread_names
            if name.startswith("PreviewCallback-")
        ]
        dispatcher_threads = [
            name
            for name in created_thread_names
            if name.startswith("PreviewCallbackDispatcher-")
        ]
        self.assertLessEqual(len(normal_callback_threads), 1)
        self.assertEqual(len(dispatcher_threads), 1)
        self.assertLessEqual(len(created_threads), 4)
        dispatcher = next(
            thread
            for thread in created_threads
            if thread.name.startswith("PreviewCallbackDispatcher-")
        )
        self.assertTrue(dispatcher.daemon)
        self.assertFalse(dispatcher.is_alive())

    def test_decoder_close_error_is_isolated_and_sanitized(self):
        decoder = BlockingDecoder(
            close_error=RuntimeError("close-secret stream-secret")
        )
        updates = Updates()

        async def resolver(source):
            return stream()

        session = PreviewSession(
            resolver,
            decoder_factory=lambda: decoder,
            join_timeout=0.02,
        )
        session.start(target(), updates)
        self.assertTrue(decoder.entered.wait(1.0))

        with self.assertLogs("lubo.apps.desktop.preview", level="WARNING") as logs:
            session.stop()
        decoder.release.set()

        diagnostics = "\n".join(logs.output)
        self.assertEqual(decoder.close_calls, 1)
        self.assertIn("RuntimeError", diagnostics)
        self.assertNotIn("close-secret", diagnostics)
        self.assertNotIn("stream-secret", diagnostics)
        self.assertEqual(updates.states()[-1], PreviewState.STOPPED)

    def test_callback_exception_does_not_break_cleanup(self):
        decoder = BlockingDecoder()
        callback_calls = 0

        def callback(update):
            nonlocal callback_calls
            callback_calls += 1
            raise RuntimeError("callback-secret stream-secret")

        async def resolver(source):
            return stream()

        session = PreviewSession(resolver, decoder_factory=lambda: decoder)
        with self.assertLogs("lubo.apps.desktop.preview", level="WARNING") as logs:
            session.start(target(), callback)
            self.assertTrue(decoder.entered.wait(1.0))
            session.stop()
        decoder.release.set()

        diagnostics = "\n".join(logs.output)
        self.assertGreaterEqual(callback_calls, 3)
        self.assertEqual(decoder.close_calls, 1)
        self.assertIsNone(session.target_id)
        self.assertNotIn("callback-secret", diagnostics)
        self.assertNotIn("stream-secret", diagnostics)

    def test_stop_called_from_callback_does_not_deadlock(self):
        decoder = HoldingDecoder()
        stopped = threading.Event()
        updates = []
        session = None

        def callback(update):
            updates.append(update)
            if update.state is PreviewState.PLAYING:
                session.stop()
            if update.state is PreviewState.STOPPED:
                stopped.set()

        async def resolver(source):
            return stream()

        session = PreviewSession(resolver, decoder_factory=lambda: decoder)
        session.start(target(), callback)

        self.assertTrue(stopped.wait(1.0))
        self.assertEqual(updates[-1].state, PreviewState.STOPPED)
        self.assertTrue(wait_until(lambda: decoder.close_calls == 1))
        self.assertEqual(decoder.close_calls, 1)
        self.assertIsNone(session.target_id)

    def test_stale_callback_start_cannot_replace_current_run(self):
        stale_entered = threading.Event()
        stale_release = threading.Event()
        stale_done = threading.Event()
        stale_result = {}
        current_updates = Updates()
        rejected_updates = Updates()
        session = None

        def stale_callback(update):
            if update.state is PreviewState.RESOLVING:
                stale_entered.set()
                stale_release.wait(2.0)
                stale_result["generation"] = session.start(
                    target("rejected-target"),
                    rejected_updates,
                )
                stale_done.set()

        async def resolver(source):
            return stream(is_live=False)

        session = PreviewSession(resolver, join_timeout=0.05)
        session.start(target("old-target"), stale_callback)
        self.assertTrue(stale_entered.wait(1.0))

        current_generation = session.start(
            target("current-target"),
            current_updates,
        )
        self.assertTrue(current_updates.wait_for(PreviewState.OFFLINE))
        stale_release.set()
        self.assertTrue(stale_done.wait(1.0))

        self.assertEqual(stale_result["generation"], current_generation)
        self.assertEqual(session.target_id, "current-target")
        self.assertEqual(rejected_updates.items, [])
        session.close()

    def test_current_callback_start_switches_without_waiting_for_worker_join(self):
        decoder_entered = threading.Event()
        frame_release = threading.Event()
        switched = threading.Event()
        switch_result = {}
        new_updates = Updates()
        session = None

        class GatedFrameDecoder:
            def frames(self, source, stop_event):
                decoder_entered.set()
                frame_release.wait(2.0)
                yield FRAME
                stop_event.wait(2.0)

            def close(self):
                pass

        def callback(update):
            if update.state is PreviewState.PLAYING:
                started = time.monotonic()
                switch_result["generation"] = session.start(
                    target("callback-target"),
                    new_updates,
                )
                switch_result["elapsed"] = time.monotonic() - started
                switched.set()

        async def resolver(source):
            if source.id == "callback-target":
                return stream(is_live=False)
            return stream()

        session = PreviewSession(
            resolver,
            decoder_factory=GatedFrameDecoder,
            join_timeout=0.5,
        )
        original_generation = session.start(target("initial-target"), callback)
        self.assertTrue(decoder_entered.wait(1.0))
        worker = worker_for(original_generation)
        original_join = worker.join

        def slow_join(timeout=None):
            time.sleep(0.3)
            original_join(0.0)

        worker.join = slow_join
        frame_release.set()

        self.assertTrue(switched.wait(0.2))
        self.assertLess(switch_result["elapsed"], 0.2)
        self.assertGreater(switch_result["generation"], original_generation)
        self.assertEqual(session.target_id, "callback-target")
        self.assertTrue(new_updates.wait_for(PreviewState.OFFLINE))
        session.close()

    def test_stop_from_resolving_callback_prevents_resolver_call(self):
        resolver_called = threading.Event()
        stopped = threading.Event()
        session = None

        def callback(update):
            if update.state is PreviewState.RESOLVING:
                session.stop()
            if update.state is PreviewState.STOPPED:
                stopped.set()

        async def resolver(source):
            resolver_called.set()
            return stream()

        session = PreviewSession(resolver)
        session.start(target(), callback)

        self.assertTrue(stopped.wait(1.0))
        self.assertFalse(resolver_called.wait(0.05))
        self.assertIsNone(session.target_id)

    def test_close_is_idempotent_clears_target_and_generations_increase(self):
        updates = Updates()

        async def resolver(source):
            return stream(is_live=False)

        session = PreviewSession(resolver)
        first = session.start(target("first"), updates)
        self.assertTrue(updates.wait_for(PreviewState.OFFLINE))
        session.close()
        stopped_count = updates.states().count(PreviewState.STOPPED)
        session.close()

        second = session.start(target("second"), updates)
        self.assertGreater(second, first)
        session.close()

        self.assertEqual(updates.states().count(PreviewState.STOPPED), stopped_count + 1)
        self.assertIsNone(session.target_id)


if __name__ == "__main__":
    unittest.main()
