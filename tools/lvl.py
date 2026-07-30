#!/usr/bin/env python3
"""KKND .LVL container parser + RLLC relocation decoder.

Foundation for the x64 null-pointer analysis. Mirrors the on-disk format used by
FILE_read_hunk / HUNK_fix_pointers (kknd.c):

  .LVL file = two hunks back-to-back: DATA then RRLC.
  hunk       = 8-byte header {char name[4]; uint32 size (BIG-endian)} + `size` bytes.
  DATA body  = little-endian words. word[0] = byte offset of the sections table.
  sections   = array of {char name[4]; uint32 data_off} terminated by data_off==0.
  RRLC body  = uint32 num_fixups (count of WORDS, not entries) then that many words:
                 & 0x80000000  -> renderer  (offset = e & 0x3FFFFFFF; slot holds blitter id)
                 & 0x40000000  -> ptr array (offset = e & 0x3FFFFFFF; NEXT word = count-1;
                                             relocate count+1 consecutive 4-byte ptrs)
                 else          -> single pointer (whole word = offset)

All offsets below are into the DATA body (i.e. relative to `data`, matching kknd.c).
"""
import struct
import sys
from dataclasses import dataclass, field

HUNK_RENDERER = 0x80000000
HUNK_PTRARRAY = 0x40000000
HUNK_MASK     = 0x3FFFFFFF


def u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


@dataclass
class Hunk:
    name: str
    body: bytes


@dataclass
class Section:
    name: str
    off: int          # byte offset into DATA body where this section's payload starts


@dataclass
class PtrArray:
    off: int          # offset of first ptr slot
    count: int        # number of relocated slots (already count-1 + 1)


@dataclass
class Lvl:
    path: str
    data: bytes                              # DATA hunk body
    rllc_body: bytes                         # RRLC hunk body
    sections: list = field(default_factory=list)
    single_ptrs: list = field(default_factory=list)   # [off, ...]
    ptr_arrays: list = field(default_factory=list)     # [PtrArray, ...]
    renderers: list = field(default_factory=list)      # [off, ...]
    ptr_slots: set = field(default_factory=set)        # every relocated 4-byte slot offset

    def section(self, name):
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def section_span(self, name):
        """(start, end) byte range of a section in DATA, end = next section start or EOF."""
        s = self.section(name)
        if not s:
            return None
        starts = sorted(x.off for x in self.sections)
        end = len(self.data)
        for st in starts:
            if st > s.off:
                end = min(end, st)
        return (s.off, end)


def _read_hunks(raw):
    hunks = []
    pos = 0
    while pos + 8 <= len(raw):
        name = raw[pos:pos + 4].decode("latin1")
        size = struct.unpack_from(">I", raw, pos + 4)[0]   # header size is BIG-endian
        body = raw[pos + 8:pos + 8 + size]
        hunks.append(Hunk(name, body))
        pos += 8 + size
    return hunks


def _parse_sections(data):
    secs = []
    table_off = u32(data, 0)
    off = table_off
    while off + 8 <= len(data):
        name = data[off:off + 4].decode("latin1")
        data_off = u32(data, off + 4)
        if data_off == 0:
            break                       # terminator entry
        secs.append(Section(name, data_off))
        off += 8
    return secs, table_off


def _decode_rllc(body):
    single, arrays, rend, slots = [], [], [], set()
    if len(body) < 4:
        return single, arrays, rend, slots
    num = u32(body, 0)
    i = 0
    words = (len(body) - 4) // 4
    while i < num and i < words:
        e = u32(body, 4 + i * 4)
        if e & HUNK_RENDERER:
            off = e & HUNK_MASK
            rend.append(off)
            slots.add(off)
        elif e & HUNK_PTRARRAY:
            off = e & HUNK_MASK
            i += 1
            count = u32(body, 4 + i * 4) + 1
            arrays.append(PtrArray(off, count))
            for j in range(count):
                slots.add(off + j * 4)
        else:
            single.append(e)
            slots.add(e)
        i += 1
    return single, arrays, rend, slots


def load(path):
    with open(path, "rb") as f:
        raw = f.read()
    hunks = _read_hunks(raw)
    data_hunk = hunks[0].body
    rllc_hunk = hunks[1].body if len(hunks) > 1 else b""
    lvl = Lvl(path=path, data=data_hunk, rllc_body=rllc_hunk)
    lvl.sections, _ = _parse_sections(data_hunk)
    lvl.single_ptrs, lvl.ptr_arrays, lvl.renderers, lvl.ptr_slots = _decode_rllc(rllc_hunk)
    return lvl


def summary(lvl):
    print(f"\n=== {lvl.path} ===")
    print(f"DATA {len(lvl.data)} bytes   RRLC {len(lvl.rllc_body)} bytes")
    print(f"sections ({len(lvl.sections)}): " +
          ", ".join(f"{s.name}@{s.off:#x}" for s in lvl.sections))
    print(f"RLLC: {len(lvl.single_ptrs)} single, {len(lvl.ptr_arrays)} arrays, "
          f"{len(lvl.renderers)} renderers -> {len(lvl.ptr_slots)} total ptr slots")
    # How many relocated slots hold a raw 0 (nulls already covered BY the RLLC)?
    zero_in_rllc = sum(1 for o in lvl.ptr_slots
                       if o + 4 <= len(lvl.data) and u32(lvl.data, o) == 0)
    print(f"      of those, {zero_in_rllc} slots hold raw 0 (nulls already in RLLC)")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        summary(load(p))
