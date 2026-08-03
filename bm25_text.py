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
    ("換刀", ("tool", "change", "magazine", "clamp", "confirmation", "pocket", "sensor", "home", "switch", "automatic")),
    ("换刀", ("tool", "change", "magazine", "clamp", "confirmation", "pocket", "sensor", "home", "switch", "automatic")),
    ("刀具更換", ("tool", "change", "magazine", "clamp", "confirmation", "pocket", "sensor", "home", "switch", "automatic")),
    ("刀具更换", ("tool", "change", "magazine", "clamp", "confirmation", "pocket", "sensor", "home", "switch", "automatic")),
    ("無法啟動", ("start", "disable")),
    ("无法启动", ("start", "disable")),
    ("不能啟動", ("start", "disable")),
    ("不能启动", ("start", "disable")),
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

_RETRIEVAL_PHRASE_EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("換刀", ("tool change", "tool magazine", "tool clamp", "clamp confirmation", "pocket sensor", "magazine home", "automatic tool change")),
    ("换刀", ("tool change", "tool magazine", "tool clamp", "clamp confirmation", "pocket sensor", "magazine home", "automatic tool change")),
    ("刀具更換", ("tool change", "tool magazine", "tool clamp", "clamp confirmation", "pocket sensor", "magazine home", "automatic tool change")),
    ("刀具更换", ("tool change", "tool magazine", "tool clamp", "clamp confirmation", "pocket sensor", "magazine home", "automatic tool change")),
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


def expand_query_with_domain_aliases(value: str) -> str:
    """Append matched English domain aliases for embedding/vector retrieval."""
    original = str(value or "")
    normalized = unicodedata.normalize("NFKC", original).casefold()
    additions: list[str] = []
    seen: set[str] = set()
    for phrase, aliases in _DOMAIN_ALIASES:
        if phrase not in normalized:
            continue
        for alias in aliases:
            if alias in seen:
                continue
            seen.add(alias)
            additions.append(alias)
    for phrase, expansions in _RETRIEVAL_PHRASE_EXPANSIONS:
        if phrase not in normalized:
            continue
        for expansion in expansions:
            if expansion in seen:
                continue
            seen.add(expansion)
            additions.append(expansion)
    return f"{original} {' '.join(additions)}".strip()
