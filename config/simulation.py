"""The simulation (MuJoCo) and its 3D view (viser); off by default."""

from __future__ import annotations

from config.settings import BASE_DIR


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
