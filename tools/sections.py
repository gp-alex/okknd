#!/usr/bin/env python3
"""Escaped-null enumeration for the non-MOBD level sections: CPLC, MAPD, BOXD.

These sections live in per-mission .LVL files (their MOBD is substituted by
sprites/supspr at runtime). Like MOBD, nullable pointer fields that hold 0 are
absent from the RLLC and must be widened to 8-byte NULL on x64.

Confidently enumerable sources (from the packed schemas in kknd.h):
  CPLC  LevelCplc{layers} -> LevelCplcSurface{size,next/prev x/y sorted}
        -> CplcEntity{task_type,x,y,z, next/prev x/y sorted (4 ptrs),
                      CplcSpawnParams{...,Entity* entity,...}}.
        Escaped: each entity's spawn_params.entity (runtime ptr, NULL on disk)
        + the 4 list-terminator nulls at the ends of the x/y sorted lists.
  MAPD  LevelMapd{layers} -> LevelMapdSurface{num_images, MapdScrlImage* images[
        num_images], num_palette, palette[]} ; MapdScrlImage{renderer, tsx,tsy,
        nx,ny, MapdScrlImageTile* tiles[16]}. Escaped: null tiles[16] slots.
  BOXD  LevelBoxd{grid[]} -> BoxdGrid{buckets,w2tx,w2ty,nx,ny, BoxdAabb* tiles[
        nx*ny]}. Escaped: null grid cells (empty tiles). NOTE: the per-cell
        collision CHAIN also has null 'next' terminators (~60/level) that this
        does NOT yet enumerate - see residual_zeros().
"""
import struct
import sys
from lvl import load, u32


def s32(b, o):
    return struct.unpack_from("<i", b, o)[0]


def _holder(lvl, name):
    s = next((s for s in lvl.sections if s.name == name), None)
    return s.off if s else None


def cplc_nulls(lvl):
    d, sl, n = lvl.data, lvl.ptr_slots, len(lvl.data)
    h = _holder(lvl, "CPLC")
    esc = set()
    if h is None:
        return esc
    # CPLC section is an ARRAY of LevelCplc{layers} - one surface per menu id
    # (CPLC_select(id) picks g_current_lvl_cplc[id]). Walk EVERY surface, not just
    # [0]; the array length is the RLLC pointer-array count (1 for missions).
    arr = next((a for a in lvl.ptr_arrays if a.off == h), None)
    count = arr.count if arr else 1
    for i in range(count):
        surf = u32(d, h + i * 4)         # LevelCplc[i].layers -> LevelCplcSurface
        if 0 < surf < n:
            _cplc_walk_surface(d, sl, n, surf, esc)
    return esc


def _cplc_walk_surface(d, sl, n, surf, esc):
    head = u32(d, surf + 4)             # next_x_sorted
    e = head
    seen = set()
    while 0 < e < n and e not in seen and len(seen) < 100000:
        seen.add(e)
        for k in range(4):              # next/prev x/y sorted list ptrs @ +16..+28
            o = e + 16 + k * 4
            if o + 4 <= n and o not in sl and u32(d, o) == 0:
                esc.add(o)               # null list terminator
        # spawn_params @+32: TaskFn task @+4 is pointer-sized (8B on x64) but holds a
        # dead scalar (0-5) on disk -> ALWAYS widen (value-preserving). Entity* entity
        # @+16 is a real pointer, NULL on disk -> widen.
        task = e + 32 + 4
        if task + 4 <= n and task not in sl:
            esc.add(task)
        ent = e + 32 + 16
        if ent + 4 <= n and ent not in sl and u32(d, ent) == 0:
            esc.add(ent)
        e = u32(d, e + 16)             # advance next_x_sorted


def _array_count(lvl, h):
    """Holder is an array of section pointers (one per menu id); RLLC gives its
    length (1 for missions)."""
    a = next((x for x in lvl.ptr_arrays if x.off == h), None)
    return a.count if a else 1


def mapd_nulls(lvl):
    d, sl, n = lvl.data, lvl.ptr_slots, len(lvl.data)
    h = _holder(lvl, "MAPD")
    esc = set()
    if h is None:
        return esc
    for i in range(_array_count(lvl, h)):    # LevelMapd[i].layers -> LevelMapdSurface
        surf = u32(d, h + i * 4)
        if not (0 < surf < n):
            continue
        num_images = u32(d, surf)
        if not (0 < num_images < 64):
            continue
        for j in range(num_images):
            img = u32(d, surf + 4 + j * 4)   # images[j] pointer
            if not (0 < img < n):
                continue
            # MapdScrlImage: renderer@0, tsx@4,tsy@8,nx@12,ny@16, tiles[16]@20
            for t in range(16):
                o = img + 20 + t * 4
                if o + 4 <= n and o not in sl and u32(d, o) == 0:
                    esc.add(o)
    return esc


def boxd_grid_nulls(lvl):
    d, sl, n = lvl.data, lvl.ptr_slots, len(lvl.data)
    h = _holder(lvl, "BOXD")
    esc = set()
    if h is None:
        return esc
    for i in range(_array_count(lvl, h)):    # LevelBoxd[i]/grid[i] -> BoxdGrid
        grid = u32(d, h + i * 4)
        if not (0 < grid < n):
            continue
        nx, ny = u32(d, grid + 12), u32(d, grid + 16)
        if not (0 < nx * ny < 1_000_000):
            continue
        tbase = grid + 20
        for c in range(nx * ny):
            o = tbase + c * 4
            if o + 4 <= n and o not in sl and u32(d, o) == 0:
                esc.add(o)                    # empty grid cell (null BoxdAabb*)
    return esc


def residual_zeros(lvl, covered):
    """Estimate escaped nulls NOT yet covered: raw-0 words that sit as a pointer
    field among RLLC pointers - i.e. the 3rd field of a {ptr,ptr,0} chain node
    (RLLC ptr at O-8 and O-4) or an array hole (RLLC ptr at O-4 and O+4). Used to
    gauge the BOXD collision-chain terminators still missing."""
    d, sl, n = lvl.data, lvl.ptr_slots, len(lvl.data)
    res = set()
    for o in sl:                        # scan near known pointers only (cheap)
        for cand in (o + 4, o + 8, o + 12):
            if cand in sl or cand in covered or cand + 4 > n:
                continue
            if u32(d, cand) != 0:
                continue
            # chain-node 3rd field: ptr at cand-8 and cand-4
            node = (cand - 8) in sl and (cand - 4) in sl
            # array hole: ptr at cand-4 and cand+4
            hole = (cand - 4) in sl and (cand + 4) in sl
            if node or hole:
                res.add(cand)
    return res


def all_nulls(lvl):
    return cplc_nulls(lvl) | mapd_nulls(lvl) | boxd_grid_nulls(lvl)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        lvl = load(p)
        c, m, b = cplc_nulls(lvl), mapd_nulls(lvl), boxd_grid_nulls(lvl)
        covered = c | m | b
        res = residual_zeros(lvl, covered)
        print(f"{p.split('/')[-1]:16} CPLC={len(c):5} MAPD={len(m):4} "
              f"BOXD_grid={len(b):5}  residual(chain?)~{len(res)}")
