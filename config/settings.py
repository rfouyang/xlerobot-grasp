"""Project configuration, split by domain."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# graspgenx clones its weights at import time when this is unset; the shared copy lives in ~/workspace/model.
os.environ.setdefault("GRASPGENX_CHECKPOINT_DIR", str(Path.home() / "workspace" / "model" / "graspgenx"))
# WSL runs with mirrored networking, where DDS discovery breaks; ROS 2 goes through the Zenoh router instead.
os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_zenoh_cpp")
# MuJoCo renders offscreen through EGL. PyOpenGL fixes its platform on first import (graspgenx pulls it in
# early), so both are set here, before anything else is imported.
os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("PYOPENGL_PLATFORM", "egl")


def use_utf8_output() -> None:
    """Make stdout/stderr able to print Chinese; entry points call this first."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


class PathConfig:
    BASE_DIR = BASE_DIR
    # package:// URIs in the URDF resolve under this folder.
    ROS_SRC_DIR = BASE_DIR / "ros_ws" / "src"
    URDF_PATH = ROS_SRC_DIR / "xlerobot_description" / "urdf" / "xlerobot.urdf"
    OUTPUT_DIR = BASE_DIR / "output"
    # Demos write here and only here.
    DEMO_DIR = OUTPUT_DIR / "demo"
    LOG_DIR = OUTPUT_DIR / "logs"


class RobotConfig:
    ROBOT_NAME = "xlerobot"
    MOVEIT_PACKAGE = "xlerobot_moveit_config"
    # Relative to the MoveIt config package.
    MOVEIT_CPP_FILE = "config/moveit_cpp.yaml"
    NODE_NAME = "xlerobot_grasp"
    # Arm name used on the page -> its MoveIt groups.
    ARMS = {
        "right": {"arm": "right_arm", "gripper": "right_gripper"},
        "left": {"arm": "left_arm", "gripper": "left_gripper"},
    }
    ARM_LABELS = {"right": "右臂", "left": "左臂"}


class GripperConfig:
    """XLeRobot's SO-101 jaw as GraspGenX sees it."""

    NAME = "xlerobot_so101"
    DIR = BASE_DIR / "asset" / "so101_gripper"
    # Copied from the robot URDF (right hand; the left hand is the same part).
    ROOT_LINK = "Fixed_Jaw"
    LINKS = ("Fixed_Jaw", "Moving_Jaw", "Fixed_Jaw_tip", "Moving_Jaw_tip", "Right_Arm_Camera")
    JAW_JOINT = "Jaw"
    FIXED_TIP_LINK = "Fixed_Jaw_tip"
    MOVING_TIP_LINK = "Moving_Jaw_tip"
    # 1.3 rad leaves ~9.5 cm between the tips. At 1.0 (7.8 cm) a 6.6 cm mustard bottle had ~5 mm a side and was
    # pushed over by a finger on the way down (MuJoCo); much wider and the revolute jaw stops facing the fixed one.
    JAW_OPEN = 1.3
    JAW_CLOSE = 0.0
    # Opening or closing takes this long, sent as a trajectory with this step.
    JAW_MOVE_S = 1.0
    JAW_STEP_S = 0.05
    # GraspGenX's canonical gripper frame (+Z approach, +X closing) in Fixed_Jaw coordinates.
    # Fingers point along Fixed_Jaw -Y and the moving jaw closes towards +X (measured from the URDF);
    # the origin sits on the centre line of the opening, level with the Fixed_Jaw origin.
    CANONICAL_AXES = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    CANONICAL_ORIGIN = (-0.02, 0.0, 0.0)
    # Sweep box size the tip frames do not give: finger width (canonical Y) and pad length (canonical Z).
    SWEEP_WIDTH = 0.025
    SWEEP_DEPTH = 0.03
    TYPE = "revolute_2f"
    # The pocket a drink sits in, in the grasp frame with the jaw open (measured on the collision meshes in
    # the y=0 plane): the fixed finger's inner face runs along x=0.028 from z=0.05 to its tip at 0.097, the
    # palm ends at z=0.047, the moving finger's tip only reaches z=0.066.
    POCKET_FIXED_FACE_X = 0.028
    POCKET_PALM_Z = 0.047
    POCKET_GAP = 0.004


class GraspConfig:
    CHECKPOINT_DIR = Path(os.environ["GRASPGENX_CHECKPOINT_DIR"]) / "release"
    # Most grasps on a tabletop object come in from the side through the table, and the 5-DOF arm reaches
    # only some of the rest (13 of the top 100 survived the table alone), so sample wide and keep by score.
    # 500 left a banana with one usable grasp in one of two runs; 800 costs ~1 s more.
    NUM_GRASPS = 800
    TOPK_GRASPS = -1
    THRESHOLD = 0.5
    NUM_SAMPLE_POINTS = 2000


