import struct, zlib

def _prop(b, o):
    t = chr(b[o]); o += 1
    if t in 'YCIFDL':
        fmt = {'Y':'<h','C':'<?','I':'<i','F':'<f','D':'<d','L':'<q'}[t]
        n = struct.calcsize(fmt)
        return t, struct.unpack_from(fmt, b, o)[0], o+n
    if t in 'fdlib':
        ln, enc, clen = struct.unpack_from('<III', b, o); o += 12
        raw = b[o:o+clen]; o += clen
        if enc == 1: raw = zlib.decompress(raw)
        code = {'f':'f','d':'d','l':'q','i':'i','b':'?'}[t]
        return t, list(struct.unpack('<%d%s' % (ln, code), raw)), o
    if t in 'SR':
        ln = struct.unpack_from('<I', b, o)[0]; o += 4
        v = b[o:o+ln]; o += ln
        return t, (v.decode('utf8','replace') if t=='S' else v), o
    raise ValueError('bad prop type %r at %d' % (t, o-1))

def _node(b, o):
    end, nprop, plen = struct.unpack_from('<QQQ', b, o); o += 24
    nlen = b[o]; o += 1
    name = b[o:o+nlen].decode('utf8','replace'); o += nlen
    props = []
    for _ in range(nprop):
        t, v, o = _prop(b, o); props.append((t, v))
    kids = []
    while o < end - 25:
        k, o = _node(b, o)
        if k: kids.append(k)
    if o < end: o = end
    return {'name':name, 'props':props, 'kids':kids}, o

def parse(path):
    b = open(path,'rb').read()
    o = 27
    roots = []
    while o < len(b) - 160:
        end = struct.unpack_from('<Q', b, o)[0]
        if end == 0: break
        n, o = _node(b, o); roots.append(n)
    return roots

def walk(n, depth=0):
    yield depth, n
    for k in n['kids']:
        yield from walk(k, depth+1)

def find(roots, name):
    for r in roots:
        for d, n in walk(r):
            if n['name'] == name: yield n
