"""Planning: the gripper and GraspGenX, cuRobo motion, grasp selection, the two arms, reach maps, the desk guard."""

from __future__ import annotations

import os
from pathlib import Path

from config.settings import BASE_DIR
from config.task import WristAlignConfig


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


class RealPlanningConfig:
    # A copy of MotionConfig.ROBOT_CONFIG with the left arm and head locked where the real ones are.
    ROBOT_CONFIG = BASE_DIR / "asset" / "curobo" / "xlerobot_right_arm_real.yml"
    # The links the planned right arm moves; every other collision link is fixed while planning.
    MOVING_LINKS = ("Rotation_Pitch", "Upper_Arm", "Lower_Arm", "Wrist_Pitch_Roll", "Fixed_Jaw", "Moving_Jaw",
                    "Right_Arm_Camera")
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
    # A short object (lower than SHORT_HEIGHT_M) up to 70 %: at its middle the jaw comes too near the desk (the 11.8 cm
    # bottle: the left fixed jaw 5.7 cm above it at 65 %, its margin 6 cm, 2026-10-10).
    SHORT_HEIGHT_M = 0.13
    GRASP_BAND_SHORT = (0.5, 0.70)
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
    # Off since 2026-10-09: the fit takes in the wrist camera's mounting plate, which stands behind the jaw and is not
    # in the URDF, and read the right jaw 3 cm off where the fixed-jaw check found 0.2 cm (and misled the left arm's
    # calibration the same way). The fixed-jaw approach check stays.
    CORRECT_ENABLED = False
    CORRECT_MIN_M = 0.01
    CORRECT_MAX_M = 0.06
    CORRECT_FIT_MAX_MM = 8.0


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
    SEEDS_REAL_DEG = ((-14.5, 29.8, 17.0, -46.8, 0.0), (-24.5, 29.2, 16.3, -45.5, 0.0),
                      (-37.7, 48.1, -11.1, -37.0, 0.0), (-19.0, 17.5, 38.7, -56.2, 0.0),
                      (5.0, 30.0, 15.0, -45.0, 0.0))
    # The suggested move goes this much past the nearest reachable cell.
    MARGIN_M = 0.01
    # The desk height the grid is solved at (base_link z, m; planning has measured 0.762-0.766).
    TABLE_Z_M = 0.764


class DeskGuardConfig:
    """component/real_robot/desk_guard.py: the desk the arms keep clear of."""

    # The desk plane as last measured from the head depth (written at every planning); flat at DEFAULT_Z until then.
    PLANE_FILE = BASE_DIR / "output" / "desk_plane.json"
    DEFAULT_Z = 0.762
    # While following, a target may come this much closer than the arm's margin before streaming stops (the plan was
    # checked with the full margin; the wrist levelling moves the jaw a little).
    FOLLOW_TOLERANCE_M = 0.01
    # A trajectory starting below an arm's margin (at rest) may not come closer than its start by more than this (m).
    START_SLACK_M = 0.003
    # The desk is taken to reach the robot: arm points in front of this (base x, m) are checked.
    FRONT_X = 0.0
    # Mesh points per link, and the densest joint step checked along a trajectory (rad).
    SAMPLES = 300
    STEP_RAD = 0.02


class ArmConfig:
    """Per-arm values (component/real_robot/arm.py). Both grippers move their right half and keep the left one fixed:
    the left arm is a copy of the right, not a mirror (the user, 2026-10-09)."""

    # Which arm the interactive grasp (pick_target) uses: "auto" (the arm whose shoulder is nearer the object, as the
    # user asked on 2026-10-09: left of the line halfway between the shoulders the left arm, right of it the right arm;
    # if the nearer one cannot reach, the other), "right" or "left". The other tasks keep the right arm.
    CHOICE = "auto"
    ROBOT_CONFIGS = {"right": MotionConfig.ROBOT_CONFIG,
                     "left": BASE_DIR / "asset" / "curobo" / "xlerobot_left_arm.yml"}
    # Copies with the joints not planned locked where the real ones are (written at every planning).
    REAL_CONFIGS = {"right": RealPlanningConfig.ROBOT_CONFIG,
                    "left": BASE_DIR / "asset" / "curobo" / "xlerobot_left_arm_real.yml"}
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
    EXTRA_IGNORE_PAIRS = {"right": (),
                          "left": (("Upper_Arm", "Wrist_Pitch_Roll"), ("Rotation_Pitch", "Wrist_Pitch_Roll"))}
    # The approach check's expected fixed-jaw reading (measured - model, m), taken off before judging. The left one
    # reads -1.5 cm at every calibration pose even with its mount corrected (-0.7..-1.8): most likely its wrist camera
    # board, which sits elsewhere than the URDF draws it, in front of the fixed jaw's pixels (the right arm reads ~0).
    APPROACH_CHECK_OFFSET_M = {"right": 0.0, "left": -0.015}
    # How much higher than measured cuRobo sees the desk for each arm (RealPlanningConfig.TABLE_CLEARANCE_M
    # for the right): the left arm's real path dipped with its fingertips down towards the desk on
    # 2026-10-09 while the plan stayed 8.6 cm above it, so it keeps further up until that is understood.
    # The left one is a little above its desk guard margin (DESK_MARGIN_M, 6 cm): cuRobo's desk is a flat
    # box over the tilted plane and its spheres are not the guard's mesh points, so a plan at exactly 6 cm
    # came out 5.8 cm by the guard and the approach was refused (2026-10-09 19:35).
    TABLE_CLEARANCE_M = {"right": RealPlanningConfig.TABLE_CLEARANCE_M, "left": 0.065}
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
