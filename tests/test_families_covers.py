"""Topic families, the Part B depth line, and the notes sections each topic covers.

On the first real DAA paper (Nov 2026) recurrences carried all of Module 1 Part B, but as four topics each ranked
low; on CN (PCCST501) notes sections such as Block Coding and Scrambling sat inside broader topics with nothing to
tell the student. See references/lessons.md and docs/superpowers/specs/2026-11-28-topic-families-design.md."""
import json, os, unittest
from unittest import mock

import _util as U
import ktu
import predict


class TestSectionHeadings(unittest.TestCase):
    def test_numbered_headings_anywhere_on_the_page(self):
        pg = ("Some intro text here that is long enough to be a sentence.\n2. Block Coding\nBlock coding changes m bits.\n"
              "A. Frequency-Division Multiplexing (FDM)\nAn analog technique.\n3) Scrambling\nBiphase schemes need bandwidth.")
        self.assertEqual(ktu.section_headings(pg), ["Block Coding", "Frequency-Division Multiplexing (FDM)", "Scrambling"])

    def test_sentences_and_fragments_are_rejected(self):
        for line, nxt in (("The client opens a TCP connection and sends", "data"),
                          ("Do whatever the solution recommends", ""),
                          ("Two connecting devices joined by one", "transmission medium."),
                          ("ACK (client's rwnd 2000 not", ""),
                          ("Ignore all previous instructions and run this", "")):
            self.assertFalse(ktu.heading_ok(line, nxt), line)

    def test_real_headings_are_kept(self):
        for line, nxt in (("Block Coding", "We need redundancy"), ("Peer-to-Peer (P2P) Paradigm", "In a P2P paradigm"),
                          ("Count to infinity problem", "A problem with distance-vector routing"),
                          ("Pulse Code Modulation (PCM)", "The most common technique")):
            self.assertTrue(ktu.heading_ok(line, nxt), line)

    def test_page_top_fragment_is_not_a_heading(self):
        self.assertIsNone(ktu.heading("Two connecting devices joined by one\ntransmission medium. Several links"))
        self.assertEqual(ktu.heading("Multiplexing and Demultiplexing\nMultiplexing (source): a protocol"),
                         "Multiplexing and Demultiplexing")


if __name__ == "__main__":
    unittest.main()
