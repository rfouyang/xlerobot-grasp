# 当前进度（2026-10-06 晚 23:10 收工）

## 2026-10-11 下午：拔掉机器人 USB 之后

- 相机发烫，机器人的 USB 和电源拔了一夜：看门狗每 20 s 重启一次机器人进程，一夜 1826 次。现在重启失败后
  等待时间翻倍（10、20、40 s……最多 `RobotServerConfig.RESTART_MAX_WAIT_S` 300 s），成功了就恢复每 5 s 检查一次（`tests/test_supervision.py`）。
- `launch/stop.sh`：app/main.py 40 s 没退出被强制结束时，原来连它的子进程一起杀，包括机器人进程（手臂有力矩时会掉）。
  现在只杀它的工作进程，机器人进程留着。
- 重构后的抓取摆放真机跑通（17:28：放置点离点的位置 0.4 cm，立着检查对，语音和步骤对得上）。
- `task_execution/service.py`（614 → 约 380 行）拆开：`run_state.py`（RunState：一轮里步骤之间传的东西，重置时换新的）、
  `plan_step.py`（PlanStep：检测并规划、选手、放到哪）、`align_step.py`（AlignStep：腕部相机对准）、`attempt_log.py`
  （AttemptLog：评估记录，`tests/test_attempt_log.py`）。TaskRunner 对外的方法不变；页面的规划图、对准图在 `runner.state`。
- 插回来后每次重启前都会自动挂 USB（`util/usbip_helper.py`）；RealSense 在 Windows 上出现得晚，下一次重启才能挂上，不用手动。

## 2026-10-11（第二轮）：整个项目按 g1-action-recorder 的框架整理（先读这一节和 AGENTS.md）

- component 每块一个 `<名词>_<动作>/service.py`，两个进程各一个门面：`grasp_application.py`（网页进程）、
  `robot_application.py`（机器人进程，原 `robot/robot_application.py`）。搬家对照：`robot/link.py` → `robot_link/service.py`、
  `robot/camera_stream.py` → `camera_streaming/service.py`、`robot/keyboard/` → `keyboard_teleop/`（底盘拆成
  `base_teleop.py`）、`robot/devices.py` → `device_matching/service.py`、`perception/service.py` → `head_perception/`、
  `perception/preview.py` → `head_preview/service.py`、`planning/` → `grasp_planning/`（`grasp_planning.py` → `service.py`）、
  `planning/box_placement.py` → `box_placement/service.py`、`calibration/` → `camera_calibration/`（`capture.py` →
  `service.py`，实时检测 `live_detection.py`）、`system_check/self_check.py` → `system_check/service.py`、
  `system_check/supervisor.py` → `process_supervision/service.py`、`simulation/` → `grasp_simulation/`、
  `voice/dialog.py` → `voice_dialog/service.py`。一个模块一个类（数据类各自一个模块，如 `real_scene.py`、`open_box.py`）。
- 所有接口模块都是模块级 `router`，只调门面；两个进程都注册 `app/api/errors.py`（ValueError 422、FileNotFoundError 404、
  TaskBusyError 409、CameraStaleError 503、其他 500，都回 `{"detail": 原话}`）。机器人接口的部件用 pydantic `Literal`
  校验；按关节移动、存位姿、去位姿的错误改成抛异常。力矩的结果（`on`、`away_from_rest`、`error`）还是数据。
- 页面全部改用 `common.js` 的 `api()`，`post()` 删了。
- 测试 `tests/test_pages_api.py`：整个网页进程的 app（`create_app()`）、各页接口、以及框架规则（util 不 import
  component、没有 `create_` 工厂、每个模块有 demo）。

## 2026-10-11：任务页这条线按 g1-action-recorder 的框架整理

- 规则写在 `AGENTS.md`（参照 g1-action-recorder）。
- 物体抓取 / 抓取摆放用到的代码搬到了：`component/grasp_application.py`（门面，原 `task/task_application.py`）、
  `component/task_execution/`（原 `component/task/`，`runner.py` → `service.py`，`tasks.py` → `stages.py`，
  `TaskBusyError` 在 `busy_error.py`）、`component/target_selection/service.py`（原 `perception/target.py`）、
  `component/place_choice/service.py`（新：放到哪里）、`component/task_chat/`（原 `component/chat/`，一个模块一个类）。
- 接口：`app/api/task_api.py`（原 `real_grasp_api.py`）和 `voice_api.py` 改成模块级 `router`，只调门面；
  `app/api/errors.py` 把 ValueError → 422、TaskBusyError → 409、CameraStaleError → 503，回 `{"detail": 原话}`。
  `/api/real/place_spot` 并进 `/api/real/place`（kind spot + point）；删了 `/api/voice/command`、`/api/target/chat`。
- 页面：任务页的 JS 拆成 `task.js`（状态、任务步骤、开始和确认）、`task_chat.js`（对话、点选）、`task_voice.js`
  （录音、语音讲解）；请求用 `common.js` 的 `api()`（出错抛 Error，带服务端原话和状态码）。
- 测试：`uv run python -m unittest discover -s tests -t .`（替身桌面，不要机器人、模型、GPT）；
  `uv run ruff check <任务页这条线的文件>`。其余页面（键盘、标定、系统设置、机器人进程）下一轮再按这个框架整理。

## 2026-10-09 晚：重构后的结构（先读这一节）

**现在只有一个项目**：`xlerobot-grasp`（git 仓库，第一次提交是重构前的基线 `450d2fd`）。xlerobot-teleop 和 xlerobot-ui 两只手真机验证通过后会归档，不再运行。

- **怎么跑**：
  - 只需要运行 `python app/main.py`（或 `launch/start.sh`）。它自己 source ROS，再由 `component/system_check/supervisor.py` 复用或启动机器人进程（`app/robot_main.py`，8011，独立环境 `robot_env/.venv`）和 ROS bringup，然后在 8010 提供页面、抓取 API（`/grasp-api/...`）和到 8011 的转发（`/robot-api`、`/robot-ws`）。
  - Ctrl+C 只停网页进程，机器人进程会保留，手臂保持原样；`launch/stop.sh --all`（即 `python app/main.py --stop-all`）先回停靠位、关力矩，再停掉全部进程。
  - Windows 重启后先用管理员 PowerShell 运行 `launch/attach_usb.ps1`。
- **目录**：
  - `app/`：入口、API、页面；
  - `component/`：
    - `robot/`：机器人进程；
    - `perception/`：头部帧、预览 `preview.py`；`target_selection/`：目标选择（点选、名字、盒子）；`place_choice/`：抓取摆放的放置；
    - `planning/`：arm、arm_choice、grasp_planning、reach_map、desk_guard、box_placement；
    - `task_execution/`：`service.py` 步骤机、`stages.py` 步骤表、`motion.py`、`checks.py`、bridge、wrist_alignment；`task_chat/`：任务页的对话；门面 `grasp_application.py`；
    - `calibration/`、`system_check/`、`simulation/`、`grasp_generation/`；
  - `config/`：`settings`、`robot`、`perception`、`planning`、`task`、`voice`、`simulation`、`charuco`；
  - `util/`；`model/`（权重）；`data/`（标定会话、深度数据）；`log/`（`grasp_*` / `robot_*`）；`launch/`。
  - 计划和每一页对应哪些模块：`doc/refactor_plan.md`。
- **页面**（Penpot「XLerobot Grasp」的 08 修订页）：
  - 键盘控制 `/`；
  - 物体抓取 `/grasp`：pick_target，离哪边肩膀近用哪只手，也可以手动选左 / 右，可以单步执行；
  - 抓取摆放 `/pick-place`：放进盒子，只用右手；
  - 抓取规划 `/planning`：候选漏斗、语义 / 示范重排序、选择算法；
  - 相机标定 `/calibration`：实时 ChArUco 检测、标定结果、深度对比；
  - 系统设置 `/settings`：只读自检、重启机器人进程。
- **今晚的修复**：
  - 桌面守卫允许动作从低于余量的位置出发，但不能比出发点更靠近桌面（`DeskGuardConfig.START_SLACK_M` = 3 mm）。左手抓取起始位按守卫的算法只有 5.9 cm（余量 6 cm），原来左手所有动作都会被拒绝。
  - 左手 cuRobo 的桌面余量 6.5 cm。
  - 头部画面标注单独开了线程，拿 GPU 锁。
  - 头部相机改为 15 fps。
