Third-party code shipped with Pumpkin, unchanged:

- `pypdf/` (6.21.2): reads text from PDF files. BSD-3-Clause, see LICENSE-pypdf.txt.
- `typing_extensions.py`: needed by pypdf on Python older than 3.11. PSF License, see LICENSE-typing_extensions.txt.

Pumpkin adds this folder to the end of the import path, so a copy you installed yourself is used first.
