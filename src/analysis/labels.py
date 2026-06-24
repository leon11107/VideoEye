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


# libaom FILTER_INTRA_MODE enum (av1/common/enums.h). Filter-intra keeps the
# block's y_mode = DC_PRED, so this is signalled separately (use_filter_intra +
# filter_intra_mode); the sidecar stores the mode 0..4, or -1 if not used.
_AV1_FILTER_INTRA = ("FILTER_DC", "FILTER_V", "FILTER_H",
                     "FILTER_D157", "FILTER_PAETH")


def av1_filter_intra_name(mode: int) -> str:
    """Name for an AV1 filter-intra mode (0..4)."""
    if 0 <= mode < len(_AV1_FILTER_INTRA):
        return _AV1_FILTER_INTRA[mode]
    return f"filter-intra {mode}"


# libaom RestorationType enum (av1/common/restoration.h): the *remapped*
# FrameRestorationType the decoder uses, NOT the coded lr_type syntax element
# (Elecard prints the coded value, which maps coded 1<->2/3 via Remap_Lr_Type;
# both resolve to the same names here).
_AV1_RESTORATION = ("None", "Wiener", "SGRPROJ", "Switchable")


def av1_restoration_name(lr_type: int) -> str:
    """Name for an AV1 loop-restoration frame type (0..3)."""
    if 0 <= lr_type < len(_AV1_RESTORATION):
        return _AV1_RESTORATION[lr_type]
    return f"lr {lr_type}"


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


# H.264 intra prediction mode names (canonical, normalized in the decoder).
# I_16x16 uses the first four (Vertical/Horizontal/DC/Plane); I_4x4 / I_8x8 add
# the directional modes 3..8.
_H264_INTRA16 = ("Vertical", "Horizontal", "DC", "Plane")
_H264_INTRA4 = (
    "Vertical", "Horizontal", "DC", "Diag Down-Left", "Diag Down-Right",
    "Vertical-Right", "Horizontal-Down", "Vertical-Left", "Horizontal-Up",
)


def h264_intra_mode_name(intra_type: int, mode: int) -> str:
    """Name for an H.264 macroblock's canonical luma intra mode (sub_pdir)."""
    if intra_type == 3:
        return "PCM"
    if intra_type == 2:
        return _H264_INTRA16[mode] if 0 <= mode < 4 else f"mode {mode}"
    if intra_type == 1:
        return _H264_INTRA4[mode] if 0 <= mode < 9 else f"mode {mode}"
    return "n/a"


def h264_mb_type_label(intra_type: int, pred: int, intra_w: int = 0) -> str:
    """Short H.264 macroblock type label (PredType in pred; intra_type refines
    the intra family). intra_w (the covering intra block width) separates
    I_8x8 from I_4x4 when known."""
    if intra_type == 3:
        return "I_PCM"
    if intra_type == 2:
        return "I_16x16"
    if intra_type == 1:
        return "I_8x8" if intra_w == 8 else "I_4x4" if intra_w == 4 else "I_NxN"
    # inter families come from the prediction class
    from .schema import PredType
    return {PredType.SKIP: "P_Skip", PredType.INTER: "P/B inter",
            PredType.BI: "B_Bi"}.get(pred, "inter")


def qp_field_name(codec: str) -> str:
    """Label for the QP-like value (AV1 carries qindex, not a 0..51 QP)."""
    return "qindex" if codec == "av1" else "qp"
