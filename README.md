# xlerobot-grasp

XLeRobot (cart + two 5-DOF SO-101 arms) picks a drink (water bottle, cola can, Sprite bottle) standing on a table
in front of it, from the side, by the middle of its body:
GraspGenX predicts grasps, cuRobo v2 checks which the right arm can reach and plans the motion, MoveIt 2 executes
it through ros2_control, and a MuJoCo world follows the joint commands so the grasp happens with real contact
physics and is recorded to video. The console is a FastAPI + Preline page with a viser 3D view.

## Run

Two terminals in WSL Ubuntu-24.04, both from the project root (the Zenoh router runs as `rmw-zenohd.service`):

```bash
source /opt/ros/jazzy/setup.bash && source ros_ws/install/setup.bash
ros2 launch xlerobot_moveit_config bringup.launch.py
```

```bash
source /opt/ros/jazzy/setup.bash && source ros_ws/install/setup.bash
.venv/bin/python app/main.py
```

The grasp backend listens on **127.0.0.1:8012**; its page is xlerobot-ui's 抓取 (http://localhost:8010/grasp), which forwards `/grasp-api/*` here. The simulation's own page (`app/ui_grasp`, 3D view from port 8080) is kept but no longer mounted:

1. **机器人** — move either arm / jaw to the SRDF poses through MoveIt; live joint table.
2. **抓取生成** — stand a drink on the table in front of the right arm (it settles in MuJoCo), run GraspGenX on
   it, browse the grasps.
3. **抓取执行** — reachability, selection, planning and execution of one grasp; progress streams in, then the
   MuJoCo clip (30 fps, side view with a close-up inset) and the verdict (did the object rise with the jaw?).
   Clips are kept in `output/videos/`.

## Setup

```bash
uv venv --python /usr/bin/python3.12 --system-site-packages   # system site packages: rclpy / moveit_py
uv sync
(cd ros_ws && colcon build --symlink-install)
```

GraspGenX weights are read from `~/workspace/model/graspgenx` (`config/settings.py`).
Generated assets (re-run the module to rebuild them after changing `config/settings.py`):

- `asset/so101_gripper/` — `component/grasp_generation/gripper_description.py`
- `asset/objects/{water_bottle,coke_can,sprite_bottle}.obj` — `component/grasp_generation/drink_models.py`
- `asset/curobo/xlerobot_right_arm.yml` — `util/motion_plan_helper.py` `demo_build_robot_config()`

## Layout

```
app/                    FastAPI entry (main.py), context, page (ui_grasp/), REST + WebSocket (api_grasp/)
component/
  grasp_application.py  facade: the page and the command line only call this
  robot_operation/      named poses, joint state (MoveIt)
  grasp_generation/     tabletop scene, SO-101 gripper description, GraspGenX prediction
  grasp_execution/      reachability (cuRobo IK), selection, motion (cuRobo plan + MoveIt execution)
  robot_simulation/     MuJoCo world following the joint commands, clip rendering
  scene_display/        viser view: robot, table, object (follows the simulator), grasps
util/                   moveit / grasp (GraspGenX) / motion_plan (cuRobo) / physics_sim (MuJoCo) / scene_view (viser) helpers
ros_ws/src/             xlerobot_description (URDF + meshes), xlerobot_moveit_config (SRDF, ros2_control mock, controllers)
```

## Decisions worth knowing

- **Middle of a standing drink.** Upright on the table at (0.27, -0.133), straight in front of the right shoulder,
  where the arm reaches a level side grasp. `grasp_selection.py` keeps GraspGenX's level side grasps whose jaw
  centre lands in the middle 35–65 % of the drink's height, nearest the middle first, and adjusts each one:
  - *turned to face the shoulder* — a 5-DOF arm only approaches along the vertical plane through its shoulder's
    yaw axis; the drink is round, so turning a grasp about its axis keeps it a grasp;
  - *levelled* — approach and closing axis flat; a pitched jaw does not hold an upright body;
  - *seated in the jaw's pocket* — GraspGenX puts the axis ~10 cm into the jaw, past the moving finger's tip
    (6.6 cm), and closing then squeezes the drink out. The grasp is slid so the body touches the fixed finger's
    inner face just past the palm (`GripperConfig.POCKET_*`, measured on the collision meshes); the moving
    finger then wraps round its far side.
  What GraspGenX still decides: the height and which side the fixed finger goes.
- **5-DOF arm.** A grasp pose has 6 DOF, so cuRobo plans for `right_grasp_tcp` (between the fingertips) with one
  rotation free: the turn about tool y, which is vertical for a level side grasp (`MotionConfig.POSE_AXES_WEIGHT`).
  Freeing the closing axis instead lets the IK pitch the jaw down by 40–60°.
- **Sequence.** Arm to "ready" (jaw level, pulled back over the cart) before the drink is put down; approach to
  8 cm behind the grasp along the approach, open, move in, close, wait 0.5 s, lift 8 cm.
- **Payload.** The shoulder (STS3215, 2.9 N·m) needs ~2.55 N·m to lift the 0.57 kg water bottle at this reach:
  heavier or farther drinks will not come up.
- **SO-101 in GraspGenX.** The released model conditions on sweep volumes only, so the gripper is described by
  its sweep boxes (`make_sweep_volume_gripper_info`); no GraspGenX asset folder is needed.
- **Simulation.** ros2_control runs mock hardware; MuJoCo tracks its joint commands with position actuators
  (STS3215 torque limits) and is the physical world. Objects collide as CoACD convex parts carrying their whole
  mass (the visual mesh has none).