class MotionConfig:
    """cuRobo planning for the right arm."""

    ROBOT_CONFIG = BASE_DIR / "asset" / "curobo" / "xlerobot_right_arm.yml"
    # Between the fingertips, not at the gripper base: the free rotation must turn about the contacts, or the
    # fingertips swing off the object. URDF: right_grasp_frame + TCP_OFFSET.
    TOOL_FRAME = "right_grasp_tcp"
    TCP_OFFSET = (0.0, 0.0, 0.0745)
    # A 5-DOF arm cannot hold a full 6-DoF pose, so IK and planning leave one rotation free (cuRobo weights,
    # x y z roll pitch yaw of the tool frame). A level side grasp on an upright drink keeps the jaw level and
    # frees the turn about tool y, which is then vertical: the drink is round, so a few degrees of yaw only
    # roll it in the jaw. Freeing the closing axis instead (as for top grasps) lets the IK pitch the jaw.
    POSE_AXES_WEIGHT = (1, 1, 1, 1, 0, 1)
    ARM_JOINTS = ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll")
    # Everything but the right arm stays where it is while planning; the jaw is held open.
    LOCK_JOINTS = {
        "Jaw": GripperConfig.JAW_OPEN,
        "Rotation_2": 0.0, "Pitch_2": 0.0, "Elbow_2": 0.0, "Wrist_Pitch_2": 0.0, "Wrist_Roll_2": 0.0, "Jaw_2": 0.0,
        "head_pan_joint": 0.0, "head_tilt_joint": 0.0,
    }
    CONTACT_LINKS = ["Fixed_Jaw", "Moving_Jaw"]
    # Pre-grasp this far back along the approach, so the open jaw slides onto the bottle sideways; lift this far
    # straight up after closing (world z). A 5-DOF arm can do both without turning the tool: the approach lies
    # in the arm's vertical plane.
    APPROACH_DISTANCE = 0.08
    # Planned 10 cm: holding the bottle the shoulder settles ~5° short (RealRobotConfig.LOADED_ARRIVED_DEG), so the real
    # jaw comes up ~8 cm (8 cm planned gave ~6 cm, 2026-10-06).
    LIFT_HEIGHT = 0.10
    # Pause with the jaw closed before lifting, so its grip force has built up.
    GRIP_SETTLE_S = 0.5
    ARM_GROUP = "right_arm"
    GRIPPER_GROUP = "right_gripper"


class SelectionConfig:
    """Grasps on the middle of a standing drink, from the side."""

    # The drink's centre in the jaw, as a share of its height (the user: grasp the middle section).
    MIDDLE_BAND = (0.35, 0.65)
    # How far the approach and the closing axis may tilt from level.
    MAX_TILT_DEG = 25.0
    # The right arm's shoulder yaw axis (joint Rotation) in base_link: the arm only approaches along the
    # vertical plane through it.
    SHOULDER_XY = (-0.0898, -0.133)


class SceneConfig:
    """Tabletop in front of the cart, in the robot's base_link frame."""

    # The cart ends at x=0.194. cuRobo IK for a level side grasp (rotation about the closing axis left free)
    # reaches the fingertip centre to x=0.16..0.32 at every height from 0.78 to 0.93 m; top-down grasps only
    # reached x~0.24. So the table touches the cart and a drink stands with its axis at x=0.27.
    TABLE_SIZE = (0.5, 0.8, 0.04)
    TABLE_TOP_Z = 0.74
    TABLE_FRONT_X = 0.20
    # Where a drink stands: in front of the right arm.
    OBJECT_XY = (0.27, -0.133)
    OBJECT_YAW = 0.0
    # An object's near side stays this far from the table's front edge.
    EDGE_MARGIN = 0.01
    OBJECT_DIR = BASE_DIR / "asset" / "objects"
    # Drinks from component/grasp_generation/drink_models.py, standing on their base. rgba for the 3D view and
    # the simulator; mass of a full container (kg).
    OBJECTS = {
        "water_bottle": {"file": "water_bottle.obj", "label": "矿泉水", "rgba": (0.72, 0.86, 0.97, 0.75), "mass": 0.57},
        "coke_can": {"file": "coke_can.obj", "label": "可乐", "rgba": (0.80, 0.07, 0.10, 1.0), "mass": 0.35},
        "sprite_bottle": {"file": "sprite_bottle.obj", "label": "雪碧", "rgba": (0.12, 0.62, 0.30, 0.85), "mass": 0.52},
    }


