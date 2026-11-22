"""What the arm's pen can write. Mirrored from sim/arm_worker.py at ea3a5c6 (clean() and its limits),
so the card side never writes a job the arm would refuse."""
import unicodedata

# The arm swaps these, drops accents, and then refuses anything that is not plain printable ASCII.
TYPOGRAPHY = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
              "…": "...", " ": " ", "~": "-"}

# Quote mode (ARM_STATUS.md, 22 Nov 13:43): a stranger's card is written as a short quote only.
MAX_QUOTE_WORDS, MAX_QUOTE_CHARS = 3, 24


def without_end_marks(text):
    """Tiny lifted marks are the pen's hardest move, so a quote is inked as its words only."""
    return " ".join(w for w in (w.rstrip("!?.,;:") for w in text.split()) if w)


def pen_text(text):
    """The text as the arm's pen font will see it, and the characters it has no glyph for."""
    for a, b in TYPOGRAPHY.items():
        text = (text or "").replace(a, b)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = " ".join(text.split())
    return text, sorted({c for c in text if c != " " and not 32 < ord(c) < 126})
