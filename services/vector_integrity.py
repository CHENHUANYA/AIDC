from __future__ import annotations

from typing import Any, Iterable

import numpy as np


class VectorIntegrityError(ValueError):
    pass


def inspect_vector_sample(
    vectors: Iterable[Any],
    *,
    expected_count: int | None = None,
    label: str = "vector snapshot",
) -> dict[str, Any]:
    arrays = [np.asarray(vector, dtype=float) for vector in vectors]
    if expected_count is not None and len(arrays) != expected_count:
        raise VectorIntegrityError(f"{label} sample is incomplete: {len(arrays)}/{expected_count}")
    if not arrays:
        raise VectorIntegrityError(f"{label} sample is empty")
    dimensions = {int(array.size) for array in arrays}
    if len(dimensions) != 1 or next(iter(dimensions)) < 1:
        raise VectorIntegrityError(f"{label} vectors have inconsistent or empty dimensions")
    norms = [float(np.linalg.norm(vector)) for vector in arrays]
    if not all(np.isfinite(norm) and norm > 1e-8 for norm in norms):
        raise VectorIntegrityError(f"{label} contains a zero or non-finite vector")
    distinct = not (len(arrays) > 1 and all(np.allclose(arrays[0], vector) for vector in arrays[1:]))
    if len(arrays) > 1 and not distinct:
        raise VectorIntegrityError(f"{label} sampled vectors are identical")
    return {
        "sample_count": len(arrays),
        "dimension": next(iter(dimensions)),
        "minimum_norm": round(min(norms), 6),
        "maximum_norm": round(max(norms), 6),
        "distinct": distinct,
    }
