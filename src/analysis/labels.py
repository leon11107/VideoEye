"""Human-readable labels for codec-specific block fields.

Keeps codec specifics (AV1 prediction modes, H.264 partition shapes, HEVC
CU sizing) out of the views so the Block Info panel stays codec-agnostic:
it asks for a label and renders whatever string it gets back.
"""

# libaom PREDICTION_MODE enum order (aom/av1/common/enums.h). The sidecar
# stores this value in BLOCK_DTYPE["mode"] for AV1.
_AV1_PRED_MODES = (
    "DC", "V", "H", "D45", "D135", "D113", "D157", "D203", "D67",
    "SMOOTH", "SMOOTH_V", "SMOOTH_H", "PAETH",
    "NEARESTMV", "NEARMV", "GLOBALMV", "NEWMV",
    "NEAREST_NEARESTMV", "NEAR_NEARMV", "NEAREST_NEWMV", "NEW_NEARESTMV",
    "NEAR_NEWMV", "NEW_NEARMV", "GLOBAL_GLOBALMV", "NEW_NEWMV",
)

# H.264 partition shape codes (veye_sidecar SHAPE_*) stored in
# BLOCK_DTYPE["mode"] for H.264.
_H264_SHAPES = {
    0: "16x16", 1: "16x8", 2: "8x16", 3: "8x8",
    4: "Intra 16x16", 5: "Intra 4x4", 6: "I_PCM", 7: "Skip", 8: "Direct",
}


def block_type_label(codec: str, mode: int) -> str:
    """Name for a coding block's BLOCK_DTYPE['mode'] value, per codec."""
    if codec == "av1":
        if 0 <= mode < len(_AV1_PRED_MODES):
            return _AV1_PRED_MODES[mode]
        return f"mode {mode}"
    if codec == "hevc":
        # HEVC stores cu_log2; the CU is square at 1<<cu_log2 px.
        side = 1 << mode
        return f"CU {side}x{side}"
    return _H264_SHAPES.get(mode, f"shape {mode}")


def qp_field_name(codec: str) -> str:
    """Label for the QP-like value (AV1 carries qindex, not a 0..51 QP)."""
    return "qindex" if codec == "av1" else "qp"
