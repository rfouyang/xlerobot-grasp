"""Tasks on the real robot: following, the wrist camera alignment, placing, the move-right task."""

from __future__ import annotations



class RealRobotConfig:
    """The real XLeRobot is reached only through the robot process (app/robot_main.py): it owns the serial ports and the
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
