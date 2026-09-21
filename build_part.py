#!/usr/bin/env python3
"""Build a BL4 SeparatedGestaltSkeletalMeshPart from FModel-extracted PSKs.

Takes the vanilla cooked part as a shell and replaces the geometry in every
LOD with your own, padding each buffer up to the vanilla counts. Nothing in
the package structure is rebuilt.

Usage
-----
    python3 build_part.py \
        --vanilla  /path/to/SKPart_CorpoHacker_UpperBody \
        --psk      LOD0.psk LOD1.psk LOD2.psk LOD3.psk \
        --out      /path/to/output

--vanilla is a path WITHOUT extension; .uasset/.uexp/.ubulk are appended.
--psk takes one file per LOD, in order, each needing a VTXNORMS chunk
      (FModel writes it; the Blender exporter does not).
--out is a directory; the three files are written there under the same
      base name as the vanilla part, ready to repack.

Everything else - buffer offsets, budgets, LOD count, which LOD is inline -
is read from the vanilla files, so this works on any part.
"""
import argparse, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # find the bl4* modules next to this script
import bl4psk, bl4mesh, bl4uexp, bl4build

# Offsets into the .uasset package summary. These are stable for BL4 packages.
OFF_TOTAL_HEADER_SIZE = 0x1c
OFF_SUMMARY           = 0x88    # (NameCount, NameOffset), then the rest of the summary
OFF_BULK_DATA_START   = 0x110
OFF_DATA_RESOURCE     = 0x134
DR_ENTRY_V1           = 44      # bytes per ObjectDataResource entry at version 1


def read_summary(ua):
    """Pull the handful of package-summary fields the build needs."""
    hdr = struct.unpack_from('<i', ua, OFF_TOTAL_HEADER_SIZE)[0]
    ints = struct.unpack_from('<' + 'i' * 12, ua, OFF_SUMMARY)
    export_count, export_off, depends_off = ints[6], ints[7], ints[10]
    stride = (depends_off - export_off) // export_count
    exports = []
    for k in range(export_count):
        o = export_off + k * stride
        size, offset = struct.unpack_from('<qq', ua, o + 28)
        exports.append(dict(index=k, off_record=o, SerialSize=size, SerialOffset=offset))
    return dict(header_size=hdr, export_offset=export_off, export_stride=stride, exports=exports)


def read_bulk_regions(ua):
    """(offset, size) of each streamed LOD payload inside the .ubulk.

    Source of truth is the ObjectDataResource table. Entries with SerialSize 0
    are the inline LOD, which lives in the .uexp instead, so they are skipped.
    """
    dro = struct.unpack_from('<i', ua, OFF_DATA_RESOURCE)[0]
    version, count = struct.unpack_from('<ii', ua, dro)
    if version != 1:
        raise SystemExit(f'ObjectDataResource version {version}; BL4 expects 1. '
                         'This asset was cooked by a newer engine and will crash the game.')
    regions = []
    for k in range(count):
        o = dro + 8 + DR_ENTRY_V1 * k
        flags, ser_off, dup_off, ser_size, raw_size, outer, legacy = struct.unpack_from('<iqqqqiI', ua, o)
        if ser_size > 0:
            regions.append((ser_off, ser_size))
    return regions


def find_extras_start(uexp, export_data_start, export_data_end):
    """Locate the binary tail of the mesh export.

    The export begins with unversioned property data of unpredictable length,
    then the binary block starts with 2 strip-flag bytes followed by
    ImportedBounds as 7 float64s. Scan for a plausible bounds record, then
    confirm by checking the whole block parses to exactly the export end.
    """
    for off in range(export_data_start, min(export_data_start + 65536, export_data_end - 64)):
        if uexp[off:off + 2] != b'\x05\x00':
            continue
        try:
            b = struct.unpack_from('<7d', uexp, off + 2)
        except struct.error:
            continue
        ox, oy, oz, ex, ey, ez, r = b
        if not (r > 0.01 and ex > 0.001 and ey > 0.001 and ez > 0.001):
            continue
        if any(abs(v) > 1e6 for v in b):
            continue
        try:
            m = bl4uexp.parse(uexp, off, export_data_end)
        except Exception:
            continue
        if m['exact'] and m['LODCount'] > 0:
            return off, m
    raise SystemExit('could not locate the export binary tail; is this a Gestalt skeletal mesh part?')


# BaseVertexIndex lives inside the 21 otherwise-undecoded bytes that follow
# NumTriangles. It is the first vertex of the section within the shared vertex
# buffer, so it must be rewritten whenever the section split changes.
OFF_BASE_VERTEX_IN_FLAGS_A = 13


