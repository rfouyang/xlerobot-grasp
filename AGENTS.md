# AGENTS.md

Rules for working on this project (after g1-action-recorder). README.md explains what it does and how to run it;
doc/current_state.md what changed lately.

## Layout

- `app/` is interface only. `app/main.py` is the one entry (it brings up the robot process and ROS); `api/` holds the
  routes of the page process, `robot_api/` those of the robot process, `ui/` the pages. Every route module has a
  module-level `router`; a page does all its work through the API.
- Two processes, two facades: `app/context.py` creates `grasp_context` (`component/grasp_application.py`, the page
  process: pages, perception, planning, the tasks), `app/robot_context.py` creates `robot_context`
  (`component/robot_application.py`, the robot process: motors, cameras, keyboard teleop, stop). The API calls only
  its facade.
- Business rules live in a facade or in the block services, `component/<noun>_<activity>/service.py`, never in routes
  or JavaScript:
  - page process: `task_execution` (the steps), `task_chat`, `target_selection`, `place_choice`, `head_perception`,
    `head_preview`, `grasp_planning`, `box_placement`, `grasp_generation`, `grasp_simulation`, `camera_calibration`,
    `system_check`, `process_supervision`, `voice_dialog`;
  - robot process: `robot_link` (bus, torque, take-over), `camera_streaming`, `keyboard_teleop`, `device_matching`.
- `util/` wraps libraries, devices and files and knows nothing about tasks; it never imports from `component/`. Name
  helpers after the capability (`segment_helper.py`), not the domain.
- `config/` holds `XxxConfig` classes with UPPERCASE attributes, one module per domain.
- Dependencies point one way: `app -> component -> util`.

## Code

- One class per module, shared state in members, minor parameters in `**kwargs`.
- Every module ends with `demo_xxx()` called from `main()`, runnable from an IDE right-click; a demo that needs the
  robot says so in its docstring and moves nothing it does not say.
- `return` a named variable; logging via loguru. Comments in English say why; what the user sees and hears is Chinese.
- Errors: a service raises `ValueError` (cannot be chosen or done, said why), `TaskBusyError` (a step or a run is
  going) or lets `CameraStaleError` through; `app/api/errors.py` turns them into 422 / 409 / 503 with the words as
  they are. No `{"ok": False}` answers for errors. The chat says a refusal back instead of raising.
- Validate at the edges only: pydantic request models. No defensive checks elsewhere.
- Keep `tests/` passing (`uv run python -m unittest discover -s tests -t .`; stand-ins, no robot, models or GPT) and
  `uv run ruff check` clean for the files you touch (line length 120).

## UI

- Penpot (file 「XLerobot Grasp」, page 08) is the source of truth: change Penpot first, then the code; they never differ.
- Preline + Tailwind browser build, no frontend build step; plain scripts, one per page or part of a page
  (`task.js`, `task_chat.js`, `task_voice.js`); `common.js` is shared (`api()` for requests).
- Text in Chinese; the robot's sentences short (they are spoken).

## Robot

- Left and right are the robot's own sides; the head image's left is the robot's left.
- Real-robot motion only with the user's go-ahead. Never press an object or the jaw into the desk.
- Nothing that loads GPU models runs beside `app/main.py` (a second SAM load killed it once); test through the running
  service or with stand-ins.
