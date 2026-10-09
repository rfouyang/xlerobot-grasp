"""Perception: the head camera (frames, depth from Fast-FoundationStereo, its calibration), detection and segmentation."""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import BASE_DIR, PathConfig


class PerceptionConfig:
    """Open-vocabulary detection + segmentation (YOLOE) on the head RGB-D image."""

    MODEL_DIR = PathConfig.MODEL_DIR / "yoloe"
    # YOLOE-26 large, segmentation; its text encoder (mobileclip2_b.ts) sits in MODEL_DIR too.
    WEIGHTS = "yoloe-26l-seg.pt"
    # "small spray bottle" / "spray bottle" score <= 0.06 on the target; "bottle" finds it at 0.73 (and the water
    # bottle further back), so the prompt stays plain and the nearest match is the target.
    PROMPTS = ("bottle",)
    CONFIDENCE = 0.25
    # Picking the target by a click on the head image (the interactive grasp page): YOLOE's prompt-free model (its own
    # ~4.6k names) says what is there, the smallest confident mask under the click (the bottle, not the desk mat under
    # it); its name is then the prompt for planning and the wrist camera. Loaded on the first click.
    PICK_WEIGHTS = "yoloe-26l-seg-pf.pt"
    PICK_CONFIDENCE = 0.2
    PICK_MAX_AREA_SHARE = 0.25
    # With a target point, the detection whose mask holds it is the target; one whose mask misses it by up to this
    # (px) still counts (the click on its edge, or the object moved a little since).
    PICK_RADIUS_PX = 30
    # A click is segmented by SAM 3.1 (any object, a tight mask; ~0.65 s and ~2.4 GB on the GPU, loaded on the first
    # click), and so is the target at every later look while it has a point; the prompt-free YOLOE only names it: its
    # detection overlapping SAM's mask most (by IoU, at least PICK_NAME_IOU). False: YOLOE alone.
    SEGMENT_BY_POINT = True
    SAM_WEIGHTS = PathConfig.MODEL_DIR / "sam3" / "sam3.1_multiplex.pt"
    # SAM's input size: a multiple of its patch stride 14 (1024 is rounded up to this anyway).
    SAM_IMGSZ = 1036
    PICK_NAME_IOU = 0.3
    # Following the target at later looks: SAM's mask at its point must be within this factor of the target's area at
    # planning, else the object moved off the point (SAM then segments the desk: on 2026-10-09 that read as a fallen
    # bottle 46 times) and YOLOE looks for its name within TRACK_RADIUS_PX of the point instead.
    TRACK_AREA_FACTOR = 3.0
    TRACK_RADIUS_PX = 80
    # Depth inside a mask: a ring of background often leaks in, the median of the near half is the object.
    NEAR_FRACTION = 0.5
    # Mask outline sent to the teleop page's overlay, simplified to about this many pixels of error.
    POLYGON_EPSILON_PX = 2.0
    # Where the head depth comes from: "sensor" (the D415's own stereo, depth aligned to the colour in the camera) or
    # "stereo" (Fast-FoundationStereo on the D415's infrared pair, moved into the colour camera here). Stereo since
    # 2026-10-08 (the user's call): on the same 5 frames it filled 86 % of the image against the sensor's 79 % (99.6 %
    # of the bottle's mask), left the fewest strays (0.08 %), measured the box's walls closest and took 0.08 s.
    DEPTH_SOURCE = "stereo"
    # The head depth refined by LingBot-Depth before detection and planning use it. Off since 2026-10-08: after the
    # stereo it only spread points into the bottle and trailed strays behind it (1.8 %), and GraspGenX scored its clouds
    # lowest. LingBot stays for the wrist cameras (RGB only).
    DEPTH_REFINE = False
    # Fast-FoundationStereo (research checkpoint 23-36-37, the most accurate of the family: ~50 ms at 640x480 on a
    # 3090, 653 MB peak). Run from its checkout like LingBot-Depth.
    FFS_DIR = Path.home() / "workspace" / "app" / "fast-foundationstereo"
    FFS_WEIGHTS = PathConfig.MODEL_DIR / "fast-foundationstereo" / "23-36-37" / "model_best_bp2_serialize.pth"
    FFS_VALID_ITERS = 8
    FFS_MAX_DISP = 192
    # Stereo depth outside this (m) is dropped: nearer than the D415 can match, or the room behind the desk.
    STEREO_RANGE_M = (0.15, 2.0)
    LINGBOT_DIR = Path.home() / "workspace" / "app" / "lingbot-depth"
    LINGBOT_WEIGHTS = Path.home() / "workspace" / "model" / "lingbot-depth" / "vitl-14-v0.5" / "model.pt"
    # 9 = 3600 tokens: 0.19 s and ~2.8 GiB on the GPU (weights included); lower levels save little and move the
    # depth 1-2 mm (median) from it.
    LINGBOT_RESOLUTION_LEVEL = 9
    # The grasp page's live depth, raw and refined: at most this rate (LingBot takes ~0.2 s a frame), only while the page
    # has asked within PREVIEW_IDLE_S, coloured over this fixed range (m; the head at the grasp pose sees the desk at
    # ~0.3-1.1 m).
    PREVIEW_HZ = 3.0
    # The framed head RGB ("detect" / "target") has its own thread at up to this rate: colour only, YOLOE, no stereo.
    # In the depth loop it waited for Fast-FoundationStereo every frame and lagged seconds behind (2026-10-09).
    OVERLAY_HZ = 10.0
    PREVIEW_IDLE_S = 3.0
    PREVIEW_RANGE_M = (0.25, 1.1)