def bone_parents(uexp, M):
    """Parent index per bone, from the reference skeleton."""
    out = []
    for i in range(M['BoneCount']):
        out.append(struct.unpack_from('<i', uexp, M['off_bones'] + 12 * i + 8)[0])
    return out


def expand_ancestors(bones, parents):
    """Close a bone set over its ancestors - a skinned bone needs its chain."""
    out = set(bones)
    for b in list(bones):
        p = parents[b] if 0 <= b < len(parents) else -1
        while p >= 0 and p not in out:
            out.add(p)
            p = parents[p]
    return out


def bone_arrays(uexp, L, used, parents):
    """RequiredBones / ActiveBoneIndices for a LOD, as uint16 blobs.

    Vanilla prunes facial and twist bones at low LODs. Geometry that still
    weights to them needs them listed, or a reader that builds the LOD
    skeleton from these arrays cannot resolve the bone map. Vanilla's entries
    are kept and the missing ones merged in.
    """
    need = expand_ancestors(used, parents)
    van_req = set(struct.unpack_from(f'<{L["RequiredBonesCount"]}H', uexp, L['off_RequiredBones']))
    van_act = set(struct.unpack_from(f'<{L["ActiveBonesCount"]}H', uexp, L['off_ActiveBones']))
    req = sorted(van_req | need)
    act = sorted(van_act | need)
    return (struct.pack(f'<i{len(req)}H', len(req), *req),
            struct.pack(f'<i{len(act)}H', len(act), *act),
            len(req), len(act))


def section_bytes(uexp, vanilla_section, new, patch_base_vertex=True, dup_verts='empty'):
    """Rebuild one section record, reusing vanilla's undecoded flag bytes.

    Everything in flags_a/b/c is copied verbatim except BaseVertexIndex, which
    is patched to match this build's section boundaries.
    """
    S = vanilla_section
    flags_a = bytearray(uexp[S['off_NumTriangles'] + 4:S['off_BoneMapCount']])
    if patch_base_vertex:
        struct.pack_into('<i', flags_a, OFF_BASE_VERTEX_IN_FLAGS_A, new['vbase'])
    flags_a = bytes(flags_a)
    flags_b = uexp[S['off_MaxBoneInfluences'] + 4:S['off_DupVertDataCount']]
    dup_end = S['off_DupVertIndex'] + 8 * S['DupVertIndexCount']
    flags_c = uexp[dup_end:dup_end + 4]
    o = bytearray(b'\x05\x00')
    o += struct.pack('<H', new['mat'])
    o += struct.pack('<ii', new['base'], new['tris'])
    o += flags_a
    o += struct.pack('<i', len(new['bonemap']))
    o += struct.pack(f'<{len(new["bonemap"])}H', *new['bonemap'])
    o += struct.pack('<ii', new['verts'], new['maxinfl'])
    o += flags_b
    if dup_verts == 'vanilla':
        # Carry vanilla's duplicated-vertices buffers over verbatim. They map
        # UV-seam duplicates for the recompute-tangents pass. The counts no
        # longer match this build's vertices, but a working third-party mod
        # does exactly this, and FModel may need them non-empty.
        dv_start = S['off_DupVertDataCount']
        dv_end = S['off_DupVertIndex'] + 8 * S['DupVertIndexCount']
        o += uexp[dv_start:dv_end]
    else:
        o += struct.pack('<ii', 0, 0)      # DupVertData / DupVertIndexData: empty
    o += flags_c
    return bytes(o)


def preflight(vuexp, vubulk, M, regions):
    """Round-trip vanilla through the codec before trusting it.

    If the vanilla payloads do not re-serialise byte-identically, the format
    model does not fit this part and nothing built from it can be trusted.
    """
    streamed = 0
    for i, L in enumerate(M['LODs']):
        if L['inline']:
            src = vuexp[L['off_payload']:L['off_payload'] + L['payloadSize']]
            P = bl4mesh.parse_lod(vuexp, L['off_payload'], L['payloadSize'])
        else:
            off, size = regions[streamed]; streamed += 1
            src = vubulk[off:off + size]
            P = bl4mesh.parse_lod(vubulk, off, size)
        if bl4mesh.write_lod(P) != src:
            raise SystemExit(f'preflight failed: vanilla LOD{i} does not round-trip. '
                             'The codec does not match this part; build aborted.')
    return True