class SimConfig:
    """MuJoCo world that stands in for the real robot: it follows ros2_control's joint commands."""

    CACHE_DIR = BASE_DIR / "output" / "sim_cache"
    VIDEO_DIR = BASE_DIR / "output" / "videos"
    TIMESTEP = 0.002
    VIDEO_FPS = 30
    VIDEO_SIZE = (1280, 720)
    CAMERA_FOVY = 45
    # Front-right of the cart, framing the right arm, the jaw and the object together.
    OBSERVER_CAMERA = ((0.78, -0.62, 1.08), (0.18, -0.10, 0.80))
    # Close on the grasp, shown as an inset (1/INSET_SHRINK of the frame) in the top right of the clip: from the
    # right, level with the drinks' middle, so the jaw coming in and holding the body is seen in profile.
    CLOSEUP_CAMERA = ((0.22, -0.50, 0.92), (0.22, -0.133, 0.84))
    INSET_SHRINK = 3
    # The physics loop runs at most this often (it renders nothing; a clip is drawn afterwards).
    LOOP_S = 0.01
    # A clip starts LEAD_IN_S before the first state that differs from the first by more than MOTION_EPS.
    MOTION_EPS = 0.002
    LEAD_IN_S = 0.5
    # The jaw links press on the object: higher friction, softer contact.
    GRIP_LINKS = ("Fixed_Jaw", "Moving_Jaw")
    # joint -> (kp, force limit); STS3215 stalls at ~2.9 N·m.
    # The arm is stiff like the servos (an open jaw clears a 6.6 cm box by only ~5 mm a side, so gravity sag
    # must stay small). The jaw squeezes with the servo's full stall torque: at 1.5 N·m lifted objects slipped
    # out during the 1.5 s hold in 6 of 9 runs.
    ACTUATOR_GAINS = {"default": (150.0, 2.9), "Jaw": (30.0, 2.9), "Jaw_2": (30.0, 2.9)}
    JOINT_DAMPING = 0.5
    JOINT_ARMATURE = 0.01
    # CoACD concavity threshold for object collision parts (lower: more, tighter pieces).
    CONVEX_THRESHOLD = 0.04
    # Cap on physics steps per loop pass, so a stall (e.g. a rebuild) does not fast-forward the world.
    MAX_STEPS_PER_PASS = 50
    # Seconds of physics after an object is put down, before the robot may touch it.
    # A bottle on its side still rolled after 0.5 s.
    SETTLE_S = 1.5
    # An object whose centre rose at least this much by the end of the lift counts as grasped.
    LIFTED_M = 0.03
    # Recording continues this long after the lift, so the clip shows whether the object stays in the jaw.
    TAIL_S = 1.5
    GEOM_CLASSES = {
        "visual": {"contype": "0", "conaffinity": "0", "group": "2", "density": "0"},
        "robot": {"contype": "1", "conaffinity": "0", "group": "3", "rgba": "0 0 0 0"},
        "grip": {"contype": "1", "conaffinity": "0", "group": "3", "rgba": "0 0 0 0", "friction": "2.0 0.02 0.002",
                 "condim": "4", "solref": "0.01 1", "solimp": "0.95 0.99 0.001"},
        "world": {"contype": "1", "conaffinity": "1", "group": "0"},
        "object": {"contype": "1", "conaffinity": "1", "group": "3", "rgba": "0 0 0 0", "friction": "1.5 0.02 0.002",
                   "condim": "4"},
    }


class ViewerConfig:
    # All interfaces: the page is opened from the Windows browser, which reaches WSL through mirrored localhost.
    HOST = "0.0.0.0"
    PORT = 8080
    ROBOT_NODE = "/xlerobot"
    CAMERA_POSITION = (0.95, -0.85, 1.25)
    CAMERA_TARGET = (0.22, -0.08, 0.78)
    CAMERA_FOV_DEG = 45.0
    # How often the 3D robot follows /joint_states.
    FOLLOW_INTERVAL_S = 0.05
    TABLE_COLOR = (205, 180, 140)
    POINT_COLOR = (40, 120, 220)
    SELECTED_COLOR = (37, 99, 235)


class RealRobotConfig:
    """The real XLeRobot is reached only through the xlerobot-teleop service: it owns the serial ports and the
    cameras, and keeps the safety rules (rest pose, torque, watchdog)."""

    # xlerobot-teleop, the motor and camera backend.
    TELEOP_URL = "http://127.0.0.1:8011"
    # Torque off walks every part back to the rest pose before replying.
    TIMEOUT_S = 40.0
    # A move counts as done when every powered joint is this close to its target (P 16 leaves ~1-3° under load).
    ARRIVED_DEG = 4.0
    # Under load the position control leaves a steady error (no integral term): holding the bottle out, shoulder_lift
    # stopped 6.3° short of the lift's end. A joint counts as arrived when it is within ARRIVED_DEG, or when the arm
    # has settled (no joint moving more than SETTLED_DEG over SETTLED_S) within LOADED_ARRIVED_DEG.
    LOADED_ARRIVED_DEG = 10.0
    # The jaw's pitch is the sum shoulder_lift + elbow_flex + wrist_flex: where the shoulder and the elbow sag (or lag)
    # the wrist makes up for it, at most this much. Lifting the bottle the jaw dipped 10.8° without it (2026-10-06).
    LEVEL_MAX_DEG = 15.0
    SETTLED_DEG = 0.3
    SETTLED_S = 1.0
    MOVE_TIMEOUT_S = 30.0
    # Follow mode: how often the bridge sends the mock's joints to the real arm.
    FOLLOW_HZ = 30
    # The real arm runs MoveIt's trajectories this many times slower than planned (first runs on the real robot).
    TIME_SCALE = 2.0
    # Which real arm the grasp uses.
    ARM_PART = "right_arm"
    # After a gripper step, time for the real jaw to finish (a closing jaw stops on the bottle and squeezes).
    JAW_SETTLE_S = 1.0
    # The real gripper reading after closing at or below which the jaw holds nothing (empty: 0-2; spray bottle: 14-16).
    EMPTY_JAW_READING = 5.0


