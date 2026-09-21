# BL4 Mesh Injector
## What it is
A tool to inject your edited mesh, created from a vanilla mesh, into vanilla uasset/ubulk/uexp.

This is necessary because a standard Unreal mesh is not a Gearbox custom **SeparatedGestaltMeshPart** class mesh, and thus does not support tinting and material application in player model customisation.
### This guide does not cover using Blender, Unreal, FModel and retoc!
## How to - Workflow
1. Create a mesh in modelling software, such as Blender
2. Export to Unreal and set it up correctly
#### Important: mesh streaming enabled, 4 LODs created!
3. Cook and package it 
4. Use FModel to export LODs 0-3 as PSKs
5. Use retoc to export vanilla assets to be replaced as legacy/non-IOStore uasset/ubulk/uexp
6. Use build_part.py to inject your mesh into vanilla uasset/ubulk/uexp
7. Package the modified vanilla assets
## Example - Setup Mesh Injector
### Folders
- assets -> vanilla assets
- out -> output folder
- psks -> PSKs folder
### Example - Arguments
#### Vanilla assets path (without file extentions in the path!)
--vanilla assets/SKPart_CorpoHacker_UpperBody
#### PSKs path (with file extentions in the path!)
--psk psks/SKPart_CorpoHacker_UpperBody.psk psks/SKPart_CorpoHacker_UpperBody_LOD1.psk psks/SKPart_CorpoHacker_UpperBody_LOD2.psk psks/SKPart_CorpoHacker_UpperBody_LOD3.psk
#### Output folder
--out "output"

Run build_part.py on the vanilla assets to inject PSK data into vanilla assets and write modified copies of the vanilla assets into the output folder.

### Detailed technical info can be found in [docs](https://github.com/Phnxsmv/BL4MeshInjector/blob/main/docs/BL4%20Skeletal%20Mesh%20Replacement%20%E2%80%94%20Working%20Guide.md)