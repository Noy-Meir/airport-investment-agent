"""
Robust z-score normalization within a peer group (CLAUDE.md: numbers come
from deterministic code, no imputation).

z = (x - median) / (1.4826 * MAD), clipped to [-3, 3].
- MAD == 0 -> z = 0 for every member, with a caveat (the group has no
  spread on this signal, not that every airport is "average").
- Fewer than MIN_PEER_GROUP_SIZE non-NULL values -> no z-scores at all,
  with a "peer group too small" caveat -- a robust spread estimate isn't
  meaningful on a handful of points.
- Airports with a NULL signal value are excluded from the computation and
  from the result (never imputed to the peer median or to 0).
"""

import statistics

from src.reference.envelope import envelope

MIN_PEER_GROUP_SIZE = 5
Z_CLIP = 3.0
MAD_SCALE = 1.4826


def robust_z_scores(values_by_code):
    """
    values_by_code: {code: float|None}.

    Returns the uniform envelope; result is {code: z} for every code with a
    non-None input value, or None if fewer than MIN_PEER_GROUP_SIZE values
    are present.
    """
    method = f"robust z = (x - median) / ({MAD_SCALE} * MAD) within peer group, clipped to [-{Z_CLIP:g}, {Z_CLIP:g}]"
    source = "computed in-process from signal inputs (no external fetch)"

    present = {code: v for code, v in values_by_code.items() if v is not None}
    n_missing = len(values_by_code) - len(present)
    caveats = []
    if n_missing:
        caveats.append(f"excluded {n_missing} peer(s) with no value for this signal from the z-score computation")

    if len(present) < MIN_PEER_GROUP_SIZE:
        caveats.append(
            f"peer group too small ({len(present)} value(s) with data, need >= {MIN_PEER_GROUP_SIZE}) "
            "-- no z-scores computed"
        )
        return envelope(None, method, caveats, source, "low")

    vals = list(present.values())
    median = statistics.median(vals)
    mad = statistics.median(abs(v - median) for v in vals)

    if mad == 0:
        caveats.append(
            "MAD is 0 for this peer group (no spread on this signal, or too many identical values) "
            "-- z=0 for all members"
        )
        return envelope({code: 0.0 for code in present}, method, caveats, source, "medium")

    z_scores = {}
    n_clipped = 0
    for code, v in present.items():
        z = (v - median) / (MAD_SCALE * mad)
        clipped = max(-Z_CLIP, min(Z_CLIP, z))
        if clipped != z:
            n_clipped += 1
        z_scores[code] = round(clipped, 4)
    if n_clipped:
        caveats.append(f"clipped {n_clipped} outlier value(s) to [-{Z_CLIP:g}, {Z_CLIP:g}]")

    return envelope(z_scores, method, caveats, source, "high")
