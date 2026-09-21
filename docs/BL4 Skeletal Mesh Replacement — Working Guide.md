# BL4 Skeletal Mesh Replacement — Working Guide
## The core principle

**Start from the vanilla cooked part and replace only the geometry inside it. Never cook your own asset and try to make it pass as a Gestalt part.**

BL4 character bodies use a Gearbox class called `SeparatedGestaltSkeletalMeshPart`. The customisation system — tinting, material application, part swapping — binds to that class and to the package structure Gearbox shipped. Rebuild any of it and the mesh may still load and even render, but tinting and materials silently stop working.

### Why cook-and-transplant fails

The intuitive route is to build in Unreal, cook, then convert the result into a Gestalt part by editing the class, name map and imports. It does not work, and the failures mask each other:

- Unreal 5.5 writes the **ObjectDataResource table at version 2**; BL4 writes version 1. Version 2 adds one byte per entry, so BL4 walks the table with the wrong stride and crashes.
- UAssetGUI reorders the import table on save without fixing the `OuterIndex` values that point into it. The Skeleton and PhysicsAsset end up claiming to live in each other's packages, and a mesh whose Skeleton import fails to resolve loads but draws nothing.
- The export's `TemplateIndex` must point at the class default object. Table edits shift it; a wrong Template means the loader uses the wrong archetype and the entire binary block fails to parse.
- Vertex formats, LOD layout and streaming metadata all differ between a 5.5 cook and what BL4 expects.

Fixing one makes the next appear, which reads as "it got worse" when it actually got further.

### What working mods actually do

Two modders solved this independently. Their output tells the story:

- **Mod A** (topology preserved): `.uasset` **byte-identical** to vanilla. Only vertex positions and `ImportedBounds` changed inside `.uexp` / `.ubulk`.
- **Mod B** (topology changed, `.uexp` grew 124 bytes): exactly **two int32 fields** changed in the `.uasset` — the export's `SerialSize` and `BulkDataStartOffset`, both by the size delta.

Name map, imports, exports, Template and data-resource table: untouched in both. The `.uasset` is a shell you inherit. All real work happens in `.uexp` and `.ubulk`.

## Formats and scale

### Which format carries what

No single interchange format carried everything, so the working pipeline uses two together.

| Data | PSK (Blender export) | FBX (Unreal export) | FModel PSK export |
| --- | --- | --- | --- |
| Positions | yes | yes | yes |
| UVs | yes | yes | yes |
| Faces + material | yes | yes | yes |
| Skin weights | yes | yes | yes |
| Reference skeleton | yes (392 bones) | yes | yes |
| **Vertex normals** | **no** | **yes** | yes |

The Blender PSK exporter (`io_scene_psk_psa`) does not write the `VTXNORMS` chunk regardless of settings. FModel's PSK export usually does.

### Two valid source routes — do not mix them

**Route 1 (preferred, verified in-game): FModel PSKs only.** Cook your edited mesh in Unreal, package it, then extract the PSKs back out with FModel. Those files carry positions, UVs, faces, materials, weights, skeleton **and normals**, already in centimetres. No FBX needed.

**Route 2 (fallback): Blender PSKs + FBX normals.** If an extraction comes out without `VTXNORMS`, take geometry from the Blender PSKs and normals from an FBX exported from Unreal. This also produces a working build, but it needs the FBX, a scale correction and index alignment between two sources — three things that can go wrong for no benefit.

**These sources are not interchangeable.** FModel extracts in *cooked* vertex order; the FBX is in Unreal's *editor* order. Counts match but ordering does not — measured at \~2% agreement. Pairing FModel PSK geometry with FBX normals produces silently wrong shading. Whichever route you pick, every file must come from that same route.

### What the builder reads, by data type

The split is by data type, not by LOD — both sources are read for **all four LODs**:

- **Four PSK files** (`LOD0` … `LOD3`) — positions, UVs, faces, material assignment, skin weights, skeleton.
- **FBX (route 2 only)** — **vertex normals only**, for every LOD including LOD0.

It is not "FBX for the LODs, PSK for the edit".

That works because both come from the same export and their vertex ordering is identical — verified at 100% index-for-index on all four LODs. If you change the export route for one, re-verify the alignment before trusting it.

### Scale: the trap that bites twice

**Unreal works in centimetres. Blender defaults to metres. The cooked buffer is centimetres.**