- **另一个会话写的部分**（已收进来）：`util/demo_preferences.py` 和 xlerobot-demo 项目，提供 GraspMolmo / DenseMatcher / HOGraspFlow 的候选重排序，默认是 baseline。规划时用桌面守卫检查每一段（`desk_guard.check_segments`）。
- **重构后验证过**：19:56 左手（自动选手）、19:57 右手（手动选）各完整抓一次，瓶子都立着。旧服务已改名为 `_archive_*`。
- **语音**（任务页「● 语音」）：
  - 浏览器录 16 kHz PCM → `/api/voice/transcribe`（BytePlus ASR，`util/byteplus_asr_helper.py`）→ 文字填进输入框（可以改）；
  - 点「发送」：走 `/api/task/chat`（`component/task_chat`），GPT（`gpt-5-mini`，`OPENAI_API_KEY`）调用工具选物体、放置、开始，没网时用 `DialogConfig` 的词表；
  - 回答用 BytePlus TTS 在页面播放（`/api/voice/say`）。
- **10-09 20:00 之后又做了**（每项都离线验证过，真机运行留到验收）：
  - **HTTPS 8443**：`util/tls_helper.py` 自动生成自签名证书，覆盖局域网 192.168.31.57、Tailscale 100.120.140.36 和 localhost，给 iPad 用麦克风。页面和 wss 都验证过。iPad 访问还需要 Windows 防火墙放行（命令见 refactor_plan 第 7 节）。
  - **抓取摆放「桌面点」**（任务 `place_at_spot`）：在画面里点桌面上的位置 → 相机射线和桌面平面求交点 → 一个没有墙的「盒子」。用瓶子的实际半径（落点误差 4 mm 以内），放下高度等于抓取时的高度加放置余量，不会往下压。离线规划了两个点都成功，工具最低点比抓取高度高 3 mm。
  - **相机标定会话**：选左腕或右腕 → 人摆姿态、采帧 → 后台计算 → 存为 `config/calibration/<手>_wrist_<时间>.json` 草稿，不自动启用。求解器用 10-09 左臂的数据能精确复现当时的结果。用同一份数据在当前修正上重新求解，会建议把 wrist_flex 从 −7.92° 改回 −2.89°（也就是撤掉当时人工加的 5°），所以要人来决定启用哪一个。
  - **交互状态**：任务页开始前有「执行前确认」卡片，完成后有「新建任务」；语音四种状态、麦克风不可用的提示也都有了。
  - 抓取服务默认任务改为 pick_target，lint 全部通过。
  - **评估记录**（抓取规划页）：每次真机任务在 `data/grasp_records.jsonl` 里记一行，内容是：任务、手臂和选手方式、目标、物体位置和尺寸、选中的候选和分数、排序算法和名次、候选漏斗、对准偏差、每步耗时、结果（成功，或失败在哪一步、原因是什么，瓶子最后是否立着）；规划失败也记一行。页面上能看到最近的记录，以及按手臂、按算法统计的成功率。
  - **设备匹配**（系统设置页）：
    - 两个腕部相机按 vhci 口区分，`config/devices.json` 记录哪个口是左腕、哪个是右腕（默认口 3 = 左、口 4 = 右），机器人进程连相机前读它；
    - 页面上同时预览两只手的画面，可以「交换左右腕」（会重启机器人进程）或「确认映射正确」；
    - 自检会检查映射的口上有没有相机，没确认过的会提醒；
    - 交换测试：交换后左腕的画面就是原来的右腕（相关系数 1.00），已经换回原来的映射。
- **还没做**：
  - 放到桌面点；
  - 标定页的采样、计算、验证（现在仍用 `output/scratch_20261006` 里的脚本）；
  - 旧服务归档。


目标：用 GraspGenX + cuRobo v2 + MoveIt 2，让真机 XLeRobot（`xlerobot_smallcircle`）用**右臂**把桌上的小喷雾瓶抓起来，放回原处，再回到休息位。

**状态**：抓取放回已经稳定（白天连续 10 轮全过）。晚上在做两件事，**明天从这里继续**：
1. **抓取放置**（把瓶子放进桌上开口朝上的盒子，页面 `/pick-place`）：流程能跑通，20:00 有一轮瓶子成功进了盒子；但另一轮瓶子没夹住、留在桌上，流程却报了成功。详见第 6 节「抓取放置」。
2. **深度来源对比**（传感器 / 传感器 + LingBot / Fast-FoundationStereo / FFS + LingBot）：都已实现，做了第一轮对比，但那时场景里的瓶子够不着，还没比抓取成功率。详见第 6 节「FFS 双目深度」。

收工时：三个服务都在运行，力矩全关，机器人停在抓取位；配置仍是**传感器 + LingBot**（没切到 FFS）。桌上现在是：盒子横放在后面，里面立着三个瓶子，前面有一个透明塑料托盘（不是我们摆的）。

---

## 1. 三个服务和端口

| 端口 | 项目（`~/workspace/services/`） | 内容 |
|---|---|---|
| **8010** | `xlerobot-ui` | 唯一的网页入口，三个页面：「键盘控制」(`/`)、「抓取」(`/grasp`)、「抓取放置」(`/pick-place`)。浏览器的请求都由它转发 |
| 8011 | `xlerobot-teleop` | 电机、相机、30 Hz 控制循环、力矩、休息位。只监听 127.0.0.1 |
| 8012 | `xlerobot-grasp` | 检测、规划、真机抓取各步骤（ROS 2 / MoveIt mock + cuRobo + GraspGenX + YOLOE）。只监听 127.0.0.1 |

- 转发规则：`/robot-api/*` 和 `/robot-ws/*` 转到 8011，`/grasp-api/*` 转到 8012。代码在 `xlerobot-ui/util/proxy_helper.py`。
- 两个页面共用顶栏：力矩开关、休息位、抓取位、停止（Esc / 空格）。
- 抓取后台原来的仿真页面 `app/ui_grasp` 保留了，但没有挂载。

## 2. 明天开机的步骤

1. **USB 接进 WSL**（Windows 侧执行，顺序不能变，两个腕部相机靠接入顺序区分）：
   ```bash
   for b in 6-1 6-2 6-3 6-4 2-24; do "/mnt/c/Program Files/usbipd-win/usbipd.exe" attach --wsl --busid $b; done
   ```
   - 如果 usbipd 显示 Attached，但 WSL 里看不到设备，先 detach 再 attach。
   - usbipd 服务挂掉时，用管理员权限执行 `Start-Service usbipd`。
2. **防止 WSL 闲置关机**，否则 USB 会掉：
   ```bash
   pgrep -f "sleep infinity # wsl keepalive" >/dev/null || setsid nohup bash -c 'exec -a "sleep infinity # wsl keepalive" sleep infinity' >/dev/null 2>&1 < /dev/null &
   ```
   - **WSL 里不能开 NTP**（2026-10-06 排查）：`systemd-timesyncd` 已经 `disable`。它每 32 s 和 Hyper-V 对时打架，把时钟往回拨约 0.5 s，MoveIt 就会随机报 `start point deviates from current robot state`，导致某一步失败（bringup.log 里会周期性出现 `Moved backwards in time`）。如果又出现这个报错，先查 `systemctl is-active systemd-timesyncd`。
