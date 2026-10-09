"""The robot process's configuration (the real XLeRobot through lerobot: buses, keyboard teleop, rest poses,
cameras). Moved from xlerobot-teleop unchanged; config/settings.py holds the paths and both servers."""

from __future__ import annotations

from config.settings import BASE_DIR


class RobotConfig:
    """The real robot: lerobot's XLerobot (ports, cameras and the name xlerobot_smallcircle live in its config)."""

    # Arm and head positions in degrees (the gripper stays 0-100), which is also what XLeRobot's IK speaks.
    USE_DEGREES = True
    # The parts that get torque separately, and their labels on the page.
    PARTS = {"left_arm": "左臂", "right_arm": "右臂", "head": "头部", "base": "底盘"}
    ARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    HEAD_MOTORS = ("head_motor_1", "head_motor_2")
    # XLeRobot's motor tuning (P lowered from 32 against shakiness), written on connect without torque.
    PID = {"P_Coefficient": 16, "I_Coefficient": 0, "D_Coefficient": 43}
    # Extra tries for a bus read or write that gets no status packet (it happens over usbip, ~1 in 90k).
    BUS_RETRY = 3


class TeleopConfig:
    LOOP_HZ = 30
    # Speeds while a key is held (per second, so they do not depend on the loop rate).
    JOINT_SPEED_DEG_S = 30.0
    PITCH_SPEED_DEG_S = 30.0
    TIP_SPEED_M_S = 0.05
    GRIPPER_SPEED_S = 50.0
    HEAD_SPEED_DEG_S = 30.0
    # Follow mode (an outside trajectory drives an arm, e.g. the grasp service's MoveIt plan): each joint's target
    # moves at most this fast towards what arrives, and arrivals older than FOLLOW_WATCHDOG_S are not followed.
    FOLLOW_SPEED_DEG_S = 90.0
    FOLLOW_WATCHDOG_S = 0.3
    # Cap on shoulder_lift / elbow_flex while the tip moves: near the folded rest pose a few cm of tip need tens
    # of degrees of shoulder, so the tip slows down there instead of the arm swinging.
    IK_JOINT_SPEED_DEG_S = 45.0
    # Walking to the rest pose goes at this speed.
    HOMING_SPEED_DEG_S = 20.0
    # An arm or head target stays within this of where the joint is: a joint that stalls (the wrist camera's cable
    # stops wrist_roll at -73°) no longer winds its target up while a key is held, and turns back as soon as the
    # other key is pressed. The gripper is left out: a closing jaw stops on the object and should squeeze it.
    MAX_LEAD_DEG = 15.0
    # XLeRobot's three base speed levels (m/s, deg/s); the page starts on the slowest.
    BASE_SPEED_LEVELS = ({"xy": 0.1, "theta": 30.0}, {"xy": 0.2, "theta": 60.0}, {"xy": 0.3, "theta": 90.0})
    # No key message for this long (the page closed, lost focus, the network stalled): keys count as released.
    WATCHDOG_S = 0.3
    # Browser KeyboardEvent.code per action, for a compact keyboard without a numeric keypad (Logitech MX Keys
    # Mini). Left hand: XLeRobot's left-arm keys; right hand: the same shape mirrored; number row: head;
    # arrows and brackets: base. Signs measured on this robot: pan + = robot's right, wrist_flex + = down,
    # wrist_roll + = counter-clockwise seen from the wrist towards the fingertips (the user's check; an earlier
    # one had it the other way), gripper + = open, head_motor_1 + = look right,
    # head_motor_2 + = look down.
    KEYMAP = {
        "left_arm": {
            "pan_left": "KeyQ", "pan_right": "KeyE",
            "forward": "KeyW", "back": "KeyS", "up": "KeyA", "down": "KeyD",
            "pitch_down": "KeyZ", "pitch_up": "KeyX",
            "roll_cw": "KeyR", "roll_ccw": "KeyF",
            "open": "KeyT", "close": "KeyG",
        },
        "right_arm": {
            "pan_left": "KeyU", "pan_right": "KeyO",
            "forward": "KeyI", "back": "KeyK", "up": "KeyJ", "down": "KeyL",
            "pitch_down": "KeyN", "pitch_up": "KeyM",
            "roll_cw": "KeyY", "roll_ccw": "KeyH",
            "open": "KeyP", "close": "Semicolon",
        },
        "head": {"look_left": "Digit1", "look_right": "Digit2", "look_up": "Digit3", "look_down": "Digit4"},
        "base": {
            "forward": "ArrowUp", "backward": "ArrowDown", "turn_left": "ArrowLeft", "turn_right": "ArrowRight",
            "left": "BracketLeft", "right": "BracketRight", "slower": "Minus", "faster": "Equal",
        },
        "stop": {"stop": "Escape", "stop_space": "Space"},
    }
    # What each action is called on the page.
    ACTION_LABELS = {
        "pan_left": "左转", "pan_right": "右转", "forward": "前伸", "back": "后收", "up": "抬高", "down": "降低",
        "pitch_down": "腕下压", "pitch_up": "腕上抬", "roll_cw": "腕顺时针", "roll_ccw": "腕逆时针",
        "open": "张开", "close": "闭合",
        "look_left": "向左看", "look_right": "向右看", "look_up": "向上看", "look_down": "向下看",
        "backward": "后退", "turn_left": "左转", "turn_right": "右转", "left": "左移", "right": "右移",
        "slower": "减速", "faster": "加速", "stop": "停止", "stop_space": "停止",
    }


