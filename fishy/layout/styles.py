"""Style presets for the fishbone layout (FR-2.8).

All spacing values are in scene units (≈ pixels at 100% zoom). A preset is a
plain frozen dataclass, so adding a new one is a single constructor call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class FishboneStyle:
    name: str
    angle_deg: float = 60.0  # slant of main bones (and sub-sub bones) from horizontal
    max_depth: int = 4  # deeper bones are collapsed on the diagram (FR-1.6)

    # Text
    font_size: float = 11.0
    category_font_size: float = 12.5
    head_font_size: float = 13.0
    label_max_width: float = 170.0
    category_max_width: float = 150.0
    head_max_width: float = 190.0

    # Spacing
    sibling_gap: float = 14.0  # clearance between neighbouring subtrees
    label_gap: float = 6.0  # line end -> label
    first_attach: float = 10.0  # extra clearance before the first child on a bone
    min_branch_len: float = 40.0  # shortest horizontal cause line
    min_main_len: float = 70.0  # shortest category bone (vertical extent)
    branch_inset: float = 14.0  # gap between a parent slant and the first child slant
    spine_lead: float = 40.0  # spine stub to the left of the leftmost category
    head_gap: float = 24.0  # spine end -> head box
    category_pad: float = 6.0  # padding inside category box
    head_pad: float = 10.0

    # Status indicator (FR-2.10)
    status_dot: float = 7.0
    status_gap: float = 4.0
    show_status: bool = True
    pin_size: float = 11.0  # pin marker on pinned bones (FR-3.7)
    ring_size: float = 13.0  # category progress ring (FR-3a.6)

    # Line widths (renderer hints)
    spine_width: float = 3.0
    main_width: float = 2.2
    branch_width: float = 1.3


DEFAULT = FishboneStyle(name="Default")

COMPACT = replace(
    DEFAULT,
    name="Compact",
    angle_deg=64.0,
    font_size=9.5,
    category_font_size=11.0,
    head_font_size=12.0,
    label_max_width=140.0,
    category_max_width=125.0,
    head_max_width=160.0,
    sibling_gap=8.0,
    label_gap=4.0,
    first_attach=5.0,
    min_branch_len=28.0,
    min_main_len=50.0,
    branch_inset=9.0,
    spine_lead=24.0,
    head_gap=16.0,
    category_pad=4.0,
    head_pad=8.0,
    status_dot=6.0,
    status_gap=3.0,
    pin_size=10.0,
    ring_size=11.0,
)

PRESETS: dict[str, FishboneStyle] = {s.name: s for s in (DEFAULT, COMPACT)}