class JointMappingConfig:
    """Real joint (lerobot degrees, xlerobot_smallcircle's calibration) <-> URDF joint: real = SIGN * urdf_deg + OFFSET.

    Signs measured on the robot (2026-10-05): shoulder_lift, wrist_roll and the head pan turn the other way from
    xlerobot.urdf. Offsets: shoulder_lift / elbow from XLeRobot's own IK convention (lift = 90 - Pitch,
    elbow = Elbow - 90), checked against the URDF geometry within 0.2°; the head's from its calibration (the pose
    set looking straight ahead, level, reads pan -0.4°, tilt 16.3°); wrist_roll's from the jaws: at real roll ~0 both
    open sideways with the moving finger on the robot's right (the user, 2026-10-05), where the URDF's open up and down
    at roll 0 and put the moving finger on the right at +90°; the rest 0 until checked."""

    # URDF joint -> (real motor, sign, offset deg). The left arm's joints end in _2 and use the left motors.
    ARM = {
        "Rotation": ("shoulder_pan", 1, 0.0),
        "Pitch": ("shoulder_lift", -1, 90.0),
        "Elbow": ("elbow_flex", 1, -90.0),
        "Wrist_Pitch": ("wrist_flex", 1, 0.0),
        "Wrist_Roll": ("wrist_roll", -1, 90.0),
    }
    HEAD = {
        "head_pan_joint": ("head_motor_1", -1, -0.4),
        "head_tilt_joint": ("head_motor_2", 1, 16.3),
    }
    # Gripper 0 (closed) .. 100 (open) <-> Jaw 0 .. JAW_OPEN_RAD.
    JAW_OPEN_RAD = 1.7
    # Per-arm corrections on top of ARM (deg, by motor): true URDF angle = mapped angle + correction. The left arm's
    # from its own calibration (2026-10-09); the right arm's mapping is the reference the head camera was calibrated on.
    ARM_CORRECTION_DEG = {"right_arm": {},
                          "left_arm": json.loads((BASE_DIR / "config" / "calibration" / "left_arm_20261009.json").read_text())["correction_deg"]}


