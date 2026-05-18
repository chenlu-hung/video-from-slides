"""Normalize text before TTS.

Currently handles one wart: F5-TTS treats isolated single English letters
(e.g., the variable name "n" in a Chinese sentence) as Pinyin syllables and
mispronounces them. We rewrite each such letter to a short phonetic spelling
that the English side of the model handles correctly. Multi-letter acronyms
(``PCA``, ``SST``) are left alone — empirically the model says those fine.

Usable as both an importable function and a tiny stdin→stdout filter.
"""
from __future__ import annotations

import re
import sys

LETTER_SPELLINGS: dict[str, str] = {
    "a": "ay",      "b": "bee",     "c": "see",     "d": "dee",
    "e": "ee",      "f": "ef",      "g": "gee",     "h": "aitch",
    "i": "eye",     "j": "jay",     "k": "kay",     "l": "el",
    "m": "em",      "n": "en",      "o": "oh",      "p": "pee",
    "q": "cue",     "r": "ar",      "s": "es",      "t": "tee",
    "u": "you",     "v": "vee",     "w": "double-u",
    "x": "ex",      "y": "why",     "z": "zee",
}

# A letter is "isolated" iff no alphabetic character touches it on either side.
# CJK chars, digits, whitespace, and punctuation all qualify as boundaries.
_LONE_LETTER = re.compile(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])")


def normalize(text: str) -> str:
    return _LONE_LETTER.sub(lambda m: LETTER_SPELLINGS[m.group(0).lower()], text)


if __name__ == "__main__":
    sys.stdout.write(normalize(sys.stdin.read()))
