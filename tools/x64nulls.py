#!/usr/bin/env python3
"""Complete escaped-null enumeration for the x64 pointer-widening loader.

The x64 HUNK_fix_pointers widens every 4-byte pointer slot 4->8 bytes. The RLLC
lists only NON-null pointers; a pointer field holding NULL is absent, so it would
not be widened and every following byte drifts. This walks the MOBD type graph
and reports every pointer FIELD that is raw 0 and NOT in the RLLC ("escaped
null"). {RLLC slots} + {escaped nulls} = the complete slot set to widen.

Type-directed worklist (see memory kknd-mobd-format). Classify a pointer TARGET
by kind; the kind + the fixed field-offset of the slot(s) pointing at it reveal
the CONTAINING struct's start and type, which we enqueue:

  sprt-image (renderer@0, 12B): flags@4, bitmap@8 -> image        pointed by frame.sprt@12 / glyph.img@12
  image (MobdImageData):        w@0 h@4 fmt@8 pixels@9 (no ptr)    pointed by sprt.bitmap@8
  frame (MobdAnimFrame):        x y flags sprt@12 shape@16 sound@20 points@24
                                                                   pointed by frames[]@k (frames[] array/single ptr)
  shape (BoxdCollisionShape):   box@0 -> BoxdAabb (no ptr)         pointed by frame.shape@16
  animation:                    anim_speed@0, frames[]@4 (NULL-term array|single) pointed by anim-table@k
  MobdPoint array (16B, id@0):  id may be a nested ptr; term id==-1 pointed by frame.points[].id
  font: FontMobd.glyphs[]@base+4 -> GlyphDesc(28B) ptrs @12/16/20/24
"""
import struct
import sys
from lvl import load, u32

FONT_IDS = {26, 27, 80}
SPRT_SZ = 12
FR_SPRT, FR_SHAPE, FR_SOUND, FR_POINTS = 12, 16, 20, 24
SPRT_BITMAP = 8
POINT_SZ = 16
GLYPH_PTRS = (12, 16, 20, 24)


def s32(b, o):
    return struct.unpack_from("<i", b, o)[0]