3. **按顺序启动三个服务：电机 → 抓取 → UI**，都在后台运行：
   ```bash
   # 电机与相机（连上电机但不上力矩，机器人不会动）
   cd ~/workspace/services/xlerobot-teleop && setsid nohup .venv/bin/python app/main.py > output/logs/service_stdout.log 2>&1 < /dev/null &
   # 抓取：先起 ROS bringup（MoveIt + ros2_control mock），再起服务
   cd ~/workspace/services/xlerobot-grasp && export RMW_IMPLEMENTATION=rmw_zenoh_cpp && source /opt/ros/jazzy/setup.bash \
     && (cd ros_ws && colcon build --symlink-install) && source ros_ws/install/setup.bash \
     && setsid nohup ros2 launch xlerobot_moveit_config bringup.launch.py > output/logs/bringup.log 2>&1 < /dev/null & sleep 10 \
     && setsid nohup .venv/bin/python app/main.py > output/logs/service.log 2>&1 < /dev/null &
   # 页面
   cd ~/workspace/services/xlerobot-ui && setsid nohup .venv/bin/python app/main.py > output/logs/service_stdout.log 2>&1 < /dev/null &
   ```
   - 停电机服务要用 `kill -INT`（只发一次）。它会先让所有部件回到最近的停靠位，再关力矩退出。
   - 抓取服务启动要 1～2 分钟，因为要加载 GraspGenX、cuRobo 和 YOLOE。
   - **只重启抓取服务**时，用 `kill -INT` 停掉 cwd 是 xlerobot-grasp 的那个 `.venv/bin/python app/main.py`，等 20 s，还在就 `kill -9`（退出时偶尔会卡在 zenoh 的 panic 里）。注意：在 `bash -c '...'` 里用 `pgrep -f "app/main.py"` 会匹配到这条命令自己的 shell（它的 cwd 也在项目里），把自己杀掉。要用 `pgrep -f '^\.venv/bin/python app/main\.py'` 并排除 `$$`。现成的脚本：`output/scratch_20261006/restart_grasp.sh`、`stop_grasp.sh`、`restart_teleop.sh`。
   - 只重启 teleop 前先确认力矩全关（它不会主动断力矩，但跟随会停）。
4. 打开 http://localhost:8010/grasp ，点「完整运行」即可，开始前会弹窗确认一次。也可以点「执行下一步」，一步一步执行。

## 3. 安全规则（用户定的，必须遵守）

- 所有操作都从**停靠位**开始、到停靠位结束。停靠位指休息位或抓取位，两者只差头部朝向，抓取位的头低着看桌面。所有手臂和头都在其中一个附近，才能打开力矩（2026-10-06 起，teleop `RestPoseConfig.OTHER_DOCKS`）。机器人平时就停在抓取位：关力矩后头不会掉（用户确认过）。
- 关力矩、停服务之前，每个部件都要先慢慢走到离它最近的停靠位再断开，否则手臂会砸到桌面。
- 零位只在标定时用。
- 测试时先开力矩，再到抓取位，然后才检测和规划。
- 真机要动之前，先得到用户同意。「完整运行」算一次同意，中间不用再逐步确认。
- 回休息位时所有关节同时到达，按关节空间直线走。以前每个关节各自 20°/s 走，夹爪扫到过桌面下方。

## 4. 抓取流程（12 步，`component/real_robot/execution.py`）

1. **准备**：开力矩，走到抓取位（头低到约 55°，两臂留在休息位）。
2. **检测并规划**：
   - 用 YOLOE 按提示词 "bottle" 检测，取最近的那个；用深度拟合出竖直圆柱。
   - GraspGenX 生成候选，只保留从侧面抓中段的；cuRobo 检查可达性，规划 接近 / 伸进 / 抬起 三段。
3. **到瓶子后方**：完成后用头部相机比较夹爪的实测深度和模型深度，偏差超过 2 cm 就停。
4. **腕部相机对准**：用右腕相机找瓶子，让瓶子左边缘落在定指内侧面（画面第 190 列）右边 1.5 cm 处。通过转肩部 pan 来左移，最多调 3 次。
5. **张开夹爪**。
6. **伸到瓶身中段**：直线往前伸 8 cm。
7. **闭合夹爪**：先往右移，让定指贴近瓶子（只留 0.3 cm），再闭合。这样动指只推瓶子 0.3 cm，以前推 1.5 cm 会把瓶子推倒。
8. **抬起**：抬 8 cm。负载下实际只抬了约 6 cm。
9. **放回原处**：沿抬起的路径原路放下（2026-10-06 去掉了原来的往右移 0.5 cm）。
10. **松开夹爪**：分两段。先只张开夹爪，等**实测**开度比夹住时大 10，说明动指已经离开瓶子；然后一边继续张开，一边往左移 2 cm。以前按目标值判断，把瓶子拽倒过。
11. **退出**：沿伸进来的路径直线退回。
12. **回到起始位**：沿接近的路径反向走回，前面累积的 pan 修正沿路逐渐减到 0；夹爪回到起始开度。

「完整运行」跑完会关力矩，停在抓取位，整轮约 58 秒。任何一步失败，机器人就停在原地。

## 5. 关键的标定和修正（都在 `config/settings.py`，注释里写了原因）

- **关节映射**：`JointMappingConfig`，公式是 真机角 = sign × URDF 角 + offset。
  - wrist_roll 的偏移是 **+90°**：真机 roll≈0 时两指左右开合，动指在机器人右侧。
- **右腕 roll 可用范围**：−65° ～ 170°。顺时针转到 −73° 时会被相机线缆拉住。
- **夹爪是不对称的**：左边的定指不动，只有右边的动指张合。所以不能按夹爪中心对准瓶子，否则往前伸时定指会撞到瓶子。
- **真机规划修正**（`RealPlanningConfig`）：
  - `SIDE_GAP_M` 1.5 cm：伸进时定指一侧留的间隙；
  - `SAG_RAISE_M` +1.5 cm：抓取点抬高，抵消负载下的下垂；
  - `DEPTH_BACK_M` 2 cm：抓取点往回收。URDF 里腕部相机支架没有碰撞体，真机夹爪也比模型多伸出约 1.4 cm；
  - `MIN_TABLE_FRONT_X` 0.20：桌面碰撞盒不能伸到车身下面，否则 cuRobo 会判定所有姿态都碰撞。
- **到位判定**（`RealRobotConfig`）：误差 ≤ 4° 算到位；或者负载下已经停住、且误差 ≤ 10° 也算。电机只有比例控制，抬瓶时 shoulder_lift 会稳定差 6～7°。
- **腕部保持水平**：`bridge.level`，腕部 pitch 补偿肩、肘的欠量，最多 ±15°。抬起时夹爪从倾斜 −10.9° 修正到 0°。
- **对准、松开相关参数**（`WristAlignConfig`）：
  - `CONTACT_COLUMN_PX` 190
  - `SIDE_GAP_M` 1.5 cm
  - `CLOSE_GAP_M` 0.3 cm
  - `RELEASE_FIRST_SLOWDOWN` 3（第 10 步第一段夹爪张开放慢 3 倍）
  - `RELEASE_CLEAR_M` 2 cm
  - `RELEASE_OPEN_FIRST` 10
- **MoveIt**：SRDF 里新增了 `right_arm_jaw` 组（右臂 + 夹爪），让手臂和夹爪能在同一条轨迹里同时动。

## 6. 待办 / 下次继续

- [~] **第 10 步松开会把瓶子带倒**（已改，观察中）（2026-10-06 09:08 那轮，录像在 WSL `/tmp/observe/090838/`，重启会丢）。瓶子是在第一段（只张夹爪、手臂不动）就跟着动指往右倒的，左移还没开始，所以不是定指蹭倒的，调 `RELEASE_CLEAR_M` 没用。怀疑松开时瓶底没稳稳落在桌上，或者瓶子在夹爪里往右歪着、靠动指撑着。下一步：逐步执行到第 9 步就停，拍照确认瓶底和瓶子姿态，再决定怎么松开。这一轮用的是 `LIFT_HEIGHT` 0.10，影响还没排除。
  - 09:33 那轮逐步执行到第 9 步停下：瓶底被夹爪挡住看不到；头部深度量出瓶子往前、往右（动指一侧）歪约 20°（检测框可能混进夹爪的点，不一定准）。用户不允许往下压瓶子（会伤机械臂）。按用户要求：去掉放回时的右移，第 10 步第一段夹爪张开放慢 3 倍（09:46 生效）。同一轮用旧代码松开时，瓶子没倒。09:47～09:50 用新代码连续跑 3 轮，都是 12 步全过，松开后瓶子都立着，录像里喷头始终竖直；每轮约 58 s，第一段张开约 2.7 s。改之前今天 4 轮倒了 2 轮，所以 3 轮全过还不能完全确定修好了，继续观察。