def postflight(ua, uexp, ubulk, regions, check_base_vertex=True):
    """Check the assembled bytes before writing anything to disk."""
    problems = []
    summ = read_summary(ua); hdr = summ['header_size']
    mesh = max(summ['exports'], key=lambda e: e['SerialSize'])
    if sum(e['SerialSize'] for e in summ['exports']) != len(uexp) - 4:
        problems.append('export SerialSize sum does not match .uexp length')
    running = hdr
    for e in summ['exports']:
        if e['SerialOffset'] != running:
            problems.append(f'export {e["index"]} SerialOffset is wrong')
        running += e['SerialSize']
    if struct.unpack_from('<i', ua, OFF_BULK_DATA_START)[0] != hdr + len(uexp) - 4:
        problems.append('BulkDataStartOffset is wrong')
    if sum(s for _, s in regions) != len(ubulk):
        problems.append(f'DataResource sizes ({sum(s for _, s in regions)}) do not sum to '
                        f'the .ubulk length ({len(ubulk)})')
    run = 0
    for k, (off, size) in enumerate(regions):
        if off != run:
            problems.append(f'DataResource entry {k} offset is {off}, expected {run}')
        run += size

    start = mesh['SerialOffset'] - hdr
    try:
        _, M2 = find_extras_start(uexp, start, start + mesh['SerialSize'])
    except SystemExit:
        problems.append('built .uexp does not parse'); return problems
    if not M2['exact']:
        problems.append('built .uexp has unaccounted bytes')
    streamed = 0
    for i, L in enumerate(M2['LODs']):
        if L['inline']:
            P = bl4mesh.parse_lod(uexp, L['off_payload'], L['payloadSize'])
        else:
            off, size = regions[streamed]; streamed += 1
            P = bl4mesh.parse_lod(ubulk, off, size)
        bv, bt = len(bl4mesh.vertices(P)), len(bl4mesh.triangles(P))
        sv = sum(s['NumVertices'] for s in L['Sections'])
        st_ = sum(s['NumTriangles'] for s in L['Sections'])
        if sv > bv or st_ > bt:
            problems.append(f'LOD{i} sections exceed their buffers')
        last = L['Sections'][-1]
        if last['BaseIndex'] + 3 * last['NumTriangles'] > bt * 3:
            problems.append(f'LOD{i} last section runs past the index buffer')
        req = set(struct.unpack_from(f'<{L["RequiredBonesCount"]}H', uexp, L['off_RequiredBones']))
        act = set(struct.unpack_from(f'<{L["ActiveBonesCount"]}H', uexp, L['off_ActiveBones']))
        bm = set()
        for S in L['Sections']:
            bm |= set(S['BoneMap'])
        if bm - req:
            problems.append(f'LOD{i}: {len(bm - req)} bone-map entries missing from RequiredBones')
        if bm - act:
            problems.append(f'LOD{i}: {len(bm - act)} bone-map entries missing from ActiveBoneIndices')
        run_v = run_t = 0
        for j, S in enumerate(L['Sections']):
            if check_base_vertex and S['BaseVertexIndex'] != run_v:
                problems.append(f'LOD{i} section {j} BaseVertexIndex is {S["BaseVertexIndex"]}, '
                                f'expected {run_v}')
            if S['BaseIndex'] != run_t * 3:
                problems.append(f'LOD{i} section {j} BaseIndex is {S["BaseIndex"]}, '
                                f'expected {run_t * 3}')
            run_v += S['NumVertices']; run_t += S['NumTriangles']
    return problems