class RealPlanningConfig:
    # A copy of MotionConfig.ROBOT_CONFIG with the left arm and head locked where the real ones are.
    ROBOT_CONFIG = BASE_DIR / "asset" / "curobo" / "xlerobot_right_arm_real.yml"
    # The links the planned right arm moves; every other collision link is fixed while planning.
    MOVING_LINKS = ("Rotation_Pitch", "Upper_Arm", "Lower_Arm", "Wrist_Pitch_Roll", "Fixed_Jaw", "Moving_Jaw", "Right_Arm_Camera")
    # Right-arm pairs whose collision spheres overlap at the real folded rest pose (the forearm lies over the shoulder
    # bracket there; spheres are fatter than the parts): with them checked, cuRobo cannot plan out of the rest pose.
    IGNORE_PAIRS = (("Lower_Arm", "Rotation_Pitch"),)
    # Table box for collision (front edge and height come from the depth).
    TABLE_SIZE = (0.6, 1.0, 0.04)
    # cuRobo sees the table this much higher than it is, so every path (in, out, back to the start) keeps that much
    # more room: planned right at the table, the real arm (1.6 cm below the plan under its weight) went back skimming
    # it (the user, 2026-10-06).
    TABLE_CLEARANCE_M = 0.025
    # Real grasps: the drink's centre in the jaw within this share of its height, the highest first (from the middle
    # the wrist ran along the table; above 0.65 the spray bottle's shoulder starts curving in).
    GRASP_BAND = (0.5, 0.65)
    # The table's front edge never comes closer than this: the cart's top rim (x 0.145-0.194, as high as the table)
    # reads as table in the depth, and a table box reaching under it touched the cart's own collision spheres, so
    # cuRobo found every pose in collision (0 reachable, or not, with 0.1 mm of noise in the edge).
    MIN_TABLE_FRONT_X = 0.20
    # Bottle points this close to the table belong to it or the shadow at its foot.
    ABOVE_TABLE_M = 0.005
    # Bottle points further than this many radii from the fitted axis are strays. With LingBot-Depth ~2 % of the mask's
    # points hung between the bottle and the background, and GraspGenX then gave ~45 level side grasps instead of ~290
    # (raw depth: none of them; 2026-10-06).
    STRAY_RADIUS_SHARE = 1.5
    MAX_PLAN_TRIES = 10
    # The right wrist's real roll (lerobot degrees) that the arm can actually reach, with a margin: clockwise the
    # wrist camera's cable stops it at -73° (measured), anticlockwise it reaches its calibrated end +180°.
    ROLL_RANGE_DEG = (-65.0, 170.0)
    # Room between the bottle and the fixed finger, which slides straight past it on the way in: with the simulation's
    # 4 mm the real one knocked the bottle over (the bottle is where the depth says to ~1 cm). The moving finger
    # still has ~4 cm on its side and pushes the bottle onto the fixed one when it closes.
    SIDE_GAP_M = 0.015
    # Planned grasps go this much higher: at the grasp the real arm ended 1.6 cm below the plan (elbow 2.7° short
    # under the arm's weight, measured 2026-10-05).
    SAG_RAISE_M = 0.015
    # Planned grasps go this much back along the approach: reaching in, the wrist camera's mount (no collision shape
    # in the URDF) touched the bottle; the head camera has the real jaw 1.3-1.4 cm further than the model each time.
    # (0.5 cm with HeadCameraConfig's extrinsics applied overshot: back to 2 cm while they are off, 2026-10-06.)
    DEPTH_BACK_M = 0.02
    # How much of it by the drink's radius, interpolated between (radius, back): the 1.8 cm spray bottle keeps the 2 cm
    # it has run with; the 3 cm water bottle was squeezed out by the fingertips (17:56, the user: go in closer than
    # for a small one), so it goes the whole 2 cm deeper.
    DEPTH_BACK_BY_RADIUS = ((0.018, DEPTH_BACK_M), (0.030, 0.0))
    # After reaching behind the bottle, the right jaw seen by the head camera may differ from the model by this much
    # in depth (median over its pixels) before moving in.
    APPROACH_CHECK_M = 0.02
    # Which jaw links are judged by it: the fixed one only (the moving jaw's depth is off by its opening / model,
    # -2.0 cm at 11:59 with the fixed one at -0.4; both still reported).
    APPROACH_CHECK_LINKS = ("Fixed_Jaw",)
    # Behind the bottle the head camera measures where the jaw really is (fixed jaw + wrist matched to the depth): an
    # offset between CORRECT_MIN_M and CORRECT_MAX_M, from a fit no worse than CORRECT_FIT_MAX_MM median, is taken out
    # (the scene moved into the model's frame, the jaw sent to the real pre-grasp, grasp and lift planned again). The
    # left arm read 3.8 cm off there on 2026-10-09 while its calibration poses fitted to ~1.5 cm.
    # Off since 2026-10-09: the fit takes in the wrist camera's mounting plate, which stands behind the jaw and is not in
    # the URDF, and read the right jaw 3 cm off where the fixed-jaw check found 0.2 cm (and misled the left arm's
    # calibration the same way). The fixed-jaw approach check stays.
    CORRECT_ENABLED = False
    CORRECT_MIN_M = 0.01
    CORRECT_MAX_M = 0.06
    CORRECT_FIT_MAX_MM = 8.0


class HeadCameraConfig:
    """The head camera calibrated against the right arm, at the grasp start pose's head angles (the head stays there
    for grasping).

    The depth scale comes from a ChArUco board on the desk (output/calibration/session_20261006): the RealSense reads
    ~1 % too far. The extrinsics come from the jaw itself at 16 grasp-like arm poses (output/calibration/
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


class PlaceConfig:
    """Pick and place: the grasped bottle into an open box on the desk (the pick-place page, 2026-10-06). The box
    stands on the left, at the edge of the right arm's reach; a grey cardboard box, rim 4.1 cm, ~23 x 7.5 cm."""

    # YOLOE finds it as "box" (0.26; the tissue box too, further back): the open one nearest the right shoulder wins.
    BOX_PROMPTS = ("box",)
    BOX_CONFIDENCE = 0.1
    # An open box: its rim this high above the table (m).
    RIM_RANGE_M = (0.02, 0.12)
    # The bottle's axis stays inside the walls' footprint by the wall, its radius and this margin.
    WALL_M = 0.005
    WALL_MARGIN_M = 0.01
    MAX_PLACE_TRIES = 12
    # The box as an obstacle starts this far in front of the robot (base x, m): nearer, the folded left arm sits over it.
    OBSTACLE_MIN_X = 0.15
    # Place points this far from the right shoulder's axis (m) are tried first: the middle of the box, a little short of its centre.
    PREFERRED_REACH_M = 0.41  # 0.33 put the bottle on the near wall; past ~0.41 the level jaw is out of reach over the box (2026-10-06)
    # The jaw's lowest point below the tool frame: the bottle hangs (grasp height above its foot - this) below it.
    TOOL_ABOVE_JAW_BOTTOM_M = 0.03
    # Carrying, the bottle's foot clears the rim by at least this (the box obstacle raised by the hanging bottle).
    CARRY_CLEARANCE_M = 0.01
    # ... planned that much higher again: holding the bottle the real arm ends ~2 cm below the plan.
    CARRY_SAG_M = 0.02
    # The bottle's foot this high above the box floor when the jaw lets go.
    PLACE_CLEARANCE_M = 0.003
    # After the release the open jaw rises until its lowest point is this far over the rim, then backs out level; the
    # box counts this much higher while the arm leaves and goes home.
    EXIT_CLEARANCE_M = 0.02
    # How far the tool may sink below where it should stay on the way out (planning noise).
    EXIT_DIP_M = 0.005


