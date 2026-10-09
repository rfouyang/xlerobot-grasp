# Upstream

Source: https://github.com/Vector-Wangel/XLeRobot `simulation/Maniskill/assets/xlerobot/` (commit of 2026-09-24).

Changes made when importing `xlerobot.urdf`:

- `root_x_axis_joint`, `root_y_axis_joint`, `root_z_rotation_joint` turned into `fixed` joints: the first version keeps the base still.
- Mesh paths rewritten from `meshes/X.stl` to `package://xlerobot_description/meshes/X.stl`.
- Arm and jaw joints had `velocity="0" effort="0"`, which cuRobo rejects; set to `velocity="2.0" effort="2.9"`
  (STS3215: ~4.7 rad/s unloaded, 30 kg·cm ≈ 2.9 N·m stall).
- Added `right_grasp_frame` / `left_grasp_frame` (fixed to `Fixed_Jaw` / `Fixed_Jaw_2`, xyz `-0.02 0 0`, rpy `pi/2 0 0`):
  GraspGenX's canonical gripper frame (+Z approach, +X closing), so a predicted grasp is directly a target for this frame.
  It is the inverse of `GripperConfig.CANONICAL_*` in `config/settings.py`.
- Added `right_grasp_tcp` / `left_grasp_tcp`: the grasp frame moved 0.0745 m along its approach axis, to the
  centre between the fingertips (`fingertip` in the generated gripper `config.json`). cuRobo plans for this
  frame: the rotation a 5-DOF arm may not match is turned about the closing axis through the contacts.

Arm naming in this URDF: joints without suffix (`Rotation` ... `Jaw`) are the right arm, joints ending in `_2` are the left arm.
- `Elbow` / `Elbow_2` upper limit raised from `3.14159` to `3.30`: the real arm (xlerobot_smallcircle) folds to
  187° at its rest pose (real elbow 97° with XLeRobot's mapping Elbow = elbow + 90°, which the head camera's view of
  the gripper confirms), so π made the real start pose out of limits for cuRobo and MoveIt.
