"""Load and manage versioned segment parameter files.

Segment parameters are stored as JSON files in a configurable directory.
Each file contains demographic, behavioural, and response-model coefficients
for a single customer segment.
"""

import json
from pathlib import Path
from typing import Optional


class SegmentParamsLoader:
    """Load and manage versioned segment parameter files.

    Parameters are cached after first load to avoid repeated disk I/O
    during simulation runs that reference the same segment multiple times.

    Expected file naming convention::

        {params_dir}/{segment_id}.json
        {params_dir}/{segment_id}_v{version}.json
    """

    def __init__(self, params_dir: Path) -> None:
        """Initialise with the directory containing segment param JSON files.

        Args:
            params_dir: Path to directory holding segment JSON files.

        Raises:
            FileNotFoundError: If *params_dir* does not exist.
        """
        self.params_dir = Path(params_dir)
        if not self.params_dir.is_dir():
            raise FileNotFoundError(
                f"Segment params directory not found: {self.params_dir}"
            )
        self._cache: dict[str, dict] = {}

    def load(self, segment_id: str) -> dict:
        """Load parameters for a segment, returning cached data when available.

        Searches *params_dir* for JSON files whose stem contains
        *segment_id*.  The most-recently-modified match wins when multiple
        files match (i.e. the latest version).

        Args:
            segment_id: Unique identifier for the target segment.

        Returns:
            Parsed JSON dictionary of segment parameters.

        Raises:
            FileNotFoundError: If no matching file is found.
        """
        if segment_id in self._cache:
            return self._cache[segment_id]

        candidates = sorted(
            self.params_dir.glob(f"*{segment_id}*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not candidates:
            raise FileNotFoundError(
                f"No parameter file found for segment '{segment_id}' "
                f"in {self.params_dir}"
            )

        with open(candidates[0], "r", encoding="utf-8") as fh:
            data: dict = json.load(fh)

        self._cache[segment_id] = data
        return data

    def list_segments(self) -> list[dict]:
        """List all available segments with id and name.

        Returns:
            List of dicts, each containing at minimum ``id`` and ``name``
            keys extracted from the JSON files.
        """
        segments: list[dict] = []
        seen_ids: set[str] = set()

        for path in sorted(self.params_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                seg_id = data.get("id", path.stem)
                if seg_id not in seen_ids:
                    seen_ids.add(seg_id)
                    segments.append(
                        {
                            "id": seg_id,
                            "name": data.get("name", seg_id),
                            "file": str(path.name),
                        }
                    )
            except (json.JSONDecodeError, OSError):
                continue

        return segments

    def get_version(self, segment_id: str) -> str:
        """Get the current version string for a segment's parameters.

        Args:
            segment_id: Unique identifier for the target segment.

        Returns:
            Version string (e.g. ``"1.0.0"``).  Falls back to ``"unknown"``
            if the file does not contain a ``version`` field.
        """
        data = self.load(segment_id)
        return str(data.get("version", "unknown"))

    def invalidate_cache(self, segment_id: Optional[str] = None) -> None:
        """Clear cached parameters.

        Args:
            segment_id: If provided, only that segment's cache is cleared.
                Otherwise the entire cache is flushed.
        """
        if segment_id is not None:
            self._cache.pop(segment_id, None)
        else:
            self._cache.clear()