class Walk:
    def __init__(self, lvl):
        self.lvl = lvl
        self.d = lvl.data
        self.n = len(lvl.data)
        self.slots = lvl.ptr_slots
        self.renderers = set(lvl.renderers)
        self.arrays = lvl.ptr_arrays
        self.array_of = {}                  # slot offset -> containing PtrArray
        for a in lvl.ptr_arrays:
            for j in range(a.count):
                self.array_of[a.off + j * 4] = a
        self.refs = {}                      # target value -> [slot offsets pointing to it]
        for s in self.slots:
            v = u32(self.d, s)
            if v:
                self.refs.setdefault(v, []).append(s)
        self.kind = {}
        self.escaped = set()
        self.terminators = set()

    def raw(self, o):
        return u32(self.d, o) if 0 <= o and o + 4 <= self.n else None

    def _esc(self, off):
        if 0 <= off and off + 4 <= self.n and off not in self.slots and self.raw(off) == 0:
            self.escaped.add(off)

    def _term(self, off):
        # Array terminators are 0 (null-terminated: frames[]/anim-tables/shape chains)
        # OR -1 (0xffffffff, the MobdAnimation.frames[] end sentinel). Both must be
        # widened; the loader sign-extends so -1 becomes a full 8-byte -1 that the
        # code's `== (MobdAnimFrame*)-1` check matches.
        if 0 <= off and off + 4 <= self.n and off not in self.slots and self.raw(off) in (0, 0xFFFFFFFF):
            self.terminators.add(off)

    def _enq(self, off, k):
        if off is None or off < 0 or off in self.kind:
            return
        self.kind[off] = k
        self.work.append((off, k))

    def _walk_points(self, start):
        """Walk a MobdPoint array (16B, id@0). id may be a nested MobdPoint*
        (relocated slot); array terminates at id==-1. Bounded for safety."""
        p = start
        for _ in range(4096):
            if p + POINT_SZ > self.n:
                break
            if p in self.slots:                 # id is a nested MobdPoint*
                self._enq(self.raw(p), "points")
            if s32(self.d, p) == -1:
                break
            p += POINT_SZ

    def _frames_base(self, frame_off):
        """Given a frame, find the frames[]/single slot(s) pointing to it and, for
        each, the animation (anim_speed precedes frames[]) + array terminator."""
        for s in self.refs.get(frame_off, []):
            a = self.array_of.get(s)
            if a:
                base = a.off
                self._term(a.off + a.count * 4)     # frames[] NULL terminator
            else:
                base = s                             # single-frame anim: frames[0]
                self._term(s + 4)
            yield base - 4                           # animation offset

    def run(self):
        d, slots = self.d, self.slots
        mobd_off, mobd_end = self.lvl.section_span("MOBD")
        surf = self.array_of.get(mobd_off)
        surf_arr = next((a for a in self.arrays if a.off == mobd_off), None)
        surf_slots = set(range(surf_arr.off, surf_arr.off + surf_arr.count * 4, 4)) \
            if surf_arr else set()

        # surface ranges -> font partition
        ranges = {}
        if surf_arr:
            bases = sorted((u32(d, surf_arr.off + i * 4), i)
                           for i in range(surf_arr.count) if u32(d, surf_arr.off + i * 4))
            for k, (b, mid) in enumerate(bases):
                ranges[mid] = (b, bases[k + 1][0] if k + 1 < len(bases) else mobd_end)
        font_spans = [ranges[i] for i in FONT_IDS if i in ranges]

        def in_font(off):
            return any(b <= off < e for b, e in font_spans)

        # MOBD's real data (frames/anims/sprts) always lives AT the surface bases
        # and after (verified: mission files always lay out CPLC/MAPD/BOXD data at
        # LOW file offsets, with MOBD's own surface content at the HIGHEST offsets,
        # just past its own tiny surface-pointer table). Anything below the lowest
        # surface base belongs to an unrelated section - graph-chasing heuristics
        # below (esp. inline-anim seeding) must not wander into it, or a CPLC/MAPD/
        # BOXD pointer whose value coincidentally looks "frame-shaped" gets
        # misclassified as MOBD structure (confirmed bug: a CPLC entity with
        # next_x_sorted==0 and z==0 was picked up as a null-sprt frame).
        mobd_lo = bases[0][0] if bases else 0

        def in_mobd(off):
            return off is not None and off >= mobd_lo

        self.work = []               # (offset, kind)
        enq = self._enq

        def drain():
          while self.work:
            off, k = self.work.pop()
            if k == "sprt":
                if (off + SPRT_BITMAP) in slots:
                    enq(self.raw(off + SPRT_BITMAP), "image")
                else:
                    self._esc(off + SPRT_BITMAP)
                # slots pointing at this sprt are frame.sprt fields -> frames.
                for s in self.refs.get(off, []):
                    if not in_font(s) and in_mobd(s - FR_SPRT):
                        enq(s - FR_SPRT, "frame")
            elif k == "image":
                pass                                    # no pointers
            elif k == "shape":
                # BoxdCollisionShape is walked as a chain/array {BoxdAabb* box}
                # (stride 4 on disk), terminated by box==0. Widen each real box's
                # AABB, follow to the next element, and widen the box==0 terminator
                # so the x64 8-byte stride + the code's `!box` check stay correct.
                if off in slots:
                    enq(self.raw(off), "aabb")          # box -> BoxdAabb
                    enq(off + 4, "shape")               # next BoxdCollisionShape in chain
                elif self.raw(off) == 0:
                    self._esc(off)                      # box==0 chain terminator
            elif k == "aabb":
                pass
            elif k == "frame":
                # sprt@12 / shape@16 fields (escaped if null)
                self._esc(off + FR_SPRT)
                if (off + FR_SHAPE) in slots:
                    enq(self.raw(off + FR_SHAPE), "shape")
                else:
                    self._esc(off + FR_SHAPE)
                # NOTE: points[]@24 deliberately NOT walked. MobdPoint arrays hold no
                # escaped nulls (id is scalar or an RLLC-relocated nested ptr; a "null"
                # id is just scalar 0). Walking them mis-marked frames as points when a
                # frame's points region isn't -1 terminated. They stay benign/unclassified.
                # this frame reveals its animation(s).
                for anim in self._frames_base(off):
                    enq(anim, "anim")
            elif k == "anim":
                # frames[] at anim+4: array or single ptr, NULL-terminated.
                fb = off + 4
                a = self.array_of.get(fb)
                if a:
                    for j in range(a.count):
                        t = self.raw(a.off + j * 4)
                        if t and in_mobd(t):
                            enq(t, "frame")
                    self._term(a.off + a.count * 4)
                elif fb in slots and in_mobd(self.raw(fb)):
                    enq(self.raw(fb), "frame")
                    self._term(fb + 4)
                # anim-tables that reference this anim -> sibling anims.
                for s in self.refs.get(off, []):
                    a2 = self.array_of.get(s)
                    if a2 and not in_font(s):
                        for j in range(a2.count):
                            t = self.raw(a2.off + j * 4)
                            if t and in_mobd(t):
                                enq(t, "anim")
                        self._term(a2.off + a2.count * 4)

        # seed: sprt-images = ALL renderer offsets (incl. font glyph-images, so their
        # bitmap null is checked). Frame-detection below excludes font referrers.
        for r in self.renderers:
            enq(r, "sprt")
        drain()

        # seed inline animations reached ONLY by code offset (their frames all have
        # a NULL sprt, so the sprt-seed never reached them). Signature: an anim_speed
        # word (4.28 fixed => val & 0x0FFFFFFF == 0) at slot-4 whose slot points to a
        # frame-shaped target. Iterate to fixpoint.
        def ptr_or_zero(o):
            return o in slots or self.raw(o) == 0
        changed = True
        while changed:
            changed = False
            for s in list(self.slots):
                t = self.raw(s)
                if not t or t in self.kind or in_font(s) or not in_mobd(t):
                    continue
                if not (ptr_or_zero(t + FR_SPRT) and ptr_or_zero(t + FR_SHAPE)):
                    continue
                sp = s - 4
                if sp in slots:
                    continue
                v = self.raw(sp)
                if v is None or (v & 0x0FFFFFFF) != 0:
                    continue
                before = len(self.kind)
                enq(sp, "anim")
                drain()
                if len(self.kind) != before:
                    changed = True

        # font surfaces: glyphs[] @ base+4 -> GlyphDesc (ptrs @12/16/20/24)
        for i in FONT_IDS:
            if i not in ranges:
                continue
            garr = self.array_of.get(ranges[i][0] + 4)
            garr = next((a for a in self.arrays if a.off == ranges[i][0] + 4), None)
            if not garr:
                continue
            self._term(garr.off + garr.count * 4)
            for j in range(garr.count):
                g = self.raw(garr.off + j * 4)
                if not g:
                    continue
                self.kind[g] = "glyph"
                for pfx in GLYPH_PTRS:
                    self._esc(g + pfx)

        # completeness: RLLC targets not classified, split by real risk.
        risky, benign = set(), set()
        for o in slots:
            if o in surf_slots:
                continue
            t = self.raw(o)
            if not t or t in self.kind:
                continue
            # risky only if it has a relocated ptr field in a small window that we
            # haven't attributed to a following struct (heuristic display only).
            if any((t + k) in slots for k in range(0, 12, 4)):
                risky.add(t)
            else:
                benign.add(t)
        self.risky, self.benign = risky, benign
        return self

    def extra_slots(self):
        """Sorted list of DATA-body offsets to widen IN ADDITION to the RLLC:
        escaped null pointer fields + null array terminators. All hold value 0."""
        return sorted(self.escaped | self.terminators)

    def validate(self):
        """Self-check the extra-slot set. Returns list of problems (empty == OK)."""
        # NOTE: MOBD data is packed, so pointer fields are legitimately unaligned
        # (the x64 loader relocates via memcpy) - no 4-alignment requirement.
        problems = []
        extra = self.extra_slots()
        for o in extra:
            if o + 4 > self.n:
                problems.append(f"{o:#x}: out of bounds")
            elif self.raw(o) != 0:
                problems.append(f"{o:#x}: not zero ({self.raw(o):#x})")
            if o in self.slots:
                problems.append(f"{o:#x}: already in RLLC (double widen)")
        if len(extra) != len(set(extra)):
            problems.append("duplicate offsets in extra set")
        return problems

    def report(self):
        print(f"\n=== {self.lvl.path} ===")
        kinds = {}
        for k in self.kind.values():
            kinds[k] = kinds.get(k, 0) + 1
        print("classified:", kinds)
        print(f"escaped nulls: {len(self.escaped)}   terminators: {len(self.terminators)}"
              f"   => extra widen-slots: {len(self.escaped | self.terminators)}")
        print(f"unclassified: risky={len(self.risky)}  benign(ptr-free)={len(self.benign)}")
        if self.risky:
            print("  risky e.g.", sorted(self.risky)[:8])


if __name__ == "__main__":
    for p in sys.argv[1:]:
        Walk(load(p)).run().report()