class RestPoseConfig:
    """The pose every powered part walks back to before its torque goes off (torque switch, service stop):
    without torque the arms fall onto the table. Set by hand by the user, recorded from the joints."""

    FILE = BASE_DIR / "config" / "rest_pose.json"
    # Further poses where torque may go on and off just as at the rest pose (config/<name>_pose.json). Torque off
    # walks each part to whichever of them it is nearest. The grasp start pose differs only in the head looking down
    # at the desk, which stays put without torque (the user, 2026-10-06: the robot stays there between grasps).
    OTHER_DOCKS = ("grasp_start",)
    # The walk is target-driven at TeleopConfig.HOMING_SPEED_DEG_S; this is only a backstop.
    TIMEOUT_S = 20.0
    # After the targets arrive, the soft position gain (P 16) needs a moment to catch up before torque goes off.
    SETTLE_S = 1.0
    # Every session starts from the rest pose or another dock (the user's rule; zero is only for calibration): an arm
    # or the head gets torque only when each of its joints is this close to one of them.
    START_TOLERANCE_DEG = 8.0
    # A park counts as done when every target is this close to the rest pose (Esc cancels a park: targets freeze
    # wherever they are, and torque then stays on).
    ARRIVED_DEG = 0.5


class CameraStreamConfig:
    # Order on the page.
    CAMERAS = {"left_wrist": "左腕", "head": "头部 RGB", "head_depth": "头部深度", "right_wrist": "右腕"}
    # The head depth view's colour scale (TURBO, near = blue, far = red); no depth = black.
    DEPTH_RANGE_M = (0.2, 1.5)
    JPEG_QUALITY = 70
    STREAM_HZ = 15
    # The head D415's two infrared images (the rectified stereo pair its depth comes from) are streamed too, at the
    # colour's size and rate, for a learned stereo depth (Fast-FoundationStereo) on the grasp side.
    HEAD_INFRARED = True
    # The head D415's frame rate for all its streams. Colour, depth and both infrared at 30 fps (~55 MB/s over usbip)
    # froze the colour stream every few minutes on 2026-10-09; the page streams at 15 Hz and grasping takes single
    # frames, so 15 fps halves the load at no cost.
    HEAD_FPS = 15
    # The head colour counts as frozen when its frame number has not changed for this long (s), and the head camera is
    # reconnected after this many failed reads in a row (~3 s at STREAM_HZ).
    FROZEN_S = 1.0
    RECONNECT_MISSES = 45
