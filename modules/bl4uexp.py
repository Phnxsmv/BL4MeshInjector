"""BL4 SeparatedGestaltSkeletalMeshPart - .uexp structure parser.

Full map of the export's binary tail ("Extras" in UAssetGUI):

    strip flags            2
    ImportedBounds         56   (7 x float64: origin xyz, extent xyz, radius)
    SkeletalMaterials      4 + 40*count
    RefSkeleton bones      4 + 12*count   (FName 8 + ParentIndex 4)
    RefBonePose            4 + 80*count   (FTransform: quat+pos+scale, doubles)
    NameToIndexMap         4 + 12*count
    unknown i32, LODCount i32
    per LOD:
        strip 2, header 8
        RequiredBones      4 + 2*count
        Sections           4 + N section records
        ActiveBoneIndices  4 + 2*count
        streamed LOD       77-byte availability block (payload lives in .ubulk)
        inline LOD         4-byte size + full payload (same layout as .ubulk)
    NaniteResources        68 bytes (all zero for these parts)

Section record:
    strip 2, MaterialIndex u16, BaseIndex i32, NumTriangles i32, flags 21,
    BoneMap (4 + 2n), NumVertices i32, MaxBoneInfluences i32, flags 22,
    DupVertData (4 + 4n), DupVertIndexData (4 + 8n), flags 4

Every field that may need editing is recorded with its byte offset so changes
are applied as in-place patches; bytes we haven't decoded are never rewritten.
"""
import struct

class _R:
    def __init__(s,b,o): s.b,s.o=b,o
    def u16(s): v=struct.unpack_from('<H',s.b,s.o)[0]; s.o+=2; return v
    def i32(s): v=struct.unpack_from('<i',s.b,s.o)[0]; s.o+=4; return v
    def raw(s,n): v=bytes(s.b[s.o:s.o+n]); s.o+=n; return v

AVAIL_BLOCK = 77
# field offsets inside the availability block
AV = dict(streamedSize=0, dataTypeSize=8, indexCount=9, numVerts1=17,
          posStride=29, numVerts2=33, maxBoneInfl=49,
          influenceCount=53, numVerts3=57)

def _section(r):
    S={'off':r.o}
    r.raw(2)
    S['off_MaterialIndex']=r.o;     S['MaterialIndex']=r.u16()
    S['off_BaseIndex']=r.o;         S['BaseIndex']=r.i32()
    S['off_NumTriangles']=r.o;      S['NumTriangles']=r.i32()
    S['off_BaseVertexIndex']=r.o+13
    S['BaseVertexIndex']=struct.unpack_from('<i',r.b,r.o+13)[0]
    r.raw(21)
    S['off_BoneMapCount']=r.o; n=r.i32(); S['BoneMapCount']=n
    S['off_BoneMap']=r.o; S['BoneMap']=list(struct.unpack_from(f'<{n}H',r.b,r.o)); r.raw(2*n)
    S['off_NumVertices']=r.o;       S['NumVertices']=r.i32()
    S['off_MaxBoneInfluences']=r.o; S['MaxBoneInfluences']=r.i32()
    r.raw(22)
    S['off_DupVertDataCount']=r.o;  n=r.i32(); S['DupVertDataCount']=n
    S['off_DupVertData']=r.o; r.raw(4*n)
    S['off_DupVertIndexCount']=r.o; n=r.i32(); S['DupVertIndexCount']=n
    S['off_DupVertIndex']=r.o; r.raw(8*n)
    r.raw(4)
    S['size']=r.o-S['off']
    return S

def parse(uexp, extras_start, export_end):
    r=_R(uexp, extras_start); M={'extras_start':extras_start}
    r.raw(2)
    M['off_bounds']=r.o; M['ImportedBounds']=struct.unpack_from('<7d',uexp,r.o); r.raw(56)
    M['MaterialCount']=r.i32(); M['off_materials']=r.o; r.raw(40*M['MaterialCount'])
    M['BoneCount']=r.i32();     M['off_bones']=r.o;     r.raw(12*M['BoneCount'])
    M['PoseCount']=r.i32();     M['off_pose']=r.o;      r.raw(80*M['PoseCount'])
    M['NameMapCount']=r.i32();  M['off_namemap']=r.o;   r.raw(12*M['NameMapCount'])
    M['unk']=r.i32(); M['LODCount']=r.i32()
    M['LODs']=[]
    for i in range(M['LODCount']):
        L={'index':i,'off':r.o}
        r.raw(2); r.raw(8)
        L['off_RequiredBonesCount']=r.o; n=r.i32(); L['RequiredBonesCount']=n
        L['off_RequiredBones']=r.o; r.raw(2*n)
        n=r.i32(); L['Sections']=[_section(r) for _ in range(n)]
        L['off_ActiveBonesCount']=r.o; n=r.i32(); L['ActiveBonesCount']=n
        L['off_ActiveBones']=r.o; r.raw(2*n)
        # streamed LODs carry a 77-byte availability block; the inline LOD
        # carries a 4-byte size followed by the payload itself.
        size=struct.unpack_from('<i',uexp,r.o)[0]
        probe=uexp[r.o+4:r.o+6]
        if probe==b'\x05\x00' and 0<size<len(uexp):
            L['inline']=True; L['off_payloadSize']=r.o; r.i32()
            L['off_payload']=r.o; L['payloadSize']=size; r.raw(size)
        else:
            L['inline']=False; L['off_avail']=r.o; r.raw(AVAIL_BLOCK)
            L['avail']={k:struct.unpack_from('<i',uexp,r.o-AVAIL_BLOCK+v)[0] for k,v in AV.items()}
        M['LODs'].append(L)
    M['off_nanite']=r.o; M['NaniteBytes']=export_end-r.o
    M['consumed']=r.o-extras_start
    M['exact']= (r.o+M['NaniteBytes'])==export_end
    return M

def patch_i32(buf,off,v): struct.pack_into('<i',buf,off,v)
def patch_u16(buf,off,v): struct.pack_into('<H',buf,off,v)
def patch_bounds(buf,M,origin,extent,radius):
    struct.pack_into('<7d',buf,M['off_bounds'],*origin,*extent,radius)