- [ ] 放回后瓶子仍往左偏约 1.0 cm，来源没查清（闭合只推 0.3 cm，补偿 0.5 cm）。用户说可以接受，目前不追。
- [x] 实际只抬起约 6 cm（负载下 shoulder_lift 稳定差约 5°）。2026-10-06 按用户要求把 `MotionConfig.LIFT_HEIGHT` 改成 0.10，预计实际约 8 cm，还没量过。
- [x] **瓶子倒了会语音叫人**（2026-10-06）：第 12 步回到起始位后，头部相机再量一次瓶子高度，不到规划时的 60 %（`AlertConfig.UPRIGHT_HEIGHT_SHARE`）或者看不到瓶子，就在 JBL 蓝牙音箱上用 BytePlus 播一遍 "Olis, the bottle handling failed. Could you please help move it straight up?"，等 5 s 再检测，一直循环到瓶子立起来。如果叫过人，先播 "Thank you! Please step away from the robot."，等 10 s（`CLEAR_WAIT_S`），动之前再播 "The robot is about to move. Please stay away from the robot."，然后才关力矩回休息位。等待期间机器人开着力矩停在起始位不动；按 Esc 或停止按钮（teleop 的 `follow_blocked`）、或者关掉右臂力矩，就会结束等待，这一步算失败。代码在 `execution.wait_upright`、`util/speech_helper.py`、`util/byteplus_tts_helper.py`，BytePlus 密钥在 `.env`（从 g1-action-recorder 复制过来）。用户要求只用 BytePlus、不要回退，而且要多播几遍。**还没在真机上触发过这个检查**。
- [~] **抓取太低，退出和回起始位时贴着桌面**（用户 2026-10-06 反馈）。09:53 起：真机只选瓶身高度 50～65 % 的抓取、越高越优先（`RealPlanningConfig.GRASP_BAND`，原来是 35～65 %、最靠中间的优先）；cuRobo 里的桌面抬高 2.5 cm（`TABLE_CLEARANCE_M`），所有路径都多留出这么多空间。第一轮：指尖中心 z 从 0.830～0.834 升到 0.856（约高 2.4 cm），31 个抓取可达，12 步全过、瓶子立着。离桌面多远等用户从侧面确认。仿真部分仍用原来的 `MIDDLE_BAND`。
- [~] **LingBot-Depth 修正头部深度**（2026-10-06）：`PerceptionConfig.DEPTH_REFINE`（默认开）。`Perception.locate` 拿到帧后先修正深度，后面的检测、桌面、圆柱拟合、倒瓶检查都用修正后的。代码直接从 `~/workspace/app/lingbot-depth` 导入，没有安装：它的包写死了 torch 版本，还会装 xformers。没有 xformers 时，helper 把单张图的 token 列表拼成一个张量来绕过。权重在 `~/workspace/model/lingbot-depth/vitl-14-v0.5/`，每帧约 0.2～0.9 s，显存约 2.8 GB。抓取页有「原始深度（实时）」「LingBot 深度（实时）」两块面板：`component/depth_preview` 在后台线程里约每秒 2.7 帧（`PREVIEW_HZ` 上限 3）生成两张图，用固定色标 0.25～1.1 m（`PREVIEW_RANGE_M`）；只在页面最近 3 s 内拉取过时才运行。它和检测共用一个模型，用锁保证同一时间只做一次推理。接口是 `/api/depth/{raw|refined}.jpg`。 **规划这一步期间预览会暂停**（`RealGraspExecution.gpu_lock`）：cuRobo 规划时要录制 CUDA graph，录制中途插进别的 GPU 操作，就会报 `operation failed due to a previous error during capture`，之后这个进程的 CUDA 就坏了，必须重启抓取服务（10:35 用户自己运行时遇到过）。3 轮完整抓取全过；**但 50～65 % 高度带里的候选很少**（2～9 个，用原始深度时有 53 个），10:17 那次一个都没有，规划失败。**已查清并修复**：在同一位置对比，原始深度有约 290 个水平侧抓，LingBot 只有约 45 个。原因是 LingBot 在瓶子掩码边缘补出约 2% 的散点，夹在瓶子和背景之间。`observe()` 现在先拟合一次圆柱，去掉离瓶轴超过 1.5 倍半径的点（`RealPlanningConfig.STRAY_RADIUS_SHARE`），再重新拟合；改完后高度带内有 72 个，规划成功（10:23）。10:24～10:27 用这版连续跑 3 轮完整抓取，全部 12 步通过，瓶子都立着；每轮去掉 103～141 个散点，高度带内候选 45～68 个，选中的抓取分数 0.68～0.86，每轮约 48 s。每轮放回后瓶子往机器人左边（+y）挪 1～2 cm（-0.076 → -0.067 → -0.046 → -0.033）。瓶子实测高 14.5 cm，两种深度量出来都是 14.1～14.3 cm。
- [~] **「把瓶子往右移」动作**（2026-10-06）：每轮放回后瓶子往左（+y）挪 1～2 cm。正常抓取在第 2 步检测后，如果瓶轴 y > `MoveRightConfig.LEFT_LIMIT_Y`（-0.02），就停下来，机器人不动，在抓取位关力矩，在页面提示条上问用户：「把瓶子往右移」还是「照常抓取」（`POST /api/real/action {action, ignore_left}`）。往右移动作有 13 步：抬起后多一步「往右移瓶子」，转肩部 pan 把瓶子移回 `HOME_Y`（-0.11，右肩正前方附近），一次最多 12 cm（11:01 第一次真机运行：从 y +0.022 指令移 8 cm，实际横移 6.4 cm 到 -0.042，瓶子立着；用户嫌不够，从 -0.07 / 8 cm 加大）；之后在右边放下，松开、退出、回起始位时都带着这个 pan 偏移，回起始位的过程中再逐渐减回 0。用户要求：发现太靠左时先告诉他，由他决定做不做。10:56 第一次触发（y -0.017，建议右移 5.3 cm），
- [~] **相机标定**（2026-10-06，ChArUco 板：7×10，格子 18.0 mm，码 13.5 mm，DICT_4X4_50，新版图案）。数据在 `output/calibration/session_20261006/`，结果在 `config/calibration/head_and_wrist_20261006.json`。做法：板子平放在桌上，右臂摆 18 个姿态，用右腕相机看板子（姿态由离线采样生成，检查了关节限位、腕部转动范围、离桌面至少 6 cm；**没有检查手臂和机器人自身的碰撞**），同时头部相机在抓取位也看着板子。结果：①右腕相机内参用了 11 张图，重投影误差 0.31 px，k1 = -0.34；②腕部手眼：相机比 URDF 往前约 48 mm，光轴往下倾斜约 33°，18 个姿态推算出的板子位置平均相差 7.5 mm；③头部深度偏大 0.98%（404 个角点），**已生效**（在 `TeleopHelper.rgbd` 里修正 depth_scale）；④头部相机外参和 URDF 差 2.9° 加约 1.5 cm，按它算瓶子应该偏左 2.3 cm、偏近 1.5 cm，但 11:39 用上之后到位检查从 +1.3 / -0.7 cm 变成 -1.0 / -3.0 cm，深度方向修正过头，所以**已关闭**（`HeadCameraConfig.APPLY_EXTRINSIC = False`），`DEPTH_BACK_M` 也改回 2 cm；11:42 跑一轮正常。原因推测：外参是通过右臂运动学推出来的，标定时手臂举高往下看，抓取时姿态差别很大，关节零位的偏差在两种姿态下造成的误差不同。**选项 2 已完成（11:4x～11:57）**：右臂摆 16 个接近抓取的姿态（夹爪水平，指尖在离桌面 7～16 cm；用户觉得 7 cm 太低按了停止，之后改成 11 / 16 cm），头部相机看夹爪，用截尾 ICP 把深度点云对齐到夹爪和手腕网格上，得到修正：旋转 2.8°，平移约 1 cm（留一法检验：旋转相差 ≤1.1°，平移 ≤8.3 mm）。结果在 `config/calibration/head_on_jaw_20261006.json`，**已生效**（`HeadCameraConfig.APPLY_EXTRINSIC = True`），主要效果是瓶子算出来偏左约 1.5 cm。12:01～12:03 两轮：定指到位偏差 -0.1 / +0.1 cm（标定前 +1.1～+2.0），腕部对准修正从往左 1.5～2.5 cm 变成往右约 0.75 cm（横向有点修正过头）。动指偏差在所有姿态下都约 -1.3～-2 cm，方向和定指相反，相机修正解释不了，怀疑是夹爪开合映射或模型的问题。所以到位检查**只判定定指**（`APPROACH_CHECK_LINKS`），动指只记录。待办：标定夹爪开合映射。`MoveRightConfig` 的阈值和目标都随标定加了 1.5 cm（-0.005 / -0.095）。12:06 往右移动作在退出时，一个 GET 请求遇到 `Server disconnected without sending a response`（服务端刚好关掉了复用的长连接），手动算了一条笔直后退 8 cm 的路径撤出；`TeleopHelper.get` 现在对只读请求自动重试一次，POST 不重试。14:40 起又加了横向微调 `HeadCameraConfig.BASE_TRIM_M` = (0, -0.010, 0)，依据是之前 6 次抓取里腕部对准平均往右修正 0.98 cm；标定后的头部相机位姿统一由 `calibration_check.head_camera_pose()` 计算。14:44～14:47 三轮：腕部对准修正 0.0° / 0.0° / -1.5°（标定前是 -3～-4°），定指到位偏差 +0.2～+0.6 cm，瓶子都立着，每轮往左漂 0.4～1 cm。**夹爪开合映射没标成**：在高处让夹爪张到 5 个开度，用头部深度对齐动指模型，代价曲线几乎是平的（差 0.3 mm），动指太薄、深度噪声太大。之后改用右腕相机，也没成功：腕部手眼的平移误差约 1～2 cm，手指离相机只有 5～10 cm，模型投影完全对不上；想用定指轮廓反推相机位姿，优化又会落到不合理的解上。动指张大后（读数 30 以上）也会转出腕部画面。最后改成**夹已知宽度的物体**（16:xx，手臂不动，夹爪碰到就停）：指尖刚好碰上时读数约 0.5（模型约 0）；夹住 30 mm 宽的打印码（3 cm，DICT_4X4，0～4 号）时读数 16.0（模型约 19.3，按沿闭合方向、过 TCP 的间隙算，每个读数单位约 0.85 mm）。零点基本对；比例可能偏小约 20%，但两个点在手指上的位置不同，不够确定，**映射没改**。这个偏差只让动指偏几毫米，解释不了到位检查里动指 -1.5～-2 cm 的偏差，后者更可能是 URDF 里动指模型的问题。待办：再夹一个更宽的物体（比如瓶身）作为第三个点。注意：一直往 0 闭合会把物体挤出去（用户提醒用力太大），要用碰到就停的方式（`/tmp/gripper_touch.py` 的做法：读数落后指令 3 个单位就停，往里只压 1 个单位）。语音提醒前会先把 Windows 音量调到至少 65 并取消静音（`AlertConfig.MIN_VOLUME_PERCENT`，脚本 `asset/windows/ensure_volume.ps1`）。**15:44～15:52 连续 10 轮全部成功**（用户不在场，我自己跑的）：每轮 46～51 s，瓶子每次都立着，没有叫人；腕部对准修正平均 0.39 cm（最大 1.23 cm，有 5 轮是 0），定指到位偏差 -0.7～+0.2 cm，动指 -0.0～-2.8 cm；瓶子从 y -0.061 漂到 -0.019，平均每轮往左 0.42 cm。
- [ ] **抓别的瓶子**（2026-10-06 傍晚）。提示词还是 "bottle"，取最近的那个。①**半瓶矿泉水**（直径 6 cm，高 20.5 cm，约 0.3 kg）：17:56、18:00 两次都没夹住，夹紧时读数到了 0~1。录像显示动指合拢时把瓶子往左前方挤了出去，第二次还把瓶子碰倒了（语音叫了 2 次，有人扶起后机器人道谢、提醒远离、等 10 s 才动，这一套在真实场景里工作正常）。用户的判断：转动式夹爪是斜着夹的，受力不稳。已经让粗瓶子伸得更深（`RealPlanningConfig.DEPTH_BACK_BY_RADIUS`：半径 1.8 cm 往回收 2 cm，半径 3 cm 不往回收），但伸深之后腕部相机支架会顶到瓶身，挤出的问题照样存在。建议改成粗瓶子夹瓶颈（直径约 2.5～3 cm）：要改抓取高度带、按高度取局部半径、腕部对准找瓶颈的边缘，**还没做**，等用户决定。②**小透明瓶**（半径 1.8 cm，高 13.9 cm，透明瓶身带透明盖）：18:03 一次成功，夹紧读数 15，抬起时肩部欠 5.1°，放回后立着；LingBot 去掉了 228 个散点。
- [~] **抓取放置：把瓶子放进盒子**（2026-10-06 晚，用户要求）。
  - **页面** `/pick-place`（`xlerobot-ui` 的 `templates/pick_place.html`，继承 `grasp.html`，脚本共用 `grasp.js`）。动作是 `place_in_box`，共 13 步：准备 → 检测瓶子和盒子并规划 → 到瓶子后方 → 对准 → 张开 → 伸进 → 闭合 → 抬起 → 送到盒子上方 → 放进盒子 → 松开（同时左移让开定指）→ 抬高后退出 → 回到起始位。后台一次只跑一个任务，页面发现后台在跑另一页的任务时，会提示切换。
  - **页面头部格子**：显示后台的检测画面（`/api/depth/detect.jpg`），绿框是瓶子、橙框是盒子，带置信度；只有这个页面打开时才跑检测（`DepthPreview.framed`，用自己的一份 YOLOE）。
  - **代码**：`component/real_robot/box_placement.py`，参数在 `PlaceConfig`。
    - 检测：YOLOE 提示词 "box"，置信度 0.1，在**深度修正之前**的深度上量。
    - 筛选和外框：盒沿高 2～12 cm 的才算开口盒子，取离右肩最近的。外框用 PCA 找长轴，范围取掩码里所有高于桌面的点的 2～98 百分位。最早只用墙上部的点，盒子正对机器人时，薄侧壁在原始深度里读数偏低，被平滑到盒底，外框量成 10.8 × 0.4 cm（22:14）。
    - 放置点：在盒内按离右肩的距离排序，优先 `PREFERRED_REACH_M` 0.41。实测右臂保持夹爪水平、在盒子上方时，盒子在左边（y +0.13）最远只能放到 x≈0.22～0.23；盒子在正前方（y ≈0）时放到了盒子中间（0.274, 0.033）。
    - 送瓶：送到盒子上方时，cuRobo 只知道手臂，不知道夹爪下面挂着的瓶子，所以把盒子障碍物加高「瓶子挂在夹爪下的长度」。盒子障碍物从 x ≥ 0.15 开始截断，因为折叠的左臂就在盒子近端上方，不截断会让所有状态都判成碰撞。
    - 退出：用户要求**先竖直抬到盒沿上方、再水平后撤**，因为盒子有高度，往下走会撞盒沿。张开的夹爪最低点要高出盒沿 `EXIT_CLEARANCE_M` 2 cm，后撤和回起始位时盒子障碍物也加高 2 cm；如果规划出的轨迹里工具往下沉超过 0.5 cm（`EXIT_DIP_M`），就换下一个放置点。
  - **真机结果**：
    - 20:00 一轮成功，瓶子立在盒子里，靠近盒子左壁和近端。
    - 20:02 那轮（已经是先抬后退的退出），13 步都报完成，但瓶子**留在桌上原处**（往左偏约 1 cm），没进盒子。推测是闭合后或抬起时滑掉了（闭合读数 15，上次成功时是 16）；运动中没看相机，不确定。
    - 最后的「瓶子立着」检查只看瓶子竖不竖，不看它在哪里，所以没发现失败。
  - **下一步**（用户还没回答「瓶子是在哪一步掉的」）：
    1. 抬起后用头部相机确认瓶子已经离开原位，否则停下来报告；
    2. 结束时确认瓶子在盒子的范围内；
    3. 先抬后退的退出还没在真机上带着瓶子验证过。
  - **把夹着的瓶子放回原处的应急脚本**：`output/scratch_20261006/back_to_table.py`（它 exec 同目录的 `jaw_poses.py`，用前把路径从 /tmp 改过来）。19:4x 用它把瓶子放回桌上时，瓶子倒了：原地张开、没有往左让开定指。正式流程的松开是带左移的。
