from __future__ import annotations

import re
import unicodedata


BM25_TOKENIZER_VERSION = "unicode-domain-v1"

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_CJK_SEQUENCE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

# Keep this list deliberately small and auditable. These aliases bridge common
# shop-floor Chinese queries to the English terminology in the current manuals
# and mock maintenance knowledge; they are not a general translation system.
_DOMAIN_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("冷卻液", ("coolant",)),
    ("冷却液", ("coolant",)),
    ("幫浦", ("pump",)),
    ("泵浦", ("pump",)),
    ("水泵", ("pump",)),
    ("壓力", ("pressure",)),
    ("压力", ("pressure",)),
    ("過低", ("low",)),
    ("过低", ("low",)),
    ("不足", ("low",)),
    ("訊號消失", ("signal", "lost")),
    ("信號消失", ("signal", "lost")),
    ("信号消失", ("signal", "lost")),
    ("粗加工", ("roughing",)),
    ("噴嘴", ("nozzle",)),
    ("喷嘴", ("nozzle",)),
    ("堵塞", ("blockage",)),
    ("液壓", ("hydraulic",)),
    ("液压", ("hydraulic",)),
    ("夾具", ("clamp",)),
    ("夹具", ("clamp",)),
    ("刀庫", ("tool", "magazine")),
    ("刀库", ("tool", "magazine")),
    ("探針", ("probe",)),
    ("探针", ("probe",)),
    ("校正", ("calibration",)),
    ("驅動", ("drive",)),
    ("驱动", ("drive",)),
    ("加速度", ("acceleration",)),
    ("空壓", ("air", "pressure")),
    ("空压", ("air", "pressure")),
    ("調壓器", ("regulator",)),
    ("调压器", ("regulator",)),
)


def tokenize_bm25(value: str) -> list[str]:
    """Return deterministic Unicode tokens plus auditable domain aliases."""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    tokens = _ASCII_TOKEN_RE.findall(normalized)

    for match in _CJK_SEQUENCE_RE.finditer(normalized):
        sequence = match.group(0)
        tokens.append(sequence)
        if len(sequence) > 1:
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))

    for phrase, aliases in _DOMAIN_ALIASES:
        occurrences = normalized.count(phrase)
        for _ in range(occurrences):
            tokens.extend(aliases)
    return tokens
