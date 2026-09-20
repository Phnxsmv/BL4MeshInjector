rem Examples
rem python "%~dp0build_part.py" --vanilla assets/SKPart_CorpoHacker_UpperBody --psk "psks/SKPart_CorpoHacker_UpperBody.psk" "psks/SKPart_CorpoHacker_UpperBody_LOD1.psk" "psks/SKPart_CorpoHacker_UpperBody_LOD2.psk" "psks/SKPart_CorpoHacker_UpperBody_LOD3.psk" --out "output"
rem Optional, if you only want to check integrity of a modded asset against a vanilla asset
rem build_part.py already covers this as postflight check
::python "%~dp0verify_part.py" --vanilla assets/SKPart_CorpoHacker_UpperBody --built out/SKPart_CorpoHacker_UpperBody
rem pause