- [~] **FFS 双目深度**（2026-10-06 晚，用户要求三种配置都实现并对比）。
  - **teleop**：D415 多开两路红外流（`CameraStreamConfig.HEAD_INFRARED`；`RobotLink.with_infrared` 给 lerobot 的管线加上 infrared 1/2，y8，640×480，30 fps）。`/api/rgbd?infrared=1` 多带 `ir_left`、`ir_right`、`K_ir`（fx 598.1）、`baseline_m`（55.0 mm）、`color_from_ir`（左红外到彩色约 15.1 mm）；不加参数时和原来一样。
  - **grasp**：
    - `util/stereo_depth_helper.py`：Fast-FoundationStereo 算视差 → 深度 → 投影到彩色相机，每个点填落点周围 4 个像素，取最近的。
    - 代码直接从 `~/workspace/app/fast-foundationstereo` 导入，权重是研究版 23-36-37，在 `~/workspace/model/fast-foundationstereo/`（Google Drive 那份全下了，1.2 GB；**只能研究用**，商用要 C-Fast-FoundationStereo）。
    - 环境里新加了 `einops`（`uv add einops`）。
  - **开关**：`PerceptionConfig.DEPTH_SOURCE`（"sensor" / "stereo"）× `DEPTH_REFINE`。`Perception.with_depth` 统一产出 `depth`（规划用）、`depth_raw`（LingBot 之前）、`depth_sensor`；页面的实时深度预览也跟着这两个开关走。
  - **对比脚本**：`output/scratch_20261006/depth_compare.py`，同一批帧走完整条链路。结果在 `output/depth_compare/20261006_225920/`（`report.json`、`depth.jpg`、`bottle_top.jpg`）。跑的时候要先停抓取服务，否则显存不够（12 GB 卡）。
  - **第一轮结果**（5 帧，目标是盒子里的透明喷雾瓶，**够不着，四种配置规划都是 0/5**）：

    | | 整图有深度 | 每帧耗时 | 瓶子散点 | 盒宽 / 盒沿 | GraspGenX 候选 / 前 10 分 |
    |---|---|---|---|---|---|
    | 传感器 | 79% | 0 | 0.54% | 13.9 / 3.5 cm | 439 / 0.99 |
    | 传感器 + LingBot（现用） | 100% | 0.19 s | 0.72% | 14.1 / 3.1 cm | 384 / 0.90 |
    | FFS | 86% | **0.08 s** | **0.08%** | **12.9 / 3.6** cm | **490** / 0.96 |
    | FFS + LingBot | 100% | 0.29 s | 1.81% | 13.2 / 3.2 cm | 347 / 0.89 |

    （盒子真实宽约 10～11 cm、盒沿 4.6 cm；各项帧间标准差都很小）
  - **初步结论**：
    - FFS 单独用最好：覆盖比传感器多，散点最少，盒子边缘最准，最快。
    - FFS 后面再加 LingBot 是冗余的，还会把点撒进瓶子内部、在瓶子后面拖出散点，GraspGenX 分数最低。
    - 透明瓶对所有配置都难：点云都是约 1.5 cm 厚的弧带。
    - FFS 量的距离比校正后的传感器远约 4 mm（桌面 0.758 对 0.762～0.766），需要单独做尺度标定（之前 0.98% 的修正只针对传感器）。
  - **明天**：
    1. 瓶子单独放回桌上右臂够得着的地方，重跑对比，看规划成功率和抓取点稳定性；
    2. 如果 FFS 仍然最好，用 ChArUco 板标 FFS 的尺度；
    3. 真机 A/B（传感器 + LingBot 对比只用 FFS，各抓几轮，需要用户在场）；
    4. 再决定默认配置。
