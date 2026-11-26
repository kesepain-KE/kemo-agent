from __future__ import annotations

import queue
import threading
import unittest

from run.conversation import GuidanceInput, GuidanceMailbox


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
