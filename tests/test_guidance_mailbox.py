from __future__ import annotations

import threading
import unittest

from run.guidance import GuidanceMailbox


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


if __name__ == "__main__":
    unittest.main()