For the reference upper body, correct cooked values are:

```
X  -13.26 ..  15.22
Y  -62.68 ..  62.68
Z  109.05 .. 175.62      (a ~175 cm tall figure)
```

What this means in practice:

- **FModel's PSK export is already in centimetres.** Imported into Blender it looks enormous against the metre grid. That is correct — do not rescale it.
- **Blender's PSK export wrote metres** (Z 1.09–1.76), 100× too small. The FBX from Unreal was correct at centimetres.
- Applying 0.01 on export was right *when Unreal was in the pipeline*. It is wrong now. The pipeline is PSK in, PSK out, **no scaling at either end**.

On route 1 there is nothing to correct: FModel's PSKs are already in centimetres, so the build runs at `--scale 1.0`. On route 2 the builder derives the factor from the FBX, which is authoritative. Either way, set your Blender exporter to 100 so the files are correct standalone.

### Verifying scale in one line

Before building, check the bounding box against vanilla. If Z tops out near 1.75 instead of 175, you are in metres.

## The workflow

### 1. Extract the vanilla part

Pull the cooked part out of the game utoc/ucas/paks with retoc to convert to legacy non-IOStore format, use FModel to get asset paths. You need all three files together:

```
SKPart_<Character>_<Part>.uasset    the package shell
SKPart_<Character>_<Part>.uexp      descriptors + inline LOD3
SKPart_<Character>_<Part>.ubulk     streamed LOD0-2 payloads
```

Also export the mesh as PSK from FModel for each LOD. These are your reference geometry and the source of the budgets.

### 2. Edit in Blender

Import the FModel PSK. It arrives in centimetres and will look huge against the default grid — leave it alone, that is correct.

Constraints to respect while modelling:

- **Stay under the vanilla vertex and triangle counts** for every LOD (see Budgets below). You can go under freely; you cannot go over.
- **Keep the material assignment clean.** Every vertex must belong to exactly one material — a vertex shared between faces of two materials forces a split the builder does not perform. The number of material slots must also match vanilla, since sections are derived from them.
- **Keep the skeleton intact.** All 392 bones, same names, same order. The builder maps bone indices straight across and will not remap.

### 3. Generate LODs

You need four. Two routes work:

- **Unreal as a decimator only.** Import, let it auto-generate LODs, export all four as FBX. You are using Unreal for reduction and throwing away its cooked output. This is what was used successfully.
- **Blender Decimate.** Collapse mode. Vanilla's ratios are roughly 0.65, 0.125 and 0.06 of LOD0 triangles.

Vanilla also drops bones at lower LODs (`RequiredBones` goes 392 → 392 → 183 → 166) and reduces influences (7 → 4 → 2 → 1). The builder recomputes bone maps per section automatically, so this needs no manual handling.

### 4. Export

Unreal cannot export PSK, so the route that produces complete source files is a round trip: **cook the edited mesh in Unreal, package it, then extract the PSKs back out with FModel.** Those carry normals and are already in centimetres, and nothing else is needed.

If an extraction comes out without `VTXNORMS`, fall back to Blender PSKs plus an FBX from Unreal for the normals — but then *all* files must come from that Blender/Unreal export, never mixed with FModel output (see Formats above).

If the PSK exporter complains about root bones: the usual cause is that the armature has no single root — all bones parented to the node where the root normally sits. Create a root bone and parent everything to it. Exporting with a one-bone armature "fixes" the error but discards all weights, which is useless.

### 5. Build and repack

Run the builder, then repack the three output files into a mod pak with your usual tooling.

## Budgets and padding

### The rule

**Buffer lengths must not change.** The `.ubulk` has to stay exactly the size the data-resource table says, or that table needs rewriting too. So each LOD's vertex and index buffers are fixed-size containers, and your geometry has to fit inside them.

You may go **under** the vanilla counts freely. You may never go over.

### Reference budgets (CorpoHacker upper body)

| LOD | vertices | triangles | container |
| --- | --- | --- | --- |
| 0 | 18,234 | 28,386 | `.ubulk` |
| 1 | 12,941 | 18,449 | `.ubulk` |
| 2 | 3,150 | 3,547 | `.ubulk` |
| 3 | 1,696 | 1,702 | inline in `.uexp` |

These come from the vanilla section descriptors and differ per part — read them from whichever part you are replacing rather than assuming.

