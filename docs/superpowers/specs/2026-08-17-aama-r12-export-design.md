# PatternMate AAMA/R12 Export Design

## Goal

Make PatternMate exports structurally recognizable as apparel pattern DXF files by ET, PAD, Gerber, and Fuyi-class software, while remaining valid AutoCAD R12 ASCII DXF.

The first delivery covers DXF piece exchange only. Grading-rule `.rul` generation is outside this change.

## Compatibility Profile

The exporter will emit the same high-level container used by the repository's known apparel DXF inputs:

1. `999 / ANSI/AAMA` identification comment.
2. A `BLOCKS` section containing one block per pattern piece.
3. An `ENTITIES` section containing one `INSERT` per emitted piece block.
4. A terminating `EOF` record.

The output will use only R12-compatible entities: `POLYLINE`, `VERTEX`, `SEQEND`, `LINE`, `POINT`, and `TEXT`. It will not emit `LWPOLYLINE`, `OBJECTS`, subclass markers, custom extended data, or post-R12 header variables.

## Piece Grouping

Entities are grouped by non-empty `piece_id`. A stable ASCII block name is generated from the garment role and group order, for example `PM.FRONT_BODY.01`. The original `piece_id` remains represented in the export report rather than being written as non-portable DXF metadata.

Entities without a `piece_id` are not silently merged into a valid garment piece. They are skipped and counted in the report. A piece block is emitted only if it contains at least one writable geometry entity.

Each emitted block has exactly one matching `INSERT` record at origin `(0, 0)`. Coordinates remain in their composed world positions; block insertion does not apply a second translation.

## Apparel Layer Mapping

The AAMA-oriented output uses numeric function layers instead of PatternMate-specific `AI4M_*` layers:

| Layer | Meaning | PatternMate input |
| --- | --- | --- |
| `1` | Piece boundary / cut contour | `cut`, `pattern_boundary`, `outer_contour`, and other piece outline geometry |
| `4` | Notch / drill mark | `notch` |
| `7` | Grainline | `grainline` |
| `8` | Internal/reference line | `construction`, `pleat_line`, `text`, and unknown non-boundary geometry |
| `11` | Stitch/seam line | `seam`, `stitch`, `seam_line` |

Piece role no longer controls the layer. Piece identity is represented by the enclosing block; layer represents manufacturing function.

## Geometry Rules

- A geometry with exactly two points is written as `LINE` unless it is explicitly a closed cut contour.
- A geometry with three or more points is written as classic `POLYLINE/VERTEX/SEQEND`.
- Repeated closing points are removed from the vertex list and represented by POLYLINE flag bit `1`.
- Explicitly closed geometry and `cut` contours with at least three vertices are closed.
- Non-finite coordinates and geometry with fewer than two usable points are skipped and reported.
- Optimization remains enabled by the production endpoint, but export validation runs on the optimized result actually written.

## Export Report and Failure Behavior

`write_entities_dxf` keeps its existing call signature. Its report will identify the format as `aama_r12_blocks` and include:

- emitted block and insert counts;
- written and skipped entity counts;
- skipped ungrouped entity count;
- closed polyline count;
- emitted piece IDs and block names;
- byte length.

The writer raises `ValueError` instead of returning a misleading file when no valid piece block can be emitted. The `/export` endpoint will surface this as an export failure rather than packaging an empty DXF.

## Validation

Automated tests will verify:

1. `ANSI/AAMA` banner and R12-safe entity set.
2. No `LWPOLYLINE`, `AI4M_*` layer, or post-R12 header variable.
3. One block and one insert per valid `piece_id`.
4. Numeric layer mapping for boundary, notch, grainline, construction, and stitch geometry.
5. Closed contour encoding and removal of duplicate closing vertices.
6. Ungrouped and invalid geometry reporting.
7. Empty export rejection.
8. Pair structure, balanced sections, balanced `BLOCK/ENDBLK`, and `POLYLINE/SEQEND` sequences.
9. A representative composed PatternMate export passes the structural validator.

Repository-wide geometry tests and the frontend build will be rerun after the focused exporter tests.

## External Acceptance Gate

Local validation can establish R12 and AAMA-like structural compatibility, but cannot certify proprietary import behavior. Final acceptance requires opening one representative T-shirt and one representative shirt export in at least one installed target application, then checking that individual pieces are recognized rather than imported as unrelated loose lines.