def winding_agreement(P):
    """Percentage of triangles whose geometric normal agrees with the vertex
    normal. Vanilla winds them opposite, so this should be near 0."""
    V = bl4mesh.vertices(P); T = bl4mesh.triangles(P); N = P['tangents']
    a = t = 0
    for x, y, z in T[:4000]:
        if x == y or y == z or x == z: continue
        p0, p1, p2 = V[x], V[y], V[z]
        e1 = [p1[k] - p0[k] for k in range(3)]; e2 = [p2[k] - p0[k] for k in range(3)]
        gn = (e1[1]*e2[2]-e1[2]*e2[1], e1[2]*e2[0]-e1[0]*e2[2], e1[0]*e2[1]-e1[1]*e2[0])
        n = struct.unpack('<4b', N[8*x+4:8*x+8]); vn = (n[0]/127, n[1]/127, n[2]/127)
        if sum(gn[k]*vn[k] for k in range(3)) > 0: a += 1
        t += 1
    return 100.0 * a / max(t, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vanilla', required=True,
                    help='path to the vanilla part WITHOUT extension')
    ap.add_argument('--psk', required=True, nargs='+',
                    help='one FModel-exported PSK per LOD, in order')
    ap.add_argument('--out', required=True, help='output directory')
    ap.add_argument('--base-vertex-index', choices=('computed', 'vanilla'), default='computed',
                    help="computed (default) sets each section's BaseVertexIndex to match this "
                         "build's section boundaries, which is what the format specifies. "
                         "vanilla leaves the original values in place; the game ignores this "
                         "field either way, but FModel's preview may only work with vanilla.")
    ap.add_argument('--buffers', choices=('exact', 'padded'), default='exact',
                    help="exact (default) sizes each LOD buffer to your geometry and "
                         "updates the data-resource entries and availability blocks to "
                         "match. padded keeps vanilla's buffer sizes and fills the "
                         "surplus with orphan vertices and unused index slots - smaller "
                         "diff, but it stops FModel previewing and caps your vertex count.")
    ap.add_argument('--dup-verts', choices=('empty', 'vanilla'), default='empty',
                    help="empty (default) writes zero-length duplicated-vertices buffers, "
                         "which shrinks the .uexp considerably and has shown no visible "
                         "seams in game. vanilla copies the original buffers through; a "
                         "working third-party mod does this and previews in FModel.")
    ap.add_argument('--scale', type=float, default=1.0,
                    help='multiply source positions (1.0 for centimetres, 100 if exported in metres)')
    a = ap.parse_args()

    base = os.path.basename(a.vanilla)
    ua = bytearray(open(a.vanilla + '.uasset', 'rb').read())
    vuexp = open(a.vanilla + '.uexp', 'rb').read()
    vubulk = open(a.vanilla + '.ubulk', 'rb').read()

    summ = read_summary(ua)
    hdr = summ['header_size']
    mesh_export = max(summ['exports'], key=lambda e: e['SerialSize'])   # the mesh is the big one
    export_start = mesh_export['SerialOffset'] - hdr
    export_end = export_start + mesh_export['SerialSize']

    regions = read_bulk_regions(ua)
    extras, M = find_extras_start(vuexp, export_start, export_end)
    print(f'{base}: {M["LODCount"]} LODs, binary tail at 0x{extras:x}, '
          f'{len(regions)} streamed payload(s) in .ubulk')

    preflight(vuexp, vubulk, M, regions)
    print('preflight: vanilla round-trips through the codec')

    if len(a.psk) != M['LODCount']:
        raise SystemExit(f'need {M["LODCount"]} PSK files, got {len(a.psk)}')

    # per-LOD budgets, trailer bytes and weight stride, all taken from vanilla
    budgets, trailers, maxinfl = [], [], []
    streamed = 0
    for i, L in enumerate(M['LODs']):
        budgets.append((sum(s['NumVertices'] for s in L['Sections']),
                        sum(s['NumTriangles'] for s in L['Sections'])))
        if L['inline']:
            P = bl4mesh.parse_lod(vuexp, L['off_payload'], L['payloadSize'])
        else:
            P = bl4mesh.parse_lod(vubulk, *regions[streamed]); streamed += 1
        trailers.append(P['trailer']); maxinfl.append(P['MaxBoneInfluences'])

    built = []
    print(f'{"LOD":<5}{"verts":>8}{"budget":>8}{"tris":>8}{"budget":>8}{"pad v":>7}{"pad t":>7}   sections (v/t)')
    for i, path in enumerate(a.psk):
        psk = bl4psk.load(path)
        if not psk.get('normals'):
            raise SystemExit(f'{path} has no VTXNORMS chunk; export it from FModel, '
                             'not from the Blender PSK addon')
        if a.buffers == 'exact':
            bv, bt = len(psk['wedge_point']), len(psk['faces'])
        else:
            bv, bt = budgets[i]
        L, st = bl4build.build_lod(psk, psk['normals'], bv, bt,
                                   maxinfl[i], trailers[i], scale=a.scale)
        built.append((L, st))
        secs = ' + '.join(f'{s["verts"]}/{s["tris"]}' for s in st['sections'])
        print(f'LOD{i:<2}{st["verts"]:>8}{budgets[i][0]:>8}{st["tris"]:>8}'
              f'{budgets[i][1]:>8}{st["pad_verts"]:>7}{st["pad_tris"]:>7}   {secs}')

    # --- .ubulk: streamed payloads back to back, in table order
    newub = bytearray()
    newregions = []
    for i, L in enumerate(M['LODs']):
        if not L['inline']:
            p = bl4mesh.write_lod(built[i][0])
            newregions.append((len(newub), len(p)))
            newub += p

    # --- .uexp: vanilla bytes everywhere except bounds and the section arrays
    parents = bone_parents(vuexp, M)
    new_secs_pre = [[dict(s2, maxinfl=min(maxinfl[i], 8)) for s2 in built[i][1]['sections']]
                    for i in range(len(M['LODs']))]

    out = bytearray(vuexp[:extras]) + b'\x05\x00'
    origin, extent, radius = built[0][1]['bounds']
    out += struct.pack('<7d', *origin, *extent, radius)
    out += vuexp[M['off_materials'] - 4:M['LODs'][0]['off']]
    streamed_seen = 0
    for i, L in enumerate(M['LODs']):
        st = built[i][1]
        used = set()
        for sec in new_secs_pre[i]:
            used |= set(sec['bonemap'])
        req_blob, act_blob, nreq, nact = bone_arrays(vuexp, L, used, parents)
        out += vuexp[L['off']:L['off_RequiredBonesCount']]
        out += req_blob
        new_secs = new_secs_pre[i]
        if len(new_secs) != len(L['Sections']):
            raise SystemExit(
                f'LOD{i}: source mesh has {len(new_secs)} material section(s) but the '
                f'vanilla part has {len(L["Sections"])}. The section count must match - '
                'check the material slots on your mesh.')
        sb = bytearray(struct.pack('<i', len(new_secs)))
        for S, new in zip(L['Sections'], new_secs):
            sb += section_bytes(vuexp, S, new,
                                patch_base_vertex=(a.base_vertex_index == 'computed'),
                                dup_verts=a.dup_verts)
        out += sb
        out += act_blob
        if L['inline']:
            p = bl4mesh.write_lod(built[i][0])
            out += struct.pack('<i', len(p)) + p
        else:
            blk = bytearray(vuexp[L['off_avail']:L['off_avail'] + bl4uexp.AVAIL_BLOCK])
            if a.buffers == 'exact':
                Lp = built[i][0]
                off, size = newregions[streamed_seen]
                struct.pack_into('<q', blk, bl4uexp.AV['streamedSize'], size)
                struct.pack_into('<i', blk, bl4uexp.AV['indexCount'], Lp['idx_count'])
                for k in ('numVerts1', 'numVerts2', 'numVerts3'):
                    struct.pack_into('<i', blk, bl4uexp.AV[k], Lp['pos_count'])
                struct.pack_into('<i', blk, bl4uexp.AV['influenceCount'], Lp['influence_count'])
            out += bytes(blk)
            streamed_seen += 1
    out += vuexp[M['off_nanite']:]

    # --- .uasset: data-resource entries, then the two size fields
    if a.buffers == 'exact':
        dro = struct.unpack_from('<i', ua, OFF_DATA_RESOURCE)[0]
        for k, (off, size) in enumerate(newregions):
            p = dro + 8 + DR_ENTRY_V1 * k
            struct.pack_into('<q', ua, p + 4, off)      # SerialOffset
            struct.pack_into('<q', ua, p + 20, size)    # SerialSize
            struct.pack_into('<q', ua, p + 28, size)    # RawSize
    delta = len(out) - len(vuexp)
    p = mesh_export['off_record'] + 28
    struct.pack_into('<q', ua, p, struct.unpack_from('<q', ua, p)[0] + delta)
    struct.pack_into('<i', ua, OFF_BULK_DATA_START,
                     struct.unpack_from('<i', ua, OFF_BULK_DATA_START)[0] + delta)

    problems = postflight(ua, out, newub, read_bulk_regions(ua),
                          check_base_vertex=(a.base_vertex_index == 'computed'))
    v_w = winding_agreement(bl4mesh.parse_lod(vubulk, *regions[0]))
    b_w = winding_agreement(bl4mesh.parse_lod(newub, *regions[0]))
    if (v_w < 50) != (b_w < 50):
        problems.append(f'triangle winding is inverted (vanilla {v_w:.1f}%, built {b_w:.1f}%) '
                        '- the mesh would render inside-out')
    if problems:
        print('\nBUILD FAILED - nothing written:', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        sys.exit(1)
    print(f'postflight: all checks passed (winding {b_w:.1f}% vs vanilla {v_w:.1f}%)')

    os.makedirs(a.out, exist_ok=True)
    for ext, data in (('uasset', ua), ('uexp', out), ('ubulk', newub)):
        open(os.path.join(a.out, f'{base}.{ext}'), 'wb').write(data)
    print(f'\n.ubulk {len(newub)} (vanilla {len(vubulk)})')
    print(f'.uexp  {len(out)} (vanilla {len(vuexp)}, delta {delta})')
    print(f'written to {a.out}/{base}.*')


if __name__ == '__main__':
    main()