LOD2 and LOD3 are the tight ones. A decimated mesh usually fits, but check them first.

### How padding works

Two techniques, both taken from the working mods:

- **Orphan vertices.** Surplus vertex slots are filled with copies of the last real vertex, referenced by no triangle. They sit in the buffer as dead data. One working mod parks 2,522 of them.
- **Degenerate triangles.** Surplus index slots are filled with repeated indices, so the triangle collapses to zero area and renders nothing. One working mod has 4,357.

The section descriptors then cover only the real geometry. Anything past the section range is never drawn.

This is also how geometry gets *removed* without changing counts, which is worth knowing when reading other people's mods: orphan vertices whose positions trace the original shape are the fingerprint of deleted geometry.

### A useful consequence

If the part you want to modify already has orphan vertices in vanilla, the geometry they describe can be restored by writing faces that reference them — no count changes at all. Worth checking with a PSK orphan count before assuming you need to add anything.

## Cooked format reference

### File roles

- **`.uasset`** — package header: name map, imports, exports, data-resource table. Inherited from vanilla, two fields patched.
- **`.uexp`** — export data. Properties, then a binary tail ("Extras" in UAssetGUI) holding bounds, materials, skeleton and per-LOD descriptors. LOD3's payload is inline here.
- **`.ubulk`** — streamed payloads for LOD0, LOD1, LOD2, back to back.

### `.uexp` binary tail

```
strip flags            2
ImportedBounds         56   7 x float64: origin xyz, extent xyz, radius
SkeletalMaterials      4 + 40*count
RefSkeleton bones      4 + 12*count    FName 8 + ParentIndex 4
RefBonePose            4 + 80*count    FTransform, doubles
NameToIndexMap         4 + 12*count
unknown i32, LODCount i32
per LOD:
    strip 2, header 8
    RequiredBones      4 + 2*count
    Sections           4 + N section records
    ActiveBoneIndices  4 + 2*count
    streamed LOD       77-byte availability block
    inline LOD         4-byte size + full payload
NaniteResources        68 bytes, all zero
```

### Section record

```
strip 2, MaterialIndex u16, BaseIndex i32, NumTriangles i32,
flags 21, BoneMap (4 + 2n), NumVertices i32,
MaxBoneInfluences i32, flags 22,
DupVertData (4 + 4n), DupVertIndexData (4 + 8n), flags 4
```

There is **one section per material**, in ascending material order. Parts differ: the CorpoHacker upper body has two, the lower body has three. Read the count from vanilla rather than assuming.

`BaseIndex` and `BaseVertexIndex` are cumulative — each section starts where the previous one ended. Index buffers hold **absolute** vertex indices, and a section's `BaseVertexIndex` equals its first index.

Two fields sit inside the 21 otherwise-undecoded bytes that follow `NumTriangles`:

```
offset  0..3   bRecomputeTangent
offset  4      RecomputeTangentsVertexMaskChannel (3 = Alpha)
offset  5..8   bCastShadow
offset  9..12  bVisibleInRayTracing
offset 13..16  BaseVertexIndex      <- must be rewritten when the split changes
offset 17..20  ClothMappingData count
```

### LOD payload (same layout in `.ubulk` and inline)

```
index buffer     strip 2, DataTypeSize 1, elemSize 4, count 4, data
position buffer  stride 4, numVerts 4, elemSize 4, count 4, data
static vertex    strip 2, NumTexCoords 4, numVerts 4,
                 bFullPrecisionUVs 4, bHighPrecTangents 4,
                 tangents, uvs
skin weights     strip 2, f0 4, MaxBoneInfluences 4, influenceCount 4,
                 numVerts 4, f1 f2 f3 12, byteCount 4, data
trailer          40 bytes, identical across every LOD
```

### Vertex encodings

| Component | Bytes | Encoding |
| --- | --- | --- |
| Position | 12 | 3 × float32, full precision |
| Tangent basis | 8 | 2 × 4×int8 — TangentX, then Normal; W = 127 |
| UV | 4 | 2 × float16, one channel |
| Skin weights | 8 or 16 | N bone indices (uint8, into the section BoneMap) then N weights (uint8, summing to 255). 16 bytes when `MaxBoneInfluences > 4` |
| Index | 2 | uint16 |

