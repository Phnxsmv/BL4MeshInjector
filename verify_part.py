#!/usr/bin/env python3
"""Validate a built BL4 part against the vanilla original.

    python3 verify_part.py --vanilla /path/SKPart_Name --built /path/to/output/SKPart_Name

Reads every structural field from the files themselves, so it works on any
part. Prints one line per check; exits non-zero if anything fails.
"""
import argparse, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import bl4mesh, bl4uexp
from build_part import (read_summary, read_bulk_regions, find_extras_start,
                        OFF_BULK_DATA_START, OFF_DATA_RESOURCE)

def load(base):
    return (bytearray(open(base + '.uasset', 'rb').read()),
            open(base + '.uexp', 'rb').read(),
            open(base + '.ubulk', 'rb').read())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vanilla', required=True, help='vanilla part path WITHOUT extension')
    ap.add_argument('--built', required=True, help='built part path WITHOUT extension')
    a = ap.parse_args()

    vua, vuexp, vubulk = load(a.vanilla)
    ua, uexp, ubulk = load(a.built)
    ok = True
    def chk(name, got, want):
        nonlocal ok
        good = got == want; ok &= good
        print(('  OK   ' if good else '  FAIL ') + f'{name}: {got}' + ('' if good else f'   expected {want}'))

    summ = read_summary(ua); hdr = summ['header_size']
    mesh = max(summ['exports'], key=lambda e: e['SerialSize'])
    total = sum(e['SerialSize'] for e in summ['exports'])

    chk('TotalHeaderSize == .uasset size', hdr, len(ua))
    chk('sum(export SerialSize) == .uexp - 4', total, len(uexp) - 4)
    running = hdr
    for e in summ['exports']:
        chk(f'export {e["index"]} SerialOffset', e['SerialOffset'], running)
        running += e['SerialSize']
    chk('BulkDataStartOffset', struct.unpack_from('<i', ua, OFF_BULK_DATA_START)[0], hdr + len(uexp) - 4)
    dro = struct.unpack_from('<i', ua, OFF_DATA_RESOURCE)[0]
    chk('ObjectDataResource version', struct.unpack_from('<i', ua, dro)[0], 1)

    regions = read_bulk_regions(ua)
    chk('sum(DataResource sizes) == .ubulk size', sum(s for _, s in regions), len(ubulk))

    start = mesh['SerialOffset'] - hdr
    extras, M = find_extras_start(uexp, start, start + mesh['SerialSize'])
    chk('.uexp every byte accounted for', M['exact'], True)

    streamed = 0
    for i, L in enumerate(M['LODs']):
        if L['inline']:
            P = bl4mesh.parse_lod(uexp, L['off_payload'], L['payloadSize'])
            chk(f'LOD{i} inline payload exact', P['_size'], L['payloadSize'])
        else:
            off, size = regions[streamed]; streamed += 1
            P = bl4mesh.parse_lod(ubulk, off, size)
            chk(f'LOD{i} streamed payload exact', P['_size'], size)
        sv = sum(s['NumVertices'] for s in L['Sections'])
        st_ = sum(s['NumTriangles'] for s in L['Sections'])
        bv, bt = len(bl4mesh.vertices(P)), len(bl4mesh.triangles(P))
        chk(f'LOD{i} sections fit buffers', sv <= bv and st_ <= bt, True)
        last = L['Sections'][-1]
        chk(f'LOD{i} last section inside index buffer',
            last['BaseIndex'] + 3 * last['NumTriangles'] <= bt * 3, True)

    # winding: vanilla convention is geometric normal OPPOSITE the vertex normal
    def agreement(P):
        V = bl4mesh.vertices(P); T = bl4mesh.triangles(P); N = P['tangents']; a = t = 0
        for x, y, z in T[:4000]:
            if x == y or y == z or x == z: continue
            p0, p1, p2 = V[x], V[y], V[z]
            e1 = [p1[k] - p0[k] for k in range(3)]; e2 = [p2[k] - p0[k] for k in range(3)]
            gn = (e1[1]*e2[2]-e1[2]*e2[1], e1[2]*e2[0]-e1[0]*e2[2], e1[0]*e2[1]-e1[1]*e2[0])
            n = struct.unpack('<4b', N[8*x+4:8*x+8]); vn = (n[0]/127, n[1]/127, n[2]/127)
            if sum(gn[k]*vn[k] for k in range(3)) > 0: a += 1
            t += 1
        return 100.0 * a / max(t, 1)
    vregions = read_bulk_regions(vua)
    w_v = agreement(bl4mesh.parse_lod(vubulk, *vregions[0]))
    w_b = agreement(bl4mesh.parse_lod(ubulk, *regions[0]))
    print(f'  ---- winding agreement: vanilla {w_v:.1f}%, built {w_b:.1f}%  (must be on the same side of 50%)')
    ok &= (w_v < 50) == (w_b < 50)

    b = M['ImportedBounds']
    print(f'  ---- bounds origin=({b[0]:.3f},{b[1]:.3f},{b[2]:.3f}) '
          f'extent=({b[3]:.3f},{b[4]:.3f},{b[5]:.3f}) r={b[6]:.3f}')
    print('\nPACKAGE VALID' if ok else '\nPROBLEMS FOUND')
    sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