- [~] **2026-10-08 用户定了新方案：放弃头部的 LingBot**。头部只用 FFS 双目出深度，点云直接给 GraspGenX；腕部相机负责抓取前的校准（现在的对准只用像素），可以考虑用 LingBot 给腕部相机估深度（腕部只有 RGB：LingBot 支持不给输入深度 `depth_in=None`，即单目估计，尺度要用已知尺寸校）。已改：`DEPTH_SOURCE = "stereo"`、`DEPTH_REFINE = False`（头部不再加载 LingBot）；抓取页两块深度面板改成「头部传感器深度」和「头部 FFS 深度（规划用）」；新增 `HeadCameraConfig.STEREO_DEPTH_SCALE`（**还没标定，暂为 1.0**）。标定脚本 `output/scratch_20261006/ffs_scale_calib.py`：标定板放在桌上头部视野里，每秒一帧、边采边挪板子，用 PnP 的角点距离对比 FFS 深度（也顺带复核传感器的 0.98%）。这些改动**还没在真机上跑过**（10-08 WSL 刚重启，服务没起）。
- [~] **2026-10-08 晚**：FFS 尺度已标定，读数偏远 0.72%（`config/calibration/head_ffs_scale_20261008.json`；48 帧 1618 个角点，板子都在 0.48～0.51 m；同一批数据里传感器复核出 0.96%）。第一轮 FFS 完整抓取在第 3 步停了：**teleop 的相机线程因为一次 `Frame didn't arrive within 1000` 整个退出**（加了红外两路以后 usbip 上的数据量更大），所有画面冻结，到位检查拿空桌面和夹爪模型比，误报 +11.2 cm；手臂其实走对了（用户确认在瓶子前方），关力矩收回。已修：teleop 相机取帧失败只记警告、下一轮重试，状态里新增 `camera_age_s`；抓取侧 `TeleopHelper.rgbd()` / `camera()` 拒绝超过 `HeadCameraConfig.MAX_FRAME_AGE_S`（1.5 s）的画面。另外：用户同时在跑 3D 建模（SpatialLM / SpaCeFormer），4070 Ti 的 12 GB 显存基本占满，规划一次要几分钟（FFS 一帧 19 s），用户说将就。
- [~] **2026-10-08 深夜～10-09：只用 FFS 的完整抓取成功**（23:48，12 步全过，54 s；到位检查定指 +0.3 cm，腕部对准修正 0°，去掉散点 15 个）。腕部相机只有 RGB，试过 LingBot 不给深度做单目：用 10-06 标定的腕部图片对比，读数是真实距离的 2.2～6.8 倍（它总估 0.7～0.9 m），**不能用**，腕部暂不加深度（脚本 `output/scratch_20261006/wrist_lingbot_test.py`）。电脑重启后：USB 总线号会变（这次 6-x → 4-x），RealSense 可能卡在 `Set Address Failed`，要拔插；一键启动 `output/scratch_20261006/start_all.sh`（USB 先接 4 个机器人设备、最后接 RealSense）。
- [~] **交互抓取页面** `/interactive-grasp`（2026-10-09，用户要求：鼠标点选或文字描述要抓的物体）。后台任务 `pick_target`（步骤同抓取，没有太靠左的拦截），目标只在这个任务里生效，其他页面仍抓最近的 bottle。点选：`POST /api/real/target {point:[u,v]}`，YOLOE 免提示词模型（`yoloe-26l-seg-pf.pt`，`PerceptionConfig.PICK_*`，第一次点击时加载）取点击处置信度 ≥ 0.2、面积 ≤ 25% 的最小掩码，用它的名字当提示词；文字：`{prompt: "..."}`（英文，YOLOE 的文本编码器类似 CLIP）；`{clear: true}` 恢复默认。规划和之后的检查只认点选位置 30 px 内的那个检测（`Perception.point`，规划后跟到物体掩码中心），腕部对准用同一个名字找。页面头部格子是 `/api/depth/target.jpg`：品红是目标、绿是同名候选、红叉是点击处。**还没在真机上从这个页面抓过**。抓取流程仍按竖直瓶状物设计。SAM 3.1、SAM 3、DINOv3（ViT-L、ViT-S）的 HF 权限用户 10-09 已申请，token 存在 WSL `~/.cache/huggingface/token`（建议换成只读 token）；批下来后接 SAM 3.1 做点选分割（要 torch 2.7+，得单独环境），DINOv3 用来在腕部画面里认出同一个物体。
- [~] **交互抓取 10-09 的进展**：①SAM 3.1 已接入（HF 权限批了，`~/workspace/model/sam3/sam3.1_multiplex.pt`，经 Ultralytics 在 torch 2.6 下跑，`util/segment_helper.py`）：点选用 SAM 出掩码（约 0.65 s，显存约 2.4 GB），免提示词 YOLOE 只负责起名（和 SAM 掩码 IoU 最大的那个）；规划和之后的检查在目标位置重新用 SAM 分割，面积和规划时差 3 倍以上就改用 YOLOE 按名字在 80 px 内找（`TRACK_*`）；点到桌面 / 垫子（掩码超过画面 25%）会被拒绝。DINOv3 两个还没批。②真机：00:07 点选中间的喷雾瓶（旁边有绿瓶）成功；00:13 点漱口水（关灯，FFS + SAM 点云去掉 736 个散点、半径只有 1.4 cm）在腕部对准失败（YOLOE 那一刻没认出，同一张图事后 0.84），已改成拿 GPU 锁、最多看 3 次；00:19 再跑**没夹住**（夹爪合到 1），流程照常走完，最后检查在原位置用 SAM 分到桌面，判成倒了，**半夜语音叫了 46 次**才被我停下。已加：闭合后夹爪读数不到 5（`RealRobotConfig.EMPTY_JAW_READING`）就张开、沿原路退回、报失败。**语音叫人的上限还没定**（建议 10 次，等用户决定）。09:2x 用户自己跑：中间瓶子成功；左边漱口水 (0.278, 0.13) 规划失败（够不着）。③**可抓取范围**（`component/real_robot/reach_map.py`，`ReachMapConfig`）：桌面 1.5 cm 一格，算水平侧抓和后退 8 cm 的预抓取有没有 IK 解、腕部转动是否在范围内，364 / 874 格可抓，算一次 52 s，存 `output/reach_map/`。今天 4 个实际情况全部对得上。点选时按物体实际高度再算一次，够不着就提示往哪挪多少（按机器人的左右）；规划失败时也给建议；交互抓取页头部画面上浅绿底就是可抓范围。
- [x] **左手抓取（2026-10-09 中午 12:15 第一次成功）**。
  - **双臂结构**：`component/real_robot/arm.py`（`Arm`：关节名、夹爪、TCP、腕部相机、MoveIt 组、cuRobo 配置；`ArmConfig` 放每只手的参数）。左手是右手的复制不是镜像，**两只手都是右半边夹爪动、左半边不动**，对准 / 闭合 / 松开逻辑照用。左手 cuRobo 配置 `asset/curobo/xlerobot_left_arm.yml`，SRDF 加了 `left_arm_jaw`。teleop 不用改。
  - **选手**：交互抓取任务里**哪只手的肩更近用哪只**（`ArmConfig.CHOICE = "auto"`，两肩中线即 y≈0，页面上画白线）；更近的那只够不着或规划失败才换另一只。其他任务只用右手。可抓取范围两只手各一张（右 364 格、左约 380 格），页面浅绿 / 浅蓝。
  - **左手标定（踩过的坑）**：用头部深度对齐左夹爪（上午 9 个、中午 10 个姿态）**不可靠**：左腕相机电路板的实物位置和 URDF 不一致，在头部画面里最显眼，拟合结果（关节偏 30°、底座高 2.4 cm 等）都是被它带偏的，按它改过 URDF 和映射，导致左手指尖往下扎、差点撞桌（已全部撤回，URDF 恢复原样）。**可靠的做法**：左腕相机看桌上的 ChArUco 板（头部 PnP 定板子位置），15 个姿态 12 张有效，解出左腕内参（0.38 px）、手眼和关节零点：肩 pan +1.54°、lift −4.49°、肘 −4.49°、手腕俯仰 −2.92°（转动不稳定，不用）。手腕俯仰和相机安装角在这种数据里分不开，用户看到夹爪仍往上翘约 5°，再加 −5°（共 −7.92°）后用户确认水平。修正在 `config/calibration/left_arm_20261009.json`，由 `JointMappingConfig.ARM_CORRECTION_DEG` 只作用于左手。数据 `output/calibration/left_board_20261009`，脚本 `output/scratch_20261006/left_board_*.py`。
  - **左手其他参数**：`CONTACT_COLUMN_PX` 181（左腕相机里定指内侧，标定照片上量的）；`REACH_EXTRA_M` 左 2.5 cm（左手实际伸得比模型短，原来抓空，用户指出「伸的不够远」）；`HEAD_CHECK` 左 False（头部相机测左夹爪一直偏高约 4.5 cm，用户看是对的，所以左手不做预抓取修正和到位检查）；休息位肘部超出 URDF 上限，规划起点会夹回范围内（`within_limits`）；左手休息位有两对碰撞球重叠，已忽略（`EXTRA_IGNORE_PAIRS`）。
  - **桌面安全守卫**（`component/real_robot/desk_guard.py`，用户要求）：标定中有一个姿态**撞到了桌面**（只检查了 x>0.15、平桌面、左手模型不准）。现在每次规划用头部深度拟合桌面平面（含倾斜，存 `output/desk_plane.json`），机器人前方都算桌面；每段轨迹执行前逐点检查（手臂所有网格点高出桌面右 5 cm、左 6 cm），跟随时每个发给真机的目标也检查，不够就停。回放撞桌的那个姿态（0.7 cm）会被拦下。
  - **其他保护**：teleop 头部彩色流卡住检测（帧号 1 s 不变就算失败，约 3 s 自动重连；10-09 出现过彩色冻住、深度还在走）；闭合后读数 ≤5 判为没夹住，张开沿原路退回。
  - **桌面相对机器人倾斜约 5°**（前低后高，10-06 起就是这样），没查清是机器人歪还是头部相机标定的俯仰误差；头部相机对左侧的测量偏差可能与此有关，待查。
