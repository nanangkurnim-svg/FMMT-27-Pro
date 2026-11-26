"""MSVC x64 RTTI helpers: class name of a vtable, and vtable of a class name.

vtable[-1] -> RTTICompleteObjectLocator {sig=1, offset, cdOffset, pTypeDescriptor (RVA), ...}
TypeDescriptor {pVFTable, spare, name[] = ".?AVNAME@@"}
"""
import json
import os
import struct
import sys

from game import engine_build
from winproc import Process


def class_name(p: Process, base: int, vt: int) -> str | None:
    col = p.u64(vt - 8)
    if not col:
        return None
    hdr = p.read(col, 0x18)
    if not hdr:
        return None
    sig, _off, _cd, td_rva = struct.unpack_from("<IIII", hdr)
    if sig != 1:
        return None
    name = p.read(base + td_rva + 0x10, 128)
    if not name:
        return None
    return name.split(b"\0", 1)[0].decode("latin-1")


def module_image(p: Process, module) -> bytes:
    """The module as mapped. Uses VirtualQueryEx to read only committed readable regions (skipping
    unreadable or PAGE_GUARD pages cleanly, which also avoids slow page-by-page fallback on large
    protected sections in Steam builds)."""
    base, size, step, page = module.base, module.size, 16 << 20, 0x1000
    buf = bytearray(size)
    for r in p.regions(lo=base, hi=base + size):
        lo = max(r.base, base)
        hi = min(r.base + r.size, base + size)
        for addr in range(lo, hi, step):
            n = min(step, hi - addr)
            b = p.read(addr, n)
            if b:
                buf[addr - base: addr - base + n] = b
            else:
                for q in range(addr, addr + n, page):
                    pn = min(page, addr + n - q)
                    pb = p.read(q, pn)
                    if pb:
                        buf[q - base: q - base + pn] = pb
    return bytes(buf)


def vtables_for(p: Process, module, mangled: str, image: bytes | None = None) -> dict[int, int]:
    """Find every vtable in the module whose RTTI name equals `mangled`.
    Returns {vtable address: COL offset of that sub-object}.
    Pass `image` (from module_image) to look up several classes without re-reading the module."""
    base = module.base
    image = module_image(p, module) if image is None else image
    i = image.find(mangled.encode() + b"\0")
    if i < 0:
        return {}
    td_rva = i - 0x10
    out = {}
    # object locators: {sig=1, offset, cdOffset, TypeDescriptor RVA, ...}
    pat = struct.pack("<I", td_rva)
    j = image.find(pat)
    while j != -1:
        c = j - 0xC
        if c >= 0 and c % 4 == 0 and struct.unpack_from("<I", image, c)[0] == 1:
            col, offset = base + c, struct.unpack_from("<I", image, c + 4)[0]
            # vtable[-1] == col
            ref = struct.pack("<Q", col)
            k = image.find(ref[:5])   # not the whole pointer: its trailing zeros make find() crawl
            while k != -1:
                if k % 8 == 0 and image.startswith(ref, k):
                    out[base + k + 8] = offset
                k = image.find(ref[:5], k + 1)
        j = image.find(pat, j + 1)
    return out


CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FMMT 27", "classes.json")


def class_vtables(p: Process, build, names: list[str]) -> dict[str, dict[int, int]]:
    """vtables_for for several classes. Searching the module takes seconds and the answer only changes
    with a game patch, so it is kept per build, as offsets from the module base."""
    base = build.module.base
    try:
        with open(CACHE, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        saved = {}
    known = saved.get(build.id, {})
    if not all(known.get(n) for n in names):  # an empty entry is a failed lookup, not an answer
        image = module_image(p, build.module)
        known = {n: {str(v - base): off for v, off in vtables_for(p, build.module, n, image).items()} for n in names}
        try:
            if not all(known.values()):
                raise OSError("a class was not found: do not keep that")
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w", encoding="utf-8") as fh:
                json.dump({**saved, build.id: known}, fh)
        except OSError:
            pass  # only costs the search again next time
    return {n: {base + int(rva): off for rva, off in known[n].items()} for n in names}


if __name__ == "__main__":
    with Process.attach() as p:
        m = engine_build(p).module
        for a in sys.argv[1:]:
            vt = int(a, 16)
            print(f"0x{vt:X} (+0x{vt - m.base:X}): {class_name(p, m.base, vt)}")
