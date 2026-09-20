"""Minimal ActorX PSK reader for the BL4 mesh pipeline.

FModel exports with Y negated relative to Unreal; from_psk() undoes that.
"""
import struct

def load(path):
    d=open(path,'rb').read(); o=0; C={}
    while o+32<=len(d):
        cid=d[o:o+20].split(b'\x00')[0].decode()
        f,ds,dc=struct.unpack_from('<iii',d,o+20)
        C[cid]=(o+32,ds,dc); o+=32+ds*dc
    M={}
    po,_,pc=C['PNTS0000']
    M['points']=[struct.unpack_from('<3f',d,po+12*i) for i in range(pc)]
    wo,_,wc=C['VTXW0000']
    M['wedge_point']=[struct.unpack_from('<i',d,wo+16*i)[0] for i in range(wc)]
    M['wedge_uv']=[struct.unpack_from('<2f',d,wo+16*i+4) for i in range(wc)]
    M['wedge_mat']=[d[wo+16*i+12] for i in range(wc)]
    fo,_,fc=C['FACE0000']
    M['faces']=[struct.unpack_from('<3H',d,fo+12*i) for i in range(fc)]
    M['face_mat']=[d[fo+12*i+6] for i in range(fc)]
    if 'VTXNORMS' in C:
        no,_,nc=C['VTXNORMS']
        M['normals']=[struct.unpack_from('<3f',d,no+12*i) for i in range(nc)]
    ro,_,rc=C['RAWWEIGHTS']
    M['weights']=[struct.unpack_from('<fii',d,ro+12*i) for i in range(rc)]
    so,_,sc=C['REFSKELT']
    M['bones']=[d[so+120*i:so+120*i+64].split(b'\x00')[0].decode('ascii','replace') for i in range(sc)]
    return M

def from_psk(p): return (p[0], -p[1], p[2])