class ReachMapConfig:
    """Where on the desk the right arm can make its level side grasp (component/real_robot/reach_map.py): told at a
    click on the interactive grasp page, drawn on its head view."""

    # The desk grid in base_link (m): in front of the robot, from its right to its left, and the cell size.
    X_RANGE_M = (0.12, 0.46)
    Y_RANGE_M = (-0.32, 0.32)
    CELL_M = 0.015
    # The grid is for an object this tall (the spray bottle); a clicked object is checked at its own grasp height.
    DEFAULT_HEIGHT_M = 0.14
    GRASP_Z_RANGE_M = (0.04, 0.15)
    # A least-squares solution below this cost counts (position in m, the level terms weighted 0.3).
    IK_COST = 1e-6
    # IK starts (real degrees: pan, lift, elbow, wrist_flex, wrist_roll), from grasps that planned on 2026-10-08/09.
    SEEDS_REAL_DEG = ((-14.5, 29.8, 17.0, -46.8, 0.0), (-24.5, 29.2, 16.3, -45.5, 0.0), (-37.7, 48.1, -11.1, -37.0, 0.0),
                      (-19.0, 17.5, 38.7, -56.2, 0.0), (5.0, 30.0, 15.0, -45.0, 0.0))
    # The suggested move goes this much past the nearest reachable cell.
    MARGIN_M = 0.01
    # The desk height the grid is solved at (base_link z, m; planning has measured 0.762-0.766).
    TABLE_Z_M = 0.764


class MoveRightConfig:
    """Each grasp puts the bottle back 1-2 cm further to the robot's left (+y); after a few the arm reaches too far.
    A grasp then stops at planning and asks: the "move the bottle to the right" action grasps it and turns it right
    while it is up (the user, 2026-10-06: tell me first, I decide)."""

    # base_link y of the bottle's axis beyond which a grasp stops and asks (10:24-10:27 it went -0.076 -> -0.033).
    # 1.5 cm left of the first -0.02: the head camera calibration (HeadCameraConfig) puts bottles ~1.5 cm further
    # left, so the same spot on the desk (2026-10-06, the user left it to me).
    LEFT_LIMIT_Y = -0.005
    # Where the move aims to put it back, at most MAX_SHIFT_M in one go: near straight in front of the right shoulder
    # (SelectionConfig.SHOULDER_XY y -0.133). The turn moves it along an arc and the loaded arm stops short: 8 cm asked
    # gave 6.4 cm sideways (11:01; -0.07 / 8 cm before, the user wanted it to go further).
    HOME_Y = -0.095
    MAX_SHIFT_M = 0.12


class WristAlignConfig:
    """Behind the bottle, before reaching in: the right wrist camera moves the jaw left until the bottle sits on the
    moving finger's side. The fixed finger does not open, so a jaw centred on the bottle runs into it (the head
    camera's bottle and the arm's pose together are off by ~2.6 cm sideways, measured 2026-10-05)."""

    CAMERA = "right_wrist"
    # Where the closed fingers touch in the wrist image (as the teleop shows it, rotated upright): the fixed finger's
    # inner face goes forward along this column (187-192 on two pre-grasp images).
    CONTACT_COLUMN_PX = 190
    # The bottle's left edge this far right of that face, within TOLERANCE_M.
    SIDE_GAP_M = 0.015
    TOLERANCE_M = 0.005
    MAX_TRIES = 3
    # A larger correction means something else is wrong: stop instead.
    MAX_PAN_DEG = 10.0
    # A turn goes at this speed, then the image settles before the next look.
    PAN_SPEED_DEG_S = 5.0
    SETTLE_S = 1.0
    # After the release the bottle still leans on the fixed finger (closing pushed it there): while the jaw opens it
    # moves this far left, or the fixed finger rubs along the bottle on the way out (the user, 2026-10-06: 1 cm was not
    # enough, and the move should come with the opening, not after it).
    RELEASE_CLEAR_M = 0.02
    # The move left waits until the real jaw is this much (gripper 0-100) wider than where it held the bottle: starting
    # with the opening, the moving finger still pressed the bottle and carried it left (~1.5 cm), and judged on the
    # jaw's target instead of the real jaw it dragged the bottle over (2026-10-06).
    RELEASE_OPEN_FIRST = 10.0
    # A look that finds nothing is repeated this many times in all, this far apart (s), before the step fails.
    LOOKS = 3
    LOOK_PAUSE_S = 0.3
    # That first opening goes this many times slower than a normal jaw move (~0.8 s -> ~2.4 s): released quickly, the
    # bottle followed the moving finger and fell to the right (the user, 2026-10-06).
    RELEASE_FIRST_SLOWDOWN = 3.0
    # How long the real jaw may take to get there before the release fails (arm not moved).
    RELEASE_WAIT_S = 3.0
    # Before closing the jaw moves right until the fixed finger is this close to the bottle (from the gap the last look
    # measured): pushed across the whole 1.5 cm at mid-height the bottle tipped instead of sliding (2026-10-06).
    CLOSE_GAP_M = 0.003
    # The place step goes straight back down the lift. It used to drift right 0.5 cm on the way (for what closing
    # pushed the bottle left); once the bottle's foot touched the table that tilted it toward the moving finger, and it
    # fell when the jaw opened (2026-10-06, removed at the user's request).
    # Body rows: where the mask is at least this share of its widest (the sprayer and cap are narrower).
    BODY_WIDTH_SHARE = 0.85


