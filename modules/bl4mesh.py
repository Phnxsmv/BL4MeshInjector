"""BL4 SeparatedGestaltSkeletalMeshPart - streamed LOD render data codec.

Parses and re-serialises the per-LOD payloads stored in the .ubulk of a
cooked BL4 skeletal mesh part. Round-trip is byte-exact, which is what
makes it safe to modify geometry and write the result back.

Layout per LOD (all little-endian):

  index buffer     strip(2) DataTypeSize(1) elemSize(4) count(4) data
  position buffer  stride(4) numVerts(4) elemSize(4) count(4) data      12B/vertex
  static vertex    strip(2) NumTexCoords(4) numVerts(4) bFullPrecUV(4)
                   bHighPrecTangent(4) [tangents 8B/v] [uvs 4B/v]
  skin weights     strip(2) f0(4) MaxBoneInfluences(4) influenceCount(4)
                   numVerts(4) f1(4) f2(4) f3(4) byteCount(4) data
  trailer          40 bytes (empty colour/cloth/profile buffers)
"""
import struct

class _R:
    def __init__(s,b,o=0): s.b,s.o=b,o
    def u8(s):  v=s.b[s.o]; s.o+=1; return v
    def i32(s): v=struct.unpack_from('<i',s.b,s.o)[0]; s.o+=4; return v
    def raw(s,n): v=bytes(s.b[s.o:s.o+n]); s.o+=n; return v

def parse_lod(b, start, size):
    r=_R(b,start); L={}
    L['strip_idx']=r.raw(2); L['DataTypeSize']=r.u8()
    L['idx_elem']=r.i32(); n=r.i32(); L['indices']=r.raw(L['idx_elem']*n); L['idx_count']=n

    L['pos_stride']=r.i32(); L['pos_nv']=r.i32()
    L['pos_elem']=r.i32(); n=r.i32(); L['positions']=r.raw(L['pos_elem']*n); L['pos_count']=n

    L['strip_smv']=r.raw(2)
    L['NumTexCoords']=r.i32(); L['smv_nv']=r.i32()
    L['bFullPrecisionUVs']=r.i32(); L['bHighPrecTangents']=r.i32()
    L['tan_elem']=r.i32(); n=r.i32(); L['tangents']=r.raw(L['tan_elem']*n); L['tan_count']=n
    L['uv_elem']=r.i32();  n=r.i32(); L['uvs']=r.raw(L['uv_elem']*n);       L['uv_count']=n

    L['strip_skin']=r.raw(2)
    L['skin_f0']=r.i32(); L['MaxBoneInfluences']=r.i32()
    L['influence_count']=r.i32(); L['skin_nv']=r.i32()
    L['skin_f1']=r.i32(); L['skin_f2']=r.i32(); L['skin_f3']=r.i32()
    nb=r.i32(); L['weights']=r.raw(nb)

    L['trailer']=r.raw(start+size-r.o)
    L['_size']=r.o-start
    return L

def write_lod(L):
    o=bytearray()
    o+=L['strip_idx']; o.append(L['DataTypeSize'])
    o+=struct.pack('<ii',L['idx_elem'],L['idx_count']); o+=L['indices']
    o+=struct.pack('<ii',L['pos_stride'],L['pos_nv'])
    o+=struct.pack('<ii',L['pos_elem'],L['pos_count']); o+=L['positions']
    o+=L['strip_smv']
    o+=struct.pack('<iiii',L['NumTexCoords'],L['smv_nv'],L['bFullPrecisionUVs'],L['bHighPrecTangents'])
    o+=struct.pack('<ii',L['tan_elem'],L['tan_count']); o+=L['tangents']
    o+=struct.pack('<ii',L['uv_elem'],L['uv_count']);   o+=L['uvs']
    o+=L['strip_skin']
    o+=struct.pack('<iiiiiii',L['skin_f0'],L['MaxBoneInfluences'],L['influence_count'],
                   L['skin_nv'],L['skin_f1'],L['skin_f2'],L['skin_f3'])
    o+=struct.pack('<i',len(L['weights'])); o+=L['weights']
    o+=L['trailer']
    return bytes(o)

def vertices(L):
    """Decoded positions as (x,y,z) float triples."""
    n=L['pos_count']
    return [struct.unpack_from('<3f',L['positions'],12*i) for i in range(n)]

def triangles(L):
    n=L['idx_count']//3
    return [struct.unpack_from('<3H',L['indices'],6*i) for i in range(n)]

# --- PSK interop -------------------------------------------------------
# FModel's PSK export negates Y (Unreal -> ActorX handedness flip) and keeps
# vertex order identical to the cooked buffer. Verified index-by-index on
# LOD0/1/2 of SKPart_CorpoHacker_UpperBody: 100% of positions and triangles.

def to_psk_space(p):   return (p[0], -p[1], p[2])
def from_psk_space(p): return (p[0], -p[1], p[2])

def set_positions(L, pos):
    """Replace positions (cooked space). Count must match pos_count."""
    assert len(pos)==L['pos_count'], f"expected {L['pos_count']} positions, got {len(pos)}"
    o=bytearray()
    for x,y,z in pos: o+=struct.pack('<3f',x,y,z)
    L['positions']=bytes(o)

def set_triangles(L, tris):
    """Replace the index buffer. Count must match idx_count//3."""
    assert len(tris)*3==L['idx_count'], f"expected {L['idx_count']//3} triangles, got {len(tris)}"
    o=bytearray()
    for a,b,c in tris: o+=struct.pack('<3H',a,b,c)
    L['indices']=bytes(o)