class HeadCameraConfig:
    """The head camera calibrated against the right arm, at the grasp start pose's head angles (the head stays there
    for grasping).

    The depth scale comes from a ChArUco board on the desk (data/calibration/session_20261006): the RealSense reads
    ~1 % too far. The extrinsics come from the jaw itself at 16 grasp-like arm poses (data/calibration/
    jaw_20261006: trimmed ICP of the head depth onto the jaw and wrist meshes), so what the head sees lines up with
    where the arm goes when grasping. The board's extrinsics (measured through the arm far from grasping poses)
    overshot along the camera's depth by ~2 cm at the grasp (11:39) and are not used."""

    FILE = BASE_DIR / "config" / "calibration" / "head_and_wrist_20261006.json"
    # The RealSense depth reads this much too far (median over 404 board corners): true = read / DEPTH_SCALE.
    DEPTH_SCALE = json.loads(FILE.read_text())["head_depth_scale"]
    # A head or wrist picture older than this (s) is refused: the teleop's camera thread once died and every view
    # froze while the arm moved; the approach check then compared the jaw with an empty desk (2026-10-08).
    MAX_FRAME_AGE_S = 1.5
    # The same for Fast-FoundationStereo's depth (true = read / STEREO_DEPTH_SCALE): 0.72 % too far over 1618 board
    # corners at 0.48-0.51 m (2026-10-08; the sensor checked again at 0.96 %, as on 10-06).
    STEREO_DEPTH_SCALE = json.loads((BASE_DIR / "config" / "calibration" / "head_ffs_scale_20261008.json").read_text())["head_ffs_depth_scale"]
    EXTRINSIC_FILE = BASE_DIR / "config" / "calibration" / "head_on_jaw_20261006.json"
    APPLY_EXTRINSIC = True
    # The optical frame as measured, relative to the URDF's: base_T_optical = URDF base_T_optical @ CORRECTION
    # (2.8 deg and ~1 cm: a bottle comes out ~1.5 cm further left than with the URDF alone, as the wrist camera kept
    # correcting); the identity while APPLY_EXTRINSIC is off.
    CORRECTION = (json.loads(EXTRINSIC_FILE.read_text())["urdf_T_measured"] if APPLY_EXTRINSIC
                  else [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]])
    # Then shifted in base_link: with CORRECTION alone the wrist camera still moved the jaw right by 0.68-1.39 cm
    # (mean 0.98) over 6 grasps (12:01-12:15), so bottles come out 1 cm further right.
    BASE_TRIM_M = (0.0, -0.010, 0.0)
