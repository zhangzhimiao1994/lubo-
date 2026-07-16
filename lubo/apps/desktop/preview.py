from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Protocol

from lubo.apps.desktop.pyav_decoder import DecodedFrame, PyAvDecoder
from lubo.core.models import RecordingTarget, StreamInfo


logger = logging.getLogger(__name__)
_CALLBACK_STOP = object()


class PreviewState(str, Enum):
    IDLE = "idle"
    RESOLVING = "resolving"
    CONNECTING = "connecting"
    PLAYING = "playing"
    RETRYING = "retrying"
    OFFLINE = "offline"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class PreviewUpdate:
    generation: int
    target_id: str
    state: PreviewState
    message: str = ""
    frame: DecodedFrame | None = None


class _Decoder(Protocol):
    def frames(
        self,
        stream: StreamInfo,
        stop_event: threading.Event,
    ): ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _CallbackTicket:
    callback: Callable[[PreviewUpdate], None]
    update: PreviewUpdate
    status: str = "pending"
    admission_done: threading.Event = field(default_factory=threading.Event)
    completed: threading.Event = field(default_factory=threading.Event)
    release_worker: threading.Event = field(default_factory=threading.Event)


@dataclass(slots=True)
class _PreviewRun:
    generation: int
    target: RecordingTarget
    callback: Callable[[PreviewUpdate], None]
    stop_event: threading.Event
    thread: threading.Thread | None = None
    decoder: _Decoder | None = None
    callback_open: bool = True
    callback_tickets: list[_CallbackTicket] = field(default_factory=list)
    callback_queue: queue.Queue[object] = field(default_factory=queue.Queue)
    callback_thread: threading.Thread | None = None


_MESSAGES = {
    PreviewState.RESOLVING: "Resolving preview.",
    PreviewState.CONNECTING: "Connecting preview.",
    PreviewState.PLAYING: "Preview is playing.",
    PreviewState.RETRYING: "Retrying preview.",
    PreviewState.OFFLINE: "Preview is offline.",
    PreviewState.FAILED: "Preview failed.",
    PreviewState.STOPPED: "Preview stopped.",
}


