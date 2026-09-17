"""Tile generation: MVT encoding and the PMTiles archive that carries it (ADR-0011).

Two small modules with no new dependencies, because both formats are specifications rather than
libraries: ``protobuf`` is the wire format MVT needs, ``mvt`` builds tiles, ``pmtiles`` writes and
reads the archive. Keeping them here -- instead of calling ``ST_AsMVT`` or a tile server -- is what
lets the fixtures driver produce exactly the same bytes as the PostGIS driver (ADR-0014).
"""

from __future__ import annotations
