#!/usr/bin/env python3
"""Structural walker for the MOBD blob inside a KKND .LVL container.

Type graph (all packed, on-disk 32-bit pointers; offsets are into the DATA body):

  MOBD section start = surface table: array of LevelMobdSurface* (one per mobd_id,
                        sparse - unused ids are NULL). It is an RLLC pointer array.
  LevelMobdSurface   = raw base; animations/anim-tables live at code-known offsets
                        RELATIVE to this base (the `anim` arg of ENT_anim_*).
  ENT_anim_set(off)        -> off is a MobdAnimation.
  ENT_anim_set_frame(off,d)-> off is an array of MobdAnimation* (index = angle/dir);
                              array length (# angles) == its RLLC array count.
  MobdAnimation      = { int anim_speed; MobdAnimFrame* frames[] (NULL-terminated) }
  MobdAnimFrame(24+) = { int x, y, flags; MobdSprtImage* sprt; BoxdCollisionShape* shape;
                         int sound_id; MobdPoint points[] }
  MobdPoint(16)      = { int id, x, y, z }        (anchors: turret/rally/muzzle/...)
  MobdSprtImage(12)  = { Blitter blitter(renderer); int flags; MobdImageData* bitmap }
  MobdImageData(9+)  = { int width, height; u8 format; u8 pixels[] }   (packed, no pad)
  BoxdCollisionShape = { BoxdAabb* box }
"""
import struct
import sys
from lvl import load, u32

FRAME_HDR = 24            # bytes before points[]
FRAME_SPRT = 12
FRAME_SHAPE = 16
POINT_SZ = 16
SPRT_SZ = 12


def s32(buf, off):
    return struct.unpack_from("<i", buf, off)[0]


class Mobd:
    def __init__(self, lvl):
        self.lvl = lvl
        self.data = lvl.data
        self.slots = lvl.ptr_slots
        span = lvl.section_span("MOBD")
        if not span:
            raise ValueError("no MOBD section")
        self.mobd_off, self.mobd_end = span
        # surface table = the RLLC pointer array that starts at the MOBD section.
        self.surface_array = next(
            (a for a in lvl.ptr_arrays if a.off == self.mobd_off), None)

    # --- surface table -------------------------------------------------------
    def num_surfaces(self):
        return self.surface_array.count if self.surface_array else 0

    def surface_base(self, mobd_id):
        """Absolute DATA offset of layers[0] for a mobd_id, or None if NULL/absent."""
        if not self.surface_array or mobd_id >= self.surface_array.count:
            return None
        slot = self.surface_array.off + mobd_id * 4
        v = u32(self.data, slot)
        return v if v != 0 else None            # raw stored offset (== base pre-reloc)

    # --- animation decode ----------------------------------------------------
    def frame_at(self, abs_off):
        d = self.data
        sprt = u32(d, abs_off + FRAME_SPRT)
        shape = u32(d, abs_off + FRAME_SHAPE)
        # points[] runs until the next frame/anim; we bound it by walking ids until
        # a slot that is itself a known pointer target boundary. For metadata we
        # read points while id looks like a small anchor id (< 0x10000) and coords
        # are plausible; callers cross-check against the following struct.
        pts = []
        p = abs_off + FRAME_HDR
        while p + POINT_SZ <= len(d):
            pid = s32(d, p)
            if pid < 0 or pid > 0xFFFF:
                break
            pts.append((pid, s32(d, p + 4), s32(d, p + 8), s32(d, p + 12)))
            p += POINT_SZ
            if pid == 0:                        # id 0 = default/terminator anchor
                break
        return {
            "off": abs_off,
            "x": s32(d, abs_off), "y": s32(d, abs_off + 4),
            "flags": u32(d, abs_off + 8),
            "sprt_off": abs_off + FRAME_SPRT, "sprt": sprt,
            "shape_off": abs_off + FRAME_SHAPE, "shape": shape,
            "sound_id": s32(d, abs_off + 20),
            "points": pts,
        }

    def animation_at(self, abs_off):
        """Decode a MobdAnimation at absolute offset. frames[] is NULL-terminated."""
        d = self.data
        anim_speed = u32(d, abs_off)
        frames = []
        fp = abs_off + 4
        term_off = None
        while fp + 4 <= len(d):
            v = u32(d, fp)
            if v == 0:
                term_off = fp
                break
            frames.append(v)                    # stored offset to a MobdAnimFrame
            fp += 4
        return {"off": abs_off, "anim_speed": anim_speed,
                "frame_offs": frames, "term_off": term_off}

    def anim_table(self, surface_base, offset):
        """ENT_anim_set_frame: array of MobdAnimation* at surface_base+offset.
        length (# angles) taken from the RLLC array count when present."""
        abs_off = surface_base + offset
        arr = next((a for a in self.lvl.ptr_arrays if a.off == abs_off), None)
        n = arr.count if arr else 0
        entries = [u32(self.data, abs_off + i * 4) for i in range(n)]
        return {"off": abs_off, "num_angles": n, "anim_offs": entries,
                "has_rllc_array": arr is not None}


def dump_surface(m, mobd_id):
    base = m.surface_base(mobd_id)
    print(f"mobd_id {mobd_id}: surface_base={base}")
    if base is None:
        return


if __name__ == "__main__":
    path = sys.argv[1]
    m = Mobd(load(path))
    print(f"MOBD @{m.mobd_off:#x}..{m.mobd_end:#x}  surfaces={m.num_surfaces()}")
    nonnull = [i for i in range(m.num_surfaces()) if m.surface_base(i) is not None]
    print(f"non-null surfaces: {len(nonnull)}  (null: {m.num_surfaces()-len(nonnull)})")
    print("first non-null ids:", nonnull[:20])
