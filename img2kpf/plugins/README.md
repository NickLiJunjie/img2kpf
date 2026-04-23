# Plugins

Plugins live under:

- `img2kpf/plugins/<plugin_id>/`

Each plugin directory uses a small manifest:

- `plugin.json` — metadata and archive entry
- optional user-provided resources such as zip files or models

## Public-repo policy

- Third-party plugin archives are **not** committed to git.
- Users must download optional plugin archives themselves.
- The repository keeps only the manifest and setup notes.

## Supported KFX Output references

Both `kfx_direct.py` and `kpf_generator.py --emit-kfx` accept:

- a plugin ID such as `kfx_output`
- a plugin directory such as `img2kpf/plugins/kfx_output`
- a direct zip path such as `/path/to/KFX Output.zip`