class PreviewSession:
    def __init__(
        self,
        resolver: Callable[[RecordingTarget], Awaitable[StreamInfo]],
        decoder_factory: Callable[[], _Decoder] = PyAvDecoder,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
        join_timeout: float = 1.0,
    ) -> None:
        self._resolver = resolver
        self._decoder_factory = decoder_factory
        self._retry_delays = tuple(retry_delays)
        self._join_timeout = join_timeout
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._callback_context = threading.local()
        self._delivery_context = threading.local()
        self._generation = 0
        self._run: _PreviewRun | None = None

    @property
    def target_id(self) -> str | None:
        with self._state_lock:
            if self._run is None:
                return None
            return self._run.target.id

    def start(
        self,
        target: RecordingTarget,
        callback: Callable[[PreviewUpdate], None],
    ) -> int:
        callback_generation = getattr(
            self._callback_context,
            "generation",
            None,
        )
        with self._lifecycle_lock:
            if callback_generation is not None:
                with self._state_lock:
                    if (
                        self._run is None
                        or self._run.generation != callback_generation
                    ):
                        if self._run is not None:
                            return self._run.generation
                        return self._generation
            self._stop_locked(
                time.monotonic() + self._join_timeout,
                callback_generation=callback_generation,
            )
            with self._state_lock:
                self._generation += 1
                generation = self._generation
                run = _PreviewRun(
                    generation=generation,
                    target=target,
                    callback=callback,
                    stop_event=threading.Event(),
                )
                worker = threading.Thread(
                    target=self._worker,
                    args=(run,),
                    name=f"PreviewSession-{generation}",
                    daemon=True,
                )
                callback_thread = threading.Thread(
                    target=self._callback_dispatcher,
                    args=(run,),
                    name=f"PreviewCallbackDispatcher-{generation}",
                    daemon=True,
                )
                run.thread = worker
                run.callback_thread = callback_thread
                self._run = run
            callback_thread.start()
            worker.start()
            return generation

    def stop(self) -> None:
        deadline = time.monotonic() + self._join_timeout
        callback_generation = getattr(
            self._callback_context,
            "generation",
            None,
        )
        with self._lifecycle_lock:
            if callback_generation is not None:
                with self._state_lock:
                    if (
                        self._run is None
                        or self._run.generation != callback_generation
                    ):
                        return
            self._stop_locked(
                deadline,
                callback_generation=callback_generation,
            )

    def _stop_locked(
        self,
        deadline: float,
        *,
        callback_generation: int | None,
    ) -> None:
        with self._state_lock:
            run = self._run
            if run is None:
                return
            self._run = None
            run.callback_open = False
            run.stop_event.set()
            decoder = run.decoder
            run.decoder = None
            for ticket in run.callback_tickets:
                if ticket.status == "pending":
                    self._cancel_ticket_locked(ticket)
                elif ticket.status == "started":
                    ticket.release_worker.set()
            stopped_ticket = self._new_ticket(
                run,
                PreviewState.STOPPED,
            )
            run.callback_queue.put(_CALLBACK_STOP)

        self._launch_terminal_callback(stopped_ticket)
        close_worker = None
        if decoder is not None:
            close_worker = threading.Thread(
                target=self._close_decoder,
                args=(decoder,),
                name=f"PreviewDecoderClose-{run.generation}",
                daemon=True,
            )
            close_worker.start()

        if close_worker is not None:
            close_worker.join(self._remaining(deadline))

        worker = run.thread
        called_from_run_callback = callback_generation == run.generation
        if (
            worker is not None
            and worker is not threading.current_thread()
            and not called_from_run_callback
        ):
            worker.join(self._remaining(deadline))

        callback_thread = run.callback_thread
        if (
            callback_thread is not None
            and callback_thread is not threading.current_thread()
        ):
            callback_thread.join(self._remaining(deadline))

        if not stopped_ticket.admission_done.wait(self._remaining(deadline)):
            with self._state_lock:
                if stopped_ticket.status == "pending":
                    self._cancel_ticket_locked(stopped_ticket)
        with self._state_lock:
            stopped_started = stopped_ticket.status == "started"
        if stopped_started:
            stopped_ticket.completed.wait(self._remaining(deadline))

    def close(self) -> None:
        self.stop()

    def _worker(self, run: _PreviewRun) -> None:
        for delay in (*self._retry_delays, None):
            if not self._emit(run, PreviewState.RESOLVING):
                return

            try:
                source = asyncio.run(self._resolver(run.target))
                if run.stop_event.is_set() or not self._is_current(run):
                    return
                if not source.is_live:
                    self._emit(run, PreviewState.OFFLINE)
                    return

                if not self._emit(run, PreviewState.CONNECTING):
                    return
                decoder = self._decoder_factory()
                if not self._install_decoder(run, decoder):
                    self._close_decoder(decoder)
                    return

                try:
                    for frame in decoder.frames(source, run.stop_event):
                        if run.stop_event.is_set():
                            return
                        if not self._emit(run, PreviewState.PLAYING, frame=frame):
                            return
                    if run.stop_event.is_set() or not self._is_current(run):
                        return
                    raise RuntimeError("Unexpected preview end.")
                finally:
                    if self._take_decoder(run, decoder):
                        self._close_decoder(decoder)
            except Exception:
                if run.stop_event.is_set() or not self._is_current(run):
                    return

            if delay is None:
                self._emit(run, PreviewState.FAILED)
                return
            if not self._emit(run, PreviewState.RETRYING):
                return
            if run.stop_event.wait(delay):
                return

    def _emit(
        self,
        run: _PreviewRun,
        state: PreviewState,
        *,
        frame: DecodedFrame | None = None,
    ) -> bool:
        with self._state_lock:
            if (
                self._run is not run
                or run.stop_event.is_set()
                or not run.callback_open
            ):
                return False
            ticket = self._new_ticket(run, state, frame=frame)
        run.callback_queue.put(ticket)
        ticket.release_worker.wait()
        with self._state_lock:
            return self._run is run and not run.stop_event.is_set()

    def _new_ticket(
        self,
        run: _PreviewRun,
        state: PreviewState,
        *,
        frame: DecodedFrame | None = None,
    ) -> _CallbackTicket:
        run.callback_tickets[:] = [
            ticket
            for ticket in run.callback_tickets
            if ticket.status in {"pending", "started"}
        ]
        ticket = _CallbackTicket(
            callback=run.callback,
            update=PreviewUpdate(
                generation=run.generation,
                target_id=run.target.id,
                state=state,
                message=_MESSAGES[state],
                frame=frame,
            ),
        )
        run.callback_tickets.append(ticket)
        return ticket

    def _launch_terminal_callback(self, ticket: _CallbackTicket) -> None:
        worker = threading.Thread(
            target=self._run_callback_ticket,
            args=(ticket,),
            name=f"PreviewTerminalCallback-{ticket.update.generation}",
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            with self._state_lock:
                self._cancel_ticket_locked(ticket)
            logger.warning("Unable to start preview callback delivery.")

    def _callback_dispatcher(self, run: _PreviewRun) -> None:
        while True:
            item = run.callback_queue.get()
            if item is _CALLBACK_STOP:
                return
            if isinstance(item, _CallbackTicket):
                self._run_callback_ticket(item)

    def _run_callback_ticket(self, ticket: _CallbackTicket) -> None:
        self._delivery_context.ticket = ticket
        try:
            self._deliver(ticket.callback, ticket.update)
        finally:
            del self._delivery_context.ticket

    def _deliver(
        self,
        callback: Callable[[PreviewUpdate], None],
        update: PreviewUpdate,
    ) -> None:
        ticket = self._delivery_context.ticket
        with self._state_lock:
            if ticket.status != "pending":
                return
            ticket.status = "started"
            ticket.admission_done.set()
        previous_generation = getattr(
            self._callback_context,
            "generation",
            None,
        )
        self._callback_context.generation = update.generation
        try:
            callback(update)
        except Exception as error:
            logger.warning(
                "Preview callback failed (%s).",
                type(error).__name__,
            )
        finally:
            if previous_generation is None:
                del self._callback_context.generation
            else:
                self._callback_context.generation = previous_generation
            with self._state_lock:
                if ticket.status == "started":
                    ticket.status = "finished"
                ticket.completed.set()
                ticket.release_worker.set()

    @staticmethod
    def _cancel_ticket_locked(ticket: _CallbackTicket) -> None:
        ticket.status = "cancelled"
        ticket.admission_done.set()
        ticket.completed.set()
        ticket.release_worker.set()

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    def _is_current(self, run: _PreviewRun) -> bool:
        with self._state_lock:
            return self._run is run

    def _install_decoder(self, run: _PreviewRun, decoder: _Decoder) -> bool:
        with self._state_lock:
            if self._run is not run or run.stop_event.is_set():
                return False
            run.decoder = decoder
            return True

    def _take_decoder(self, run: _PreviewRun, decoder: _Decoder) -> bool:
        with self._state_lock:
            if run.decoder is not decoder:
                return False
            run.decoder = None
            return True

    @staticmethod
    def _close_decoder(decoder: _Decoder) -> None:
        try:
            decoder.close()
        except Exception as error:
            logger.warning(
                "Unable to close preview decoder (%s).",
                type(error).__name__,
            )