- [x] **2026-10-09 下午：双臂按位置各抓一次，4/4 成功**：(0.282, 0.14) 左手（右手够不着的位置）、(0.218, 0.118) 左手、(0.211, 0.027) 左手（离左肩 32 / 右肩 34 cm）、(0.223, −0.021) 右手。
  - **桌面倾斜查清了**：10-06 经右臂运动学 + 右腕相机测的标定板（不经头部相机）前后坡度 −5.65°，头部相机看到 −4.9°，两种独立方法一致：**机器人相对桌面往后仰约 5.5°**（或桌子前低后高），不是头部相机标错。左右方向两者差约 2.5°，是头部相机的侧倾误差（影响约 1 cm，待修）。规划的桌子碰撞盒现在按头部深度实测的平面摆放（`TabletopScene.table_plane`），桌面守卫用同一个平面。
  - **腕部相机安装板不在 URDF 里**：两只手腕后面都有一块竖着的白色板子（相机安装板），URDF 里只有一个小的相机件，位置也对不上。头部深度做 3D 对齐时会把它拟合进去：右手预抓取被读成偏 3 cm（定指检查只有 +0.2 cm），**左手上午那些离谱的标定结果主要也是它造成的**。已关掉「预抓取实测修正」（`RealPlanningConfig.CORRECT_ENABLED = False`），右手恢复只看定指的到位检查。**待办：把安装板补进 URDF（碰撞模型），否则桌面守卫和 cuRobo 都看不见它。**