class PerceptionConfig:
    """Open-vocabulary detection + segmentation (YOLOE) on the head RGB-D image."""

    MODEL_DIR = Path.home() / "workspace" / "model" / "yoloe"
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
    SAM_WEIGHTS = Path.home() / "workspace" / "model" / "sam3" / "sam3.1_multiplex.pt"
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
    FFS_WEIGHTS = Path.home() / "workspace" / "model" / "fast-foundationstereo" / "23-36-37" / "model_best_bp2_serialize.pth"
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


class AlertConfig:
    """Back at the start pose, the head camera looks again: a bottle that fell lies about its diameter high instead
    of its height. Until it stands, a voice on the Windows speaker asks someone at the desk to stand it up (the
    user, 2026-10-06, operating remotely)."""

    # Upright when the bottle seen is at least this share of its height at planning.
    UPRIGHT_HEIGHT_SHARE = 0.6
    BOTTLE_FALLEN = "Olis, the bottle handling failed. Could you please help move it straight up?"
    # Once it stands again someone is at the desk: thanks and a warning, this long to step back, a second warning,
    # and only then the robot moves (the user, 2026-10-06).
    THANKS_STEP_AWAY = "Thank you! Please step away from the robot."
    CLEAR_WAIT_S = 10.0
    ABOUT_TO_MOVE = "The robot is about to move. Please stay away from the robot."
    # WSL has no sound here: powershell.exe (WSL interop) plays on Windows' default device, the Bluetooth speaker.
    POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    # The clips are made once by BytePlus and kept (Windows has to reach the file, so it sits on the C: drive).
    CLIP_DIR = Path("/mnt/c/Users/rfouy/AppData/Local/Temp/xlerobot_alerts")
    # Before each alert the speaker is unmuted and turned up to at least this (louder stays); Olis has to hear it
    # elsewhere in the home (the user, 2026-10-06).
    MIN_VOLUME_PERCENT = 65
    VOLUME_SCRIPT = BASE_DIR / "asset" / "windows" / "ensure_volume.ps1"
    # Said again after this pause and another look, until the bottle stands: once is easily missed elsewhere in the
    # home (the user, 2026-10-06).
    RECHECK_PAUSE_S = 5.0


class ByteplusTtsConfig:
    """BytePlus Seed Speech 2.0, as in g1-action-recorder (the user prefers it to Windows' voices). The key is in .env."""

    API_KEY = os.getenv("BYTEPLUS_API_KEY", "")
    URL = os.getenv("BYTEPLUS_API_BASE_URL", "https://voice.ap-southeast-1.bytepluses.com") + "/api/v3/tts/unidirectional"
    RESOURCE_ID = os.getenv("BYTEPLUS_TTS_RESOURCE_ID", "seed-tts-2.0")
    USER_ID = "xlerobot-grasp"
    TIMEOUT_S = 30.0
    SAMPLE_RATE = 24000
    # Clean, clear and gentle (g1-action-recorder/config/byteplus_voices.json lists the others).
    VOICE = "en_female_nadia_uranus_bigtts"


class ServerConfig:
    # A backend: the pages are xlerobot-ui's (8010), which forwards /grasp-api/* here; nothing else reaches it.
    HOST = "127.0.0.1"
    PORT = 8012
    LOG_LEVEL = "INFO"
    # How often the page's joint table is pushed over the WebSocket.
    STATE_PUSH_INTERVAL_S = 0.1


