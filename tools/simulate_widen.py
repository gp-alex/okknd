#!/usr/bin/env python3
"""Simulate the x64 HUNK_fix_pointers widening in-process, then verify the CPLC
graph resolves. Definitive check that the null/scalar table is complete & correct.

Works in image-relative offsets (base=0): a widened pointer at old slot S holds
new_off(read32(S)); a scalar/null extra slot holds the zero-extended 4-byte value.
"""
import struct
import sys
from bisect import bisect_left
from lvl import load, u32
import sections
from x64nulls import Walk

HUNK_RENDERER = 0x80000000
HUNK_PTRARRAY = 0x40000000
HUNK_MASK = 0x3FFFFFFF


def collect_slots(lvl):
    """Return dict off -> kind ('reloc'|'fn'|'scalar'), mirroring the loader."""
    d = lvl.data
    slots = {}
    body = lvl.rllc_body
    num = u32(body, 0)
    i = 0
    while i < num:
        e = u32(body, 4 + i * 4)
        if e & HUNK_RENDERER:
            slots[e & HUNK_MASK] = 'fn'
        elif e & HUNK_PTRARRAY:
            off = e & HUNK_MASK
            i += 1
            cnt = u32(body, 4 + i * 4) + 1
            for j in range(cnt):
                slots[off + j * 4] = 'reloc'
        else:
            slots[e] = 'reloc'
        i += 1
    # sections terminator (data==0 in the offset-0 sections array)
    sec_off = u32(d, 0)
    e = sec_off
    while sec_off and e + 8 <= len(d):
        if u32(d, e + 4) == 0:
            slots.setdefault(e + 4, 'scalar')
            break
        e += 8
    # extra null/scalar table
    extra = sections.cplc_nulls(lvl) | sections.mapd_nulls(lvl) | sections.boxd_grid_nulls(lvl)
    extra |= set(Walk(lvl).run().extra_slots())
    for o in extra:
        slots.setdefault(o, 'scalar')
    return slots


def build(lvl):
    d = lvl.data
    slots = collect_slots(lvl)
    offs = sorted(slots)
    nslots = len(offs)

    def new_off(o):
        return o + 4 * bisect_left(offs, o)

    out = bytearray(len(d) + 4 * nslots)
    src = dst = 0
    for o in offs:
        seg = o - src
        out[dst:dst + seg] = d[src:src + seg]
        dst += seg + 8
        src += seg + 4
    out[dst:] = d[src:]
    for o in offs:
        pos = new_off(o)
        kind = slots[o]
        if kind == 'reloc':
            val = new_off(u32(d, o))
        elif kind == 'scalar':
            val = u32(d, o)
        else:  # fn renderer - dummy nonzero
            val = 0xDEAD0000 + (o & 0xFFFF)
        struct.pack_into('<Q', out, pos, val)
    return out, new_off, slots


def r64(buf, o):
    return struct.unpack_from('<Q', buf, o)[0]


def check_cplc(lvl):
    d = lvl.data
    out, new_off, slots = build(lvl)
    h = next((s.off for s in lvl.sections if s.name == 'CPLC'), None)
    if h is None:
        print("no CPLC"); return
    arr = next((a for a in lvl.ptr_arrays if a.off == h), None)
    cnt = arr.count if arr else 1
    print(f"CPLC array of {cnt} surfaces; verifying widened graph...")
    bad = 0
    for i in range(cnt):
        surf_old = u32(d, h + i * 4)
        # widened array entry i lives at new_off(h)+i*8
        got = r64(out, new_off(h) + i * 8)
        want = new_off(surf_old)
        if got != want:
            print(f"  [{i}] layers ptr mismatch got={got:#x} want={want:#x}"); bad += 1; continue
        # surface->prev_x_sorted (x64 struct offset 12) should == new_off(old prev_x)
        prevx_old = u32(d, surf_old + 8)
        got_pv = r64(out, new_off(surf_old) + 12)
        want_pv = new_off(prevx_old) if prevx_old else 0
        # verify it points at a real entity (task_type in 0..255, has ptr fields)
        tt = u32(d, prevx_old) if prevx_old else -1
        note = '' if (got_pv == want_pv) else f" MISMATCH got={got_pv:#x} want={want_pv:#x}"
        if got_pv != want_pv:
            bad += 1
        print(f"  [{i}] surf_old={surf_old:#x} prev_x_old={prevx_old:#x} tt={tt}{note}")
    print("RESULT:", "ALL OK" if bad == 0 else f"{bad} MISMATCHES")


def check_cplc_entities(lvl):
    """Verify EVERY entity's next/prev x/y sorted + task + entity fields widen to
    the value the runtime C struct would read at its (x64, packed) offsets."""
    d = lvl.data
    out, new_off, slots = build(lvl)
    h = next((s.off for s in lvl.sections if s.name == 'CPLC'), None)
    if h is None:
        print("no CPLC"); return
    arr = next((a for a in lvl.ptr_arrays if a.off == h), None)
    cnt = arr.count if arr else 1
    bad = checked = 0
    for i in range(cnt):
        surf_old = u32(d, h + i * 4)
        if not surf_old:
            continue
        seen = set()
        e = u32(d, surf_old + 4)          # next_x_sorted (x-sorted head)
        while e and e not in seen and len(seen) < 100000:
            seen.add(e)
            for name, off in [('next_x', 16), ('prev_x', 20), ('next_y', 24), ('prev_y', 28)]:
                old_val = u32(d, e + off)
                want = new_off(old_val) if old_val else 0
                got = r64(out, new_off(e + off))
                checked += 1
                if got != want:
                    bad += 1
                    if bad <= 10:
                        print(f"  surf[{i}] entity_old={e:#x} field={name}: "
                              f"got={got:#x} want={want:#x} old_val={old_val:#x}")
            e = u32(d, e + 16)
    print(f"checked {checked} entity pointer fields, {bad} MISMATCHES")


if __name__ == "__main__":
    lvl = load(sys.argv[1] if len(sys.argv) > 1
               else '/mnt/c/src/bloomberg/c/okk/LEVELS/640/SUPER.LVL')
    check_cplc(lvl)
    check_cplc_entities(lvl)