Per-LOD buffer overhead is a constant 135 bytes.

### Winding

The cooked convention winds triangles so the **geometric normal points opposite the vertex normal**. PSK negates Y, and that mirror reverses handedness — so triangle order must be flipped on the way back in. Getting this wrong renders the mesh inside-out: the silhouette disappears and you see interior faces.

## The toolchain

All pure Python, no dependencies.

| Script | Role |
| --- | --- |
| `bl4mesh.py` | LOD payload codec — parse and re-serialise, byte-exact. Also `vertices()`, `triangles()`, `set_positions()`, `set_triangles()` |
| `bl4uexp.py` | `.uexp` structure parser. Records the byte offset of every editable field so edits are in-place patches; undecoded bytes are never rewritten |
| `bl4psk.py` | ActorX PSK reader — points, wedge UVs, faces, materials, weights, skeleton, normals |
| `bl4build.py` | Builds one LOD payload: section split by material, vertex ordering, bone maps, tangent generation, padding |
| `build_part.py` | The build command. Preflight, four LODs, assembles `.uexp` and `.ubulk`, patches the `.uasset`, postflight |
| `verify_part.py` | Standalone validator for files already on disk — someone else's build, or a re-check after repacking |
| `fbx.py` | Binary FBX 7.x reader. Only needed for route 2, where normals come from an FBX |

### Running a build

```
python3 build_part.py \
    --vanilla /path/to/SKPart_CorpoHacker_UpperBody \
    --psk LOD0.psk LOD1.psk LOD2.psk LOD3.psk \
    --out ./built
```

`--vanilla` is a path **without** extension; `.uasset`/`.uexp`/`.ubulk` are appended. `--psk` takes one file per LOD in order. Output files are written to `--out` under the vanilla part's own base name, ready to repack.

Two optional flags:

- `--scale` multiplies source positions. Leave at 1.0 for centimetres; use 100 if your PSKs came out in metres.
- `--base-vertex-index` is `computed` (default) or `vanilla`. See the pitfall below — the game ignores the field either way, but FModel's preview may only work with `vanilla`.

The build reports per-LOD counts against budget, padding applied, payload sizes against vanilla, and the `.uasset` delta. If payload sizes come out identical to vanilla, the `.ubulk` length is unchanged and the data-resource table needs no edit — the simplest case.

Nothing is part-specific: buffer offsets, budgets, LOD count, which LOD is inline and the section count are all read from the vanilla files, so the same command works on any part.

### What the builder patches in the `.uasset`

Only two fields, both by the `.uexp` size delta:

- export 1 `SerialSize`
- `BulkDataStartOffset` (at `0x110` in the reference part)

### Verification is built in

The build runs checks itself and **writes nothing if any of them fail**:

- **Preflight** round-trips the vanilla payloads through the codec. If vanilla does not come back byte-identical, the format model does not fit that part and the build stops before touching your geometry.
- **Postflight** validates the assembled bytes in memory: header offsets, payload parsing, sections inside their buffers, `BaseVertexIndex`/`BaseIndex` chaining, and triangle winding against vanilla's convention.

Both gates are fault-tested: reverting the winding flip or the `BaseVertexIndex` patch produces an explicit failure and no output.

`verify_part.py` runs the same structural checks against files already on disk, for anything the build did not produce.

## Pitfalls, by symptom

### Mesh renders inside-out

Only interior faces visible, silhouette gone. **Triangle winding is reversed.** Negating Y to convert PSK to cooked space is a mirror, which flips handedness. Flip the index order: `(a,b,c)` → `(a,c,b)`.

Note that comparing triangles as *sets* of indices will not catch this — it passes at 100% while every face points the wrong way. Compare the geometric normal against the vertex normal instead: vanilla should show near-0% agreement.

### Mesh is invisible but the game does not crash

Usually the **Skeleton import failed to resolve**. Check that import 13's `OuterIndex` points at the skeleton's package and import 11's at the PhysicsAsset package. UAssetGUI reorders imports without updating these.

If the imports are right, check `ImportedBounds`. Zero or tiny bounds cull the mesh everywhere, and FModel renders regardless of bounds so it will look fine there.

### Game crashes on loading the mesh

Check the **ObjectDataResource version**. Version 2 in a BL4 asset will crash; it must be version 1.

### FModel opens but shows `null` for everything