class ArmConfig:
    """Per-arm values (component/real_robot/arm.py). Both grippers move their right half and keep the left one fixed:
    the left arm is a copy of the right, not a mirror (the user, 2026-10-09)."""

    # Which arm the interactive grasp (pick_target) uses: "auto" (the arm whose shoulder is nearer the object, as the
    # user asked on 2026-10-09: left of the line halfway between the shoulders the left arm, right of it the right arm;
    # if the nearer one cannot reach, the other), "right" or "left". The other tasks keep the right arm.
    CHOICE = "auto"
    ROBOT_CONFIGS = {"right": MotionConfig.ROBOT_CONFIG, "left": BASE_DIR / "asset" / "curobo" / "xlerobot_left_arm.yml"}
    # Copies with the joints not planned locked where the real ones are (written at every planning).
    REAL_CONFIGS = {"right": RealPlanningConfig.ROBOT_CONFIG, "left": BASE_DIR / "asset" / "curobo" / "xlerobot_left_arm_real.yml"}
    # The real wrist roll's usable range (deg): the right camera cable stops the right wrist at -73 (2026-10-06). The
    # left one is not measured yet: kept to +-90, which also rules out the upside-down level jaw (roll 180, the fixed
    # finger on the right).
    ROLL_RANGE_DEG = {"right": RealPlanningConfig.ROLL_RANGE_DEG, "left": (-90.0, 90.0)}
    # The shoulder's yaw axis in base_link (URDF Rotation_Pitch / Rotation_Pitch_2).
    SHOULDER_XY = {"right": SelectionConfig.SHOULDER_XY, "left": (-0.0898, 0.133)}
    # The fixed finger's inner face in each wrist image (column, px, the closed fingers' meeting line): the left one
    # from the left arm's calibration images (178-183, 2026-10-09; its camera sits a little turned).
    CONTACT_COLUMN_PX = {"right": WristAlignConfig.CONTACT_COLUMN_PX, "left": 181}
    # Pairs (in the right arm's link names) ignored for self-collision besides RealPlanningConfig.IGNORE_PAIRS: their
    # spheres overlap at that arm's folded rest pose. The left arm, folded a little tighter (its elbow 4 deg more after
    # its calibration), has the wrist on the upper arm (4.5 mm) and the shoulder bracket (2.1 mm): cuRobo saw its start
    # in collision and planned nothing (2026-10-09).
    EXTRA_IGNORE_PAIRS = {"right": (), "left": (("Upper_Arm", "Wrist_Pitch_Roll"), ("Rotation_Pitch", "Wrist_Pitch_Roll"))}
    # The approach check's expected fixed-jaw reading (measured - model, m), taken off before judging. The left one reads
    # -1.5 cm at every calibration pose even with its mount corrected (-0.7..-1.8): most likely its wrist camera board,
    # which sits elsewhere than the URDF draws it, in front of the fixed jaw's pixels (the right arm reads ~0).
    APPROACH_CHECK_OFFSET_M = {"right": 0.0, "left": -0.015}
    # How much higher than measured cuRobo sees the desk for each arm (RealPlanningConfig.TABLE_CLEARANCE_M for the right):
    # the left arm's real path dipped with its fingertips down towards the desk on 2026-10-09 while the plan stayed 8.6
    # cm above it, so it keeps further up until that is understood.
    TABLE_CLEARANCE_M = {"right": RealPlanningConfig.TABLE_CLEARANCE_M, "left": 0.06}
    # The desk guard's margin (m): every mesh point of the arm over the desk stays this far above it, in plans and in
    # every target sent to the real arm. The left arm's is wider until its calibration (2026-10-09) is proven in use.
    DESK_MARGIN_M = {"right": 0.05, "left": 0.06}
    # Whether the head camera's look at the jaw behind the bottle (the offset correction and the approach check) is
    # trusted for the arm. Not for the left: it reads the left jaw ~4.5 cm high where the user sees it right (its wrist
    # camera board is not where the URDF draws it, and the head pose was calibrated on the right arm; 2026-10-09).
    HEAD_CHECK = {"right": True, "left": False}
    # How much further than planned the jaw reaches in (m), taken off the grasp's pull-back (RealPlanningConfig
    # DEPTH_BACK_*): the right jaw reaches further than its model, the left one stopped short of the bottle and closed
    # on nothing (the user, 2026-10-09).
    REACH_EXTRA_M = {"right": 0.0, "left": 0.025}


class DeskGuardConfig:
    """component/real_robot/desk_guard.py: the desk the arms keep clear of."""

    # The desk plane as last measured from the head depth (written at every planning); flat at DEFAULT_Z until then.
    PLANE_FILE = BASE_DIR / "output" / "desk_plane.json"
    DEFAULT_Z = 0.762
    # While following, a target may come this much closer than the arm's margin before streaming stops (the plan was
    # checked with the full margin; the wrist levelling moves the jaw a little).
    FOLLOW_TOLERANCE_M = 0.01
    # The desk is taken to reach the robot: arm points in front of this (base x, m) are checked.
    FRONT_X = 0.0
    # Mesh points per link, and the densest joint step checked along a trajectory (rad).
    SAMPLES = 300
    STEP_RAD = 0.02
