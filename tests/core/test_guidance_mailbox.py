from __future__ import annotations

import queue
import threading
import unittest
from dataclasses import replace

from run.conversation import GuidanceInput, GuidanceMailbox, advance_queued_guidance, next_order


class GuidanceOrderStampTests(unittest.TestCase):
    """Ordering must not depend on the platform monotonic clock resolution.

    Windows CPython before 3.13 reads time.monotonic() from GetTickCount64 in
    15.6 ms ticks, so a dispatch and an offer that really happened in order can
    return equal floats. That collapsed the "was this call dispatched before the
    guidance arrived?" test and detached calls that had not started yet.

    These tests deliberately do not patch time.monotonic: it is the process-wide
    clock and other threads rely on it. Equal timestamps are produced directly.
    """

    class _Integration:
        def __init__(self, ids):
            self._ids = ids

        def live_executions(self):
            return [{"id": i, "name": "shell", "tool_call_id": f"call_{i}"} for i in self._ids]

    class _Runtime:
        def __init__(self):
            self.cancel_event = threading.Event()
            self.advanced = []

        def shell_handoff_state(self, execution_id):
            self.advanced.append(execution_id)
            return "waiting"  # Only target binding is under test here.

    def _offer(self, text, *, offered_at):
        mailbox = GuidanceMailbox(maxsize=4)
        mailbox.offer(GuidanceInput(text=text))
        item, = mailbox.drain()
        # Force the diagnostic timestamp, keeping the allocated order stamp.
        return replace(item, _offered_at=offered_at)

    def test_offer_stamp_survives_equal_timestamps(self) -> None:
        # Two offers that a coarse clock reports at the same instant.
        one = self._offer("one", offered_at=1000.0)
        two = self._offer("two", offered_at=1000.0)
        self.assertEqual(one._offered_at, two._offered_at)  # Coarse clock collapses time.
        self.assertLess(one._offer_order, two._offer_order)  # Order still separates them.

    def test_call_dispatched_after_the_offer_is_not_a_target(self) -> None:
        # Real order: first dispatched -> guidance offered -> second dispatched.
        first_order = next_order()
        offered = self._offer("between the two dispatches", offered_at=1000.0)
        second_order = next_order()
        self.assertLess(first_order, offered._offer_order)
        self.assertLess(offered._offer_order, second_order)

        runtime = self._Runtime()
        interrupts: list = []
        list(advance_queued_guidance(
            [offered], tool_batch=1,
            batch_dispatch_times={"exec_first": first_order, "exec_second": second_order},
            async_execution_ids=set(),
            integration=self._Integration(["exec_first", "exec_second"]),
            runtime=runtime, delayed=[], interrupts=interrupts,
        ))

        self.assertEqual(list(interrupts[0].targets), ["exec_first"])
        self.assertEqual(runtime.advanced, ["exec_first"])

    def test_calls_dispatched_before_the_offer_all_bind(self) -> None:
        first_order, second_order = next_order(), next_order()
        offered = self._offer("after both dispatches", offered_at=1000.0)

        runtime = self._Runtime()
        interrupts: list = []
        list(advance_queued_guidance(
            [offered], tool_batch=1,
            batch_dispatch_times={"exec_first": first_order, "exec_second": second_order},
            async_execution_ids=set(),
            integration=self._Integration(["exec_first", "exec_second"]),
            runtime=runtime, delayed=[], interrupts=interrupts,
        ))

        self.assertEqual(sorted(interrupts[0].targets), ["exec_first", "exec_second"])


class GuidanceMailboxTests(unittest.TestCase):
    def test_final_drain_and_offer_cannot_lose_or_duplicate_guidance(self) -> None:
        for _ in range(100):
            mailbox = GuidanceMailbox(maxsize=8)
            barrier = threading.Barrier(3)
            offered: list[tuple[bool, int]] = []
            drained: list[str] = []

            def offer() -> None:
                barrier.wait()
                offered.append(mailbox.offer("late guidance"))

            def close_boundary() -> None:
                barrier.wait()
                drained.extend(mailbox.drain_or_close())

            offer_thread = threading.Thread(target=offer)
            close_thread = threading.Thread(target=close_boundary)
            offer_thread.start()
            close_thread.start()
            barrier.wait()
            offer_thread.join(timeout=1)
            close_thread.join(timeout=1)

            self.assertFalse(offer_thread.is_alive())
            self.assertFalse(close_thread.is_alive())
            self.assertEqual(len(offered), 1)
            accepted, _ = offered[0]
            if accepted:
                self.assertEqual(drained, ["late guidance"])
            else:
                self.assertEqual(drained, [])
                self.assertFalse(mailbox.accepting)

    def test_closed_mailbox_routes_all_later_guidance_to_next_turn(self) -> None:
        mailbox = GuidanceMailbox()
        self.assertEqual(mailbox.drain_or_close(), [])
        self.assertEqual(mailbox.offer("next turn"), (False, 0))
        self.assertEqual(mailbox.qsize(), 0)

    def test_structured_attachment_only_guidance_is_preserved(self) -> None:
        mailbox = GuidanceMailbox()
        guidance = GuidanceInput(
            id="guidance_media",
            uploaded_files=[{"asset_id": "asset_1", "name": "clip.mp4"}],
        )

        self.assertEqual(mailbox.offer(guidance), (True, 1))
        self.assertEqual(mailbox.drain(), [guidance])
        self.assertEqual(guidance.display_text, "附件引导：clip.mp4")

    def test_guidance_dictionary_keeps_mode_and_falls_back_for_invalid_values(self) -> None:
        for fields, expected in (
            ({"mode": "background"}, "background"),
            ({"mode": "interrupt"}, "interrupt"),
            ({}, "interrupt"),
            ({"mode": "unknown"}, "interrupt"),
            ({"mode": []}, "interrupt"),
        ):
            with self.subTest(fields=fields):
                mailbox = GuidanceMailbox()
                mailbox.offer({"guidance_id": "g", "guidance": " update ", **fields})
                self.assertEqual(mailbox.drain(), [GuidanceInput(id="g", text="update", mode=expected)])

    def test_background_guidance_does_not_bypass_mailbox_capacity(self) -> None:
        mailbox = GuidanceMailbox(maxsize=1)
        self.assertEqual(mailbox.offer({"text": "later", "mode": "background"}), (True, 1))
        with self.assertRaises(queue.Full):
            mailbox.offer("now")
        self.assertEqual(mailbox.drain()[0].mode, "background")
        self.assertEqual(mailbox.offer("now"), (True, 1))


if __name__ == "__main__":
    unittest.main()