`ImportedBounds: null`, `LODModels: null`, and usually a `Template` that is not the class default object. The export's `TemplateIndex` is wrong — it must point at `Default__SeparatedGestaltSkeletalMeshPart`.

### UAssetGUI: "count must be a non-negative value"

A header offset is stale. When the name map changes size, every offset past it must shift — including `BulkDataStartOffset`, which is larger than the header and therefore missed by range-based heuristics.

### Everything loads, but tinting and materials do not work

The package structure was rebuilt rather than inherited. Go back to the vanilla shell.

### Mesh is 100× too small or too large

Metres vs centimetres. See the scale section.

### PSK export has no weights

Exported with a one-bone armature. Fix the root bone properly instead.

### Build fails with an index error in the bone map

`IndexError` inside `bonemap()`. The source mesh has **more material slots than the builder produced sections for**, so some vertices never entered the ordering. Check the material count against vanilla — the lower body has three where the upper body has two. Current builds fail with an explicit message naming both counts instead.

### FModel will not preview, but the game renders fine

Seen on both this project's builds and other people's working mods. Not fully explained.

One contributing factor is `BaseVertexIndex`. Setting it correctly (`--base-vertex-index computed`, the default) stops FModel previewing; leaving vanilla's values (`--base-vertex-index vanilla`) restores the preview for a two-section part. The game ignores the field either way — a build declaring a section running 1,855 vertices past the end of its buffer still renders and customises correctly — so both are safe in-game.

A three-section part did not preview under either setting, so FModel has at least one further issue beyond this field.

**Treat the game as the authority.** FModel's preview is a convenience, not a correctness criterion.

### General principle

These errors mask each other. "It got worse" often means "it got further" — a crash after a run of silent failures usually means the loader is now reaching code it could not reach before. Work symptom by symptom and re-validate structure after every change.

## Validation and known approximations

### Before repacking

`build_part.py` runs these automatically and refuses to write if any fail, so in normal use there is nothing to do. They are listed here because they are what "valid" means, and `verify_part.py` checks the same set against files already on disk:

- `sum(export SerialSize) == len(.uexp) - 4`
- `TotalHeaderSize == len(.uasset)`; each export's `SerialOffset` is the running total from the header
- `BulkDataStartOffset == TotalHeaderSize + len(.uexp) - 4`
- data-resource version is 1, and its `SerialSize` entries sum to the `.ubulk` length
- `.uexp` parses with every byte accounted for
- each LOD payload parses to exactly its declared size
- section vertex and triangle sums ≤ buffer counts; the last section's index range lies inside the buffer
- `BaseVertexIndex` and `BaseIndex` chain cumulatively through the sections
- triangle winding falls on the same side as vanilla's convention
- geometry bounds match vanilla to \~2 decimals

### Then check visually

Try FModel first when it works — it is much faster than a game launch. Look for a solid silhouette from outside, sane shading across UV seams, and correct material assignment. But see the pitfall above: a build can be structurally perfect and still not preview, so a missing preview is not by itself a defect.

The real test is in-game: the mesh renders in the menu and the world, deforms correctly under animation, and tinting and customisation still work — that last one being what the whole vanilla-shell approach exists to protect.

### Known approximations

Two deliberate shortcuts in the current builder, neither of which prevented a working result:

- **Tangents are computed from UV derivatives** rather than taken from the source. Normals are exact; the tangent basis is reconstructed. Any artifact would show as odd specular behaviour along UV seams.
- **Duplicated-vertices buffers are written empty.** These feed recompute-tangents. Vanilla has real data there (11,406 entries for LOD0 section 0), and emptying them is what shrinks the `.uexp` by \~411 KB. Leaving them empty was checked in-game against the vanilla mesh and produced no visible seams, so it is the recommended default. Both vanilla sections have `bRecomputeTangent` set to false, so nothing reads these buffers for this part — populate them only if you enable that flag, work on a cloth section, or a seam actually shows.

Also worth noting: the bounds radius comes out slightly tighter than vanilla (70.71 vs 72.38) because it is the exact bounding sphere of the new geometry. If the mesh culls early at extreme viewing angles, widen it.

### Verified working

Both the CorpoHacker upper body (two sections) and lower body (three sections) build, load, render in the menu and the world, and support tinting and the customisation system. Topology can change freely between iterations provided every LOD stays under its budget.