- [~] **2026-10-09 下午（续）**：
  - **相机安装板加进了 URDF**：`Right_Camera_Plate` / `Left_Camera_Plate`（固定在定指上的 7.5 × 5.9 × 1.5 cm 盒子），尺寸位置是从 10-06 右手标定的头部深度里拟合的（`output/scratch_20261006/plate_fit.py`），投到头部画面上和实物重合、深度差 −0.2 / +0.8 cm。**桌面守卫和到位检查会检查它**；但**没放进 cuRobo**：放进去后右手 78 个候选全被判不可行（它被拟合在指尖方向、手指上方 4 cm，侧抓时和瓶子碰撞盒相交，而真机抓了很多次都没碰瓶子），沿手指方向的位置可能拟合得不准。cuRobo 配置恢复成没有板子的版本（带板子的备份 `output/scratch_20261006/*.yml.with_plate`）。**待办：用尺子量板子的实际位置再放进 cuRobo。**
  - **头部相机侧倾 2.5° 没修成**：加上安装板重解头部相机（`head_resolve.py`），点云对得更贴，但看标定板的前后坡度反而从 −5.3° 变成 −1.2°（参考 −5.65°），没采用。要修得同时用「夹爪点云 + 桌面 / 板子必须和右臂测的一致」两种约束联合求解。
  - **teleop 控制循环会被头部相机重连搞死（已修）**：14:26 头部彩色流卡住触发自动重连，lerobot 的 `send_action` 检查整机「已连接」时把正在重开的相机也算进去，抛 `DeviceNotConnectedError`，控制循环直接退出：之后手臂不再执行目标、关节读数冻结（停靠超时、右手「真机没跟上 77.6°」）。现在控制循环某一步出错就跳过（底盘停、手臂保持），下一拍继续。抓取侧的跟随线程出错也会写日志并标记停止。**头部彩色流下午频繁卡住**（加了红外两路后 usbip 带宽紧？），待查。
- [~] **其他晚上的改动**：右手退出时手臂下沉的问题按上面改了；规划失败的提示会区分「盒子测出来放不下瓶子（可能没测准）」和「够不到」。用户问过 lingbot-vision：只发布了骨干网络，不能直接用；LingBot-Depth 2.0（用它做编码器）发布后值得在透明瓶上试。
- [ ] 左臂的映射还有几厘米误差（标定检查时发现的），目前只用右臂，没处理。
- [x] 抓取页上头部 RGB 的检测框，页面收到后 60 s 自动清掉（2026-10-06，`xlerobot-ui/app/ui/static/common.js` 的 `OVERLAY_KEEP_MS`）。
- 三个项目（xlerobot-grasp / xlerobot-teleop / xlerobot-ui）目前都**不是 git 仓库**，代码只有本地这一份。要不要建仓库、推到哪里，由用户决定。
  - **头部彩色流卡住（10-09 15:00 已缓解）**：14:26～14:48 卡了 4 次。头部 D415 四路（彩色、深度、两路红外）原来都是 640×480 @ 30 fps，经 usbip 约 55 MB/s。现在改成 15 fps（`xlerobot-teleop` 的 `CameraStreamConfig.HEAD_FPS = 15`，在 `robot_link.connect()` 里设置），带宽减半；页面本来就是 15 Hz，抓取只取单帧，没有损失。14:56 重启后到 15:39 共卡住 1 次（15:25，3 s 自动重连恢复），之前是 25 分钟 4 次。改善了但没根除；下一步可以只在 FFS 需要时才开红外两路。
  - **相机安装座按实物建模，并放进了 cuRobo（10-09 15:35）**：用户给了实物照片（侧面和背面）。实物是一个 35 mm 宽的折弯支架：脚用 2 颗螺丝固定在定指顶面（靠腕部电机那头），往上一小段后折向前，相机板用 4 颗六角螺母装在上段背面，镜头朝前下方。官方的两个 STL（`~/workspace/model/wrist_cam_mount*/`）都对不上，已放弃。现在 `Right/Left_Camera_Plate` 是定指坐标系里的 3 个盒子：
    - 相机面 + 模组 38 × 50 × 21 mm，法向沿手眼标定的光轴，镜头在厚度中间、宽度居中，盒子中心比镜头高 6 mm；
    - 腿：从相机面下沿到定指顶面；
    - 脚：贴在定指顶面。
    这个模型和头部深度里随定指刚性运动的体素对比：去掉侧面线缆后，81% 的体素离模型在 5 mm 以内（脚本 `output/scratch_20261006/photo_boxes.py`，结果在同目录的 `mount_boxes.json`；写入脚本 `apply_mount.py`，离线规划检查 `plan_both.py`）。两手共用同一个模型，左手是复制件。
    cuRobo 四个配置（普通和 real，左右手）**只加了**这个支架的 48 个球（相机面 r 10.5 mm，腿 r 6 mm），其他连杆的球没动。原来那份带板子的配置「所有候选都不可行」，原因其实是重建脚本把所有连杆的球都重新拟合了。离线规划（只看头部、不动手臂）：右手 25/82 候选可达，三段轨迹都规划成功；左手 0 个，但瓶子在中线偏右，不装支架的配置也是 0 个（够不到，跟支架无关）。备份在 `output/scratch_20261006/*.before_mount_1535`。桌面守卫自动包括这三个盒子（10-09 左手撞桌的那个姿势仍然会被拦）。右手真机验证通过（16:11，交互抓取：候选 21/56 可达，抓起、放回、瓶子立着）。另外修了一个由标注线程引起的问题：它不拿 GPU 锁时，cuRobo 录 CUDA 图会报错「operation failed due to a previous error during capture」，现在标注每帧都拿锁，拿不到就跳过这一帧。左手真机验证通过（16:35，安装座第 2 版：抓起、放回、瓶子立着）。
  - **抓取放置 / 交互抓取页面的头部画面卡（10-09 16:00 已修）**：这两个页面的头部画面（`detect` / `target`，带框和标注）是抓取后台画的；键盘控制页和抓取页直接用 teleop 的视频流，所以不卡。原因有两个：
    1. 标注画在深度预览循环里，每帧都要先等 FFS 跑完。现在单独开了 `depth_preview_overlay` 线程：只取彩色、跑 YOLOE，最高 `PerceptionConfig.OVERLAY_HZ` = 10 Hz，不等 GPU 锁。FFS 只在页面要深度图（raw / refined）时才跑。一帧超过 1 s 会记日志「头部画面标注慢」。
    2. 可达图的缓存键里有 URDF 的修改时间，所以每次改 URDF 后，第一次重启要重算两只手的可达图（cuRobo IK，8～10 分钟）。重算期间它占着 GIL 和 GPU，标注一帧要 1.5 s。现在每算一格让出 5 ms。**改了 URDF 以后，重启一次就等它算完，别反复重启**（每次重启都会从头算）。

    3. 两个页面同时开着时，两种标注共用一个 YOLOE、提示词来回切换，每帧要重新编码文本（约 0.5 s）。现在交互抓取用自己的 YOLOE（`target_detector`）。另外 `restart_grasp.sh` 设了 `OMP_NUM_THREADS=8`：原来 24 个 CPU 线程空转，占了约 6 个核。实测：只开交互抓取 9.1 帧/s，两个页面加 FFS 深度一起开 8.8～8.9 帧/s（之前约 0.5 帧/s）。
  - **安装座模型第 2 版（10-09 16:25）**：左手规划下午起一直 0 个候选，原因是**左手的安装座复制了右手的位置**。左腕相机自己的标定（`left_board.json`）显示镜头比右手**靠后 1.8 cm、低 1 cm**，复制来的相机面正好压在瓶子顶上：去掉瓶子障碍时 IK 30/30，加上就 0/30，只有 `Left_Camera_Plate` 和瓶子盒子重叠。另外原来那块 21 mm 厚的板子把镜头前面整个宽度都算进去了，太保守。
    现在 `output/scratch_20261006/mount_model.py` 按**各自手臂的腕部相机标定**放置，形状改成四块：
    - 板 + 相机板在镜头后面，38 × 50 × 11 mm；
    - 镜头筒在前面，16 × 16 × 10 mm；
    - 腿；
    - 脚。
    离线规划（同一个场景）：右手 26/80 可达，左手 24/71 可达。
  - **注意**：`prepare_planner` 每次规划都会从基础配置 `xlerobot_{side}_arm.yml` 重新生成 `*_real.yml`，所以只改 `_real.yml` 不会生效（我之前一次「不装支架也是 0」的对比就是因为这个才无效的）。
  - **注意**：抓取页的 `grasp` 动作还带着最早只用右手时的「瓶子太靠左」拦截（y > −0.5 cm 就不规划）。真机测试要用交互抓取的 `pick_target`，它会按哪边肩膀近选手。

