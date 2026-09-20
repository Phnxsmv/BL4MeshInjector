"""Build BL4 cooked LOD payloads from PSK geometry + FBX normals.

Source data:
  .psk  - points, wedge UVs, faces + material, raw weights, reference skeleton
  .fbx  - per-vertex normals (index-aligned with the PSK points)

Output: a payload in the exact layout bl4mesh reads, padded up to the vanilla
vertex/triangle totals with orphan vertices and unreferenced index slots.
"""
import struct, math
from collections import defaultdict

def _norm(v):
    l=math.sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2])
    return (v[0]/l, v[1]/l, v[2]/l) if l>1e-12 else (1.0,0.0,0.0)

def _pack_i8(v, w):
    q=lambda x: max(-127,min(127,int(round(x*127.0))))
    return bytes(struct.pack('<4b', q(v[0]), q(v[1]), q(v[2]), w))

def compute_tangents(pos, uv, tris, normals):
    """Per-vertex tangent from UV derivatives, Gram-Schmidt against the normal."""
    acc=[[0.0,0.0,0.0] for _ in pos]
    for a,b,c in tris:
        if a==b or b==c or a==c: continue
        p0,p1,p2=pos[a],pos[b],pos[c]
        u0,u1,u2=uv[a],uv[b],uv[c]
        e1=(p1[0]-p0[0],p1[1]-p0[1],p1[2]-p0[2])
        e2=(p2[0]-p0[0],p2[1]-p0[1],p2[2]-p0[2])
        d1=(u1[0]-u0[0],u1[1]-u0[1]); d2=(u2[0]-u0[0],u2[1]-u0[1])
        den=d1[0]*d2[1]-d2[0]*d1[1]
        if abs(den)<1e-12: continue
        r=1.0/den
        t=((e1[0]*d2[1]-e2[0]*d1[1])*r,
           (e1[1]*d2[1]-e2[1]*d1[1])*r,
           (e1[2]*d2[1]-e2[2]*d1[1])*r)
        for i in (a,b,c):
            acc[i][0]+=t[0]; acc[i][1]+=t[1]; acc[i][2]+=t[2]
    out=[]
    for i,n in enumerate(normals):
        t=acc[i]
        d=t[0]*n[0]+t[1]*n[1]+t[2]*n[2]
        t=(t[0]-n[0]*d, t[1]-n[1]*d, t[2]-n[2]*d)
        if abs(t[0])+abs(t[1])+abs(t[2])<1e-9:
            t=(1.0,0.0,0.0) if abs(n[0])<0.9 else (0.0,1.0,0.0)
            d=t[0]*n[0]+t[1]*n[1]+t[2]*n[2]
            t=(t[0]-n[0]*d, t[1]-n[1]*d, t[2]-n[2]*d)
        out.append(_norm(t))
    return out

