#!/usr/bin/env python3
"""Test the 'only trailing array-terminator nulls escape the RLLC' hypothesis.

For every relocated pointer ARRAY, the game treats frames[]/anim tables as
NULL-terminated. If the hypothesis holds, the single word right after each array
is a raw-0 slot that is NOT in the RLLC (the terminator), and there are no other
kinds of escaped null pointer.

We also probe MobdAnimFrame singleton pointers (sprt@+12, shape@+16): frames are
the targets of the frame-pointer arrays, so we can find them and check whether
any hold a raw 0 that is absent from the RLLC (which would BREAK the hypothesis).
"""
import sys
from lvl import load, u32

# MobdAnimFrame packed layout (on-disk, 32-bit ptrs):
#   x@0 y@4 flags@8 sprt@12 shape@16 sound_id@20 points@24 (16 bytes each)
FRAME_SPRT = 12
FRAME_SHAPE = 16


def analyze(lvl):
    print(f"\n=== {lvl.path} ===")
    data, slots = lvl.data, lvl.ptr_slots
    n = len(data)

    def raw(o):
        return u32(data, o) if o + 4 <= n else None

    # --- word immediately after each pointer array ---
    after_zero_not_rllc = 0
    after_in_rllc = 0
    after_nonzero_not_rllc = 0
    after_oob = 0
    trailing_null_offs = []
    for a in lvl.ptr_arrays:
        w = a.off + a.count * 4          # one past the last relocated slot
        if w + 4 > n:
            after_oob += 1
            continue
        if w in slots:
            after_in_rllc += 1
        elif raw(w) == 0:
            after_zero_not_rllc += 1
            trailing_null_offs.append(w)
        else:
            after_nonzero_not_rllc += 1
    print(f"ptr arrays: {len(lvl.ptr_arrays)}")
    print(f"  word after array -> in_rllc={after_in_rllc}  "
          f"ZERO&not_rllc(terminator)={after_zero_not_rllc}  "
          f"nonzero&not_rllc={after_nonzero_not_rllc}  oob={after_oob}")

    # --- do any relocated arrays already CONTAIN an interior raw-0? (nulls in RLLC) ---
    interior_zeros = 0
    for a in lvl.ptr_arrays:
        for j in range(a.count):
            if raw(a.off + j * 4) == 0:
                interior_zeros += 1
    print(f"  interior raw-0 slots inside arrays (nulls already relocated) = {interior_zeros}")

    # --- singleton nullable pointer probe: frames reached via frame arrays ---
    # Heuristic: a pointer array whose elements all point at 8-aligned targets
    # that look like MobdAnimFrame (sprt/shape slots are themselves in RLLC or 0).
    # We instead directly scan EVERY relocated pointer target and, if it looks like
    # a frame (its sprt/shape offsets are pointer-typed), check for escaped nulls.
    # A genuine MobdAnimFrame is the pointee of some relocated pointer AND has at
    # least one of sprt(+12)/shape(+16) actually relocated (proving those offsets
    # really are pointer fields for this struct, not coincidence). Dedup by offset.
    ptr_targets = {raw(o) for o in slots if raw(o) is not None}
    frames = set()
    for t in ptr_targets:
        if t + FRAME_SHAPE + 4 > n:
            continue
        sp, sh = t + FRAME_SPRT, t + FRAME_SHAPE
        if (sp in slots or sh in slots):        # at least one real ptr field
            frames.add(t)
    escaped_sprt = escaped_shape = 0
    for t in frames:
        sp, sh = t + FRAME_SPRT, t + FRAME_SHAPE
        if sp not in slots and raw(sp) == 0:
            escaped_sprt += 1
        if sh not in slots and raw(sh) == 0:
            escaped_shape += 1
    print(f"  genuine frames: {len(frames)}   "
          f"escaped null sprt={escaped_sprt}  escaped null shape={escaped_shape}")
    return trailing_null_offs


if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyze(load(p))