def build_lod(psk, normals, budget_verts, budget_tris, max_infl, trailer, scale=1.0):
    """Build one LOD payload. psk: bl4psk.load() dict. normals: per-point, PSK space.

    Sections are derived from the material ids actually used by the faces, in
    ascending material order, so a part with three materials produces three
    sections just as vanilla does.
    """
    nverts=len(psk['wedge_point'])

    # --- one section per material, in material order
    vmat={}
    for f,m in zip(psk['faces'], psk['face_mat']):
        for w in f: vmat[w]=m
    mats=sorted(set(psk['face_mat']))
    order=[]; sec_vert_counts=[]
    for m in mats:
        vs=[i for i in range(nverts) if vmat.get(i)==m]
        order+=vs; sec_vert_counts.append(len(vs))
    if len(order)!=nverts:
        stray=nverts-len(order)
        raise ValueError(f'{stray} vertices belong to no face; delete loose geometry first')
    remap={old:new for new,old in enumerate(order)}

    # --- geometry in cooked space (PSK negates Y; that mirror also flips winding)
    pos=[(psk['points'][psk['wedge_point'][o]][0]*scale,
         -psk['points'][psk['wedge_point'][o]][1]*scale,
          psk['points'][psk['wedge_point'][o]][2]*scale) for o in order]
    uv =[psk['wedge_uv'][o] for o in order]
    nrm=[_norm((normals[psk['wedge_point'][o]][0],
                -normals[psk['wedge_point'][o]][1],
                 normals[psk['wedge_point'][o]][2])) for o in order]

    sec_tris=[]
    for m in mats:
        sec_tris.append([(remap[f[0]],remap[f[2]],remap[f[1]])
                         for f,fm in zip(psk['faces'],psk['face_mat']) if fm==m])
    tris=[t for group in sec_tris for t in group]
    tan=compute_tangents(pos,uv,tris,nrm)

    # --- weights per vertex
    pw=defaultdict(list)
    for wt,pi,bi in psk['weights']: pw[pi].append((wt,bi))
    vw=[sorted(pw.get(psk['wedge_point'][o],[]),reverse=True)[:max_infl] for o in order]

    # --- per-section bone maps and descriptor values
    sections=[]; vbase=0; tbase=0
    for k,m in enumerate(mats):
        nv=sec_vert_counts[k]; nt=len(sec_tris[k])
        used=sorted({bi for i in range(vbase,vbase+nv) for _,bi in vw[i]})
        if len(used)>=256:
            raise ValueError(f'section {k} needs {len(used)} bones; the format allows 255')
        sections.append(dict(mat=m, base=tbase*3, tris=nt, verts=nv,
                             bonemap=used, index={b:i for i,b in enumerate(used)},
                             vbase=vbase))
        vbase+=nv; tbase+=nt

    padv=budget_verts-nverts; padt=budget_tris-len(tris)
    if padv<0 or padt<0:
        raise ValueError(f'over budget: {nverts}/{budget_verts} verts, {len(tris)}/{budget_tris} tris')

    P=bytearray(); T=bytearray(); UV=bytearray(); W=bytearray()
    for i in range(nverts):
        P+=struct.pack('<3f',*pos[i])
        T+=_pack_i8(tan[i],127)+_pack_i8(nrm[i],127)
        UV+=struct.pack('<2e',*uv[i])
        sec=next(s for s in reversed(sections) if i>=s['vbase'])
        ws=vw[i]; tot=sum(w for w,_ in ws) or 1.0
        q=[(max(0,min(255,int(round(w/tot*255)))), sec['index'][b]) for w,b in ws]
        if q:
            q[0]=(q[0][0]+(255-sum(x for x,_ in q)), q[0][1])
        idx=[b for _,b in q]+[0]*(max_infl-len(q))
        wts=[w for w,_ in q]+[0]*(max_infl-len(q))
        W+=bytes(idx)+bytes(wts)
    last=pos[-1] if pos else (0.0,0.0,0.0)
    for _ in range(padv):
        P+=struct.pack('<3f',*last)
        T+=_pack_i8((1.0,0.0,0.0),127)+_pack_i8((0.0,0.0,1.0),127)
        UV+=struct.pack('<2e',0.0,0.0)
        W+=bytes(max_infl)+bytes([255]+[0]*(max_infl-1))
    I=bytearray()
    for a,b,c in tris: I+=struct.pack('<3H',a,b,c)
    I+=b'\x00'*(6*padt)

    L=dict(strip_idx=b'\x05\x00', DataTypeSize=2, idx_elem=2, idx_count=budget_tris*3,
           indices=bytes(I), pos_stride=12, pos_nv=budget_verts, pos_elem=12,
           pos_count=budget_verts, positions=bytes(P), strip_smv=b'\x05\x00',
           NumTexCoords=1, smv_nv=budget_verts, bFullPrecisionUVs=0, bHighPrecTangents=0,
           tan_elem=8, tan_count=budget_verts, tangents=bytes(T),
           uv_elem=4, uv_count=budget_verts, uvs=bytes(UV),
           strip_skin=b'\x05\x00', skin_f0=0, MaxBoneInfluences=max_infl,
           influence_count=budget_verts*max_infl, skin_nv=budget_verts,
           skin_f1=0, skin_f2=0, skin_f3=1, weights=bytes(W), trailer=trailer)
    for s in sections: s.pop('index')
    stats=dict(verts=nverts, tris=len(tris), sections=sections,
               pad_verts=padv, pad_tris=padt, bounds=_bounds(pos))
    return L, stats


def _bounds(pos):
    xs=[p[0] for p in pos]; ys=[p[1] for p in pos]; zs=[p[2] for p in pos]
    o=((min(xs)+max(xs))/2,(min(ys)+max(ys))/2,(min(zs)+max(zs))/2)
    e=((max(xs)-min(xs))/2,(max(ys)-min(ys))/2,(max(zs)-min(zs))/2)
    r=max(math.dist(p,o) for p in pos)
    return o,e,r
