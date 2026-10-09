# xlerobot-grasp 重构方案（框架已确认 10-09，等开工指令）

依据：Penpot 文件「XLerobot Grasp」（8 页：总览、电脑端四个界面、iPad 横/竖屏、交互状态 12 种、抓取规划、ChArUco 标定、系统设置）+ 现有三个服务（xlerobot-grasp 7.4k 行、xlerobot-teleop 1.9k 行、xlerobot-ui 1.3k 行）。
目标：**所有代码收进 `xlerobot-grasp` 一个项目**，按 `app / component / config / util` 分层；界面按设计稿重做。**先定框架，不写代码。**

---

## 1. 关键决定（10-09 已全部确认）

| # | 问题 | 建议 | 原因 |
|---|------|------|------|
| D1 | 一个进程还是两个？ | **已定：一个代码库、两个进程**：`app/main.py`（网页 + 感知 + 规划，端口 8010）和 `app/robot_main.py`（电机、相机、键盘遥操作、停止，端口 8011） | 今天的教训：GPU 模型和可达图会占住 GIL，标注线程一帧 1.5 s。电机控制循环（30 Hz）和相机取帧如果在同一个 Python 进程里，会被卡住。分进程可以保证「停止运动」随时有效，抓取后台重启时手臂也不会掉。 |
| D2 | 浏览器连哪里 | 浏览器**只连 8010**。8010 把视频流、WebSocket、机器人接口转发到 8011（沿用现在 xlerobot-ui 的转发逻辑） | iPad 只需要一个地址；以后做 HTTPS（iPad 录音必须）也只需要配一处 |
| D3 | 仿真 / viser 3D 视图（`robot_simulation`、`scene_display`、`physics_sim_helper`、旧的 `app/ui_grasp`） | **已定：保留**，移到 `component/simulation/`，默认不启动 | 不删，以后调试还用得上 |
| D4 | LingBot-Depth（`depth_refine_helper`，已经关掉） | **已定：删除**代码、1.2 GB 权重，以及两个对不上的安装座 STL | 已决定放弃；FFS 替代了它 |
| D5 | 配置拆分 | `config/settings.py` 只放路径、服务、日志；按领域拆成 `config/robot.py`、`perception.py`、`planning.py`、`task.py`、`calibration.py`、`ui.py` | 现在两个服务加起来约 900 行、28 个 Config 类，放一个文件太长 |
| D6 | 语音转文字用什么 | **已定：BytePlus** 语音识别（和 TTS 同一家，key 已经在 `.env`） | 设计稿里有录音 → 转写 → 编辑 → 发送 |
| D7 | 旧服务 `xlerobot-teleop` / `xlerobot-ui` | **已定：归档**：迁移完、真机验证通过后改名为 `_archive_*`，不删 | 需要时可以回看 |
| D8 | 前端与接口 | **已定**：UI 用 Preline（Jinja + Tailwind + 原生 JS），API 用 FastAPI | 用户 10-09 确认 |
| D10 | 怎么启动 | **已定：只运行 `python app/main.py`**，它负责把其他进程和服务都带起来（见第 2.2 节） | 用户 10-09 确认 |
| D9 | 模型、日志、数据、启动脚本放哪 | **已定**：都放在项目仓库里：`model/`、`log/`、`data/`、`launch/` | 用户 10-09 确认，见第 2.1 节 |

---

## 2. 目录框架

```
xlerobot-grasp/
├── app/                          # 入口与接口层：只做「接请求 → 调 component → 回结果」
│   ├── main.py                   # 唯一入口：先启动下面的子进程和服务，再在本进程里跑网页 + API（8010）
│   ├── robot_main.py             # 机器人进程（8011）：硬件 + 键盘遥操作（原 xlerobot-teleop）；由 main.py 拉起，不用手动运行
│   ├── supervisor.py             # main.py 用的子进程管理：启动、健康检查、复用、重启、退出时清理
│   ├── context.py                # 两个进程各自的组件装配（app_context / robot_context）
│   ├── api/                      # 网页进程的 API，按页面/领域分
│   │   ├── robot_proxy_api.py    #  /api/robot/*、/stream/*、/ws/teleop → 8011
│   │   ├── task_api.py           #  抓取 / 抓取摆放任务：stage、run_all、reset、stop、target、place
│   │   ├── perception_api.py     #  头部画面标注（detect/target）、深度图、深度对比的冻结与导出
│   │   ├── planning_api.py       #  抓取候选列表、预览点位与接近路径、腕部对齐预览与应用
│   │   ├── voice_api.py          #  录音上传 → 转写 → 文字
│   │   ├── calibration_api.py    #  ChArUco：采样、计算、验证、版本、应用
│   │   └── system_api.py         #  自检、设备扫描与角色匹配
│   ├── robot_api/                # 机器人进程的 API（原 teleop 的 app/api_teleop）
│   │   ├── robot_api.py          #  status、torque、stop、rest、park、pose
│   │   ├── camera_api.py         #  stream、snapshot、rgbd
│   │   ├── follow_api.py         #  /ws/follow（MoveIt 跟随）
│   │   └── teleop_ws_api.py      #  /ws/teleop（键盘）
│   └── ui/                       # 页面（原 xlerobot-ui）
│       ├── routes.py             #  /teleop /grasp /pick-place /planning /depth /calibration /settings
│       ├── templates/
│       │   ├── base.html         #  侧边栏 + 顶栏（连接状态、力矩、停止运动）+ 底部服务状态
│       │   ├── partials/         #  camera_tile、task_panel、voice_input、task_progress、debug_drawer、status_badge
│       │   └── teleop.html grasp.html pick_place.html planning.html depth.html calibration.html settings.html
│       └── static/
│           ├── css/tokens.css    #  设计稿颜色、字号、圆角（Penpot 里取）
│           └── js/               #  common.js（接口、状态轮询、Esc/空格停止）、camera.js、voice.js、task.js、每页一个
│
├── component/                    # 业务能力：每个子目录一个能力，service.py 是对外入口，每个模块带 demo_ + main
│   ├── robot/                    # 【机器人进程】原 xlerobot-teleop
│   │   ├── link.py               #  串口总线、力矩、接管、限位（robot_link/service.py）
│   │   ├── head_calibration.py
│   │   ├── camera_stream.py      #  D415（彩色、深度、红外对，15 fps）+ 两个腕部相机，卡住检测与重连
│   │   ├── keyboard/             #  arm_teleop.py、head_base_teleop.py、service.py
│   │   └── robot_application.py  #  机器人进程的总装（原 teleop_application.py）
│   ├── perception/               # 看：头部帧、深度、识别、分割、选目标
│   │   ├── head_frame.py         #  取帧 + FFS 深度（perception/service.py 拆出）
│   │   ├── target.py             #  点选 / 文字 → 目标（YOLOE 命名 + SAM 分割 + 跟踪）
│   │   ├── scene_understanding.py#  【新】VLM（GPT 看图）：把「右边那个喝了一半的水」之类的描述 / 语音，
│   │   │                         #   变成具体目标（类别名 + 大致位置/框 + 放置位置），再交给 YOLOE → SAM（→ DINOv3 复核）
│   │   ├── preview.py            #  头部标注线程 + 深度预览线程（depth_preview/service.py）
│   │   └── depth_compare.py      #  【新】深度对比：冻结当前帧、导出对比图
│   ├── grasp_generation/         # GraspGenX（保留：scene、gripper_description、prediction、drink_models）
│   ├── planning/                 # 算：选手、可达、规划、安全
│   │   ├── arm.py                #  Arm（左右手差异都在这里）
│   │   ├── arm_choice.py         #  哪边肩膀近用哪只手、可达提示（从 execution.py 拆出）
│   │   ├── grasp_planning.py     #  cuRobo 规划（含 reachability、grasp_selection）
│   │   ├── reach_map.py
│   │   ├── desk_guard.py
│   │   ├── box_placement.py
│   │   └── candidates.py         #  【新】给「抓取规划」页的候选列表、点位投影、接近路径预览
│   ├── task/                     # 做：任务流程（execution.py 869 行拆开）
│   │   ├── runner.py             #  通用步骤机：stage / run_all / stop / reset / 进度与日志
│   │   ├── grasp_task.py         #  抓取：准备 → 识别与规划 → 抓取 → 放回 → 返回
│   │   ├── pick_place_task.py    #  抓取摆放：…… → 放到桌面位置 / 盒子内部
│   │   ├── bridge.py             #  MoveIt → 真机跟随
│   │   ├── wrist_alignment.py    #  腕部相机对齐（给「腕部视觉调整」面板出偏差、建议、历史）
│   │   ├── checks.py             #  到位检查、夹没夹住、瓶子立着没有
│   │   └── task_application.py   #  网页进程的总装（原 grasp_application.py）
│   ├── calibration/              # 【新页面】ChArUco 标定：把 scratch 里的脚本变成正式代码
│   │   ├── capture.py            #  采样（头部 / 左腕 / 右腕），覆盖率与有效性
│   │   ├── solve.py              #  内参、头部 PnP、腕部手眼 + 关节偏置（left_board_solve.py 等）
│   │   ├── versions.py           #  v1 / v2 草稿 / 应用 / 回退（config/calibration/ 下按版本存）
│   │   ├── check.py              #  calibration_check.py（头部深度看夹爪）
│   │   └── mount_model.py        #  相机安装座模型（mount_model.py）
│   ├── system_check/             # 【新页面】系统设置
│   │   ├── self_check.py         #  服务 → 相机图像 → 关节读数 → 规划依赖，五个阶段；不开力矩
│   │   └── device_match.py       #  相机/总线按序列号与物理端口匹配角色，预览确认后保存
│   ├── voice/                    # 【新】语音对话，全部用 BytePlus（D6）
│   │   ├── asr.py                #  听：浏览器录音 → BytePlus 流式语音识别 → 文字（可编辑后发送）
│   │   ├── dialog.py             #  对话：识别结果 → 任务描述；用语音回答「选中了什么、用哪只手、要不要确认、结果」
│   │   ├── tts.py                #  说：BytePlus TTS 播放（页面播放或 JBL 音箱）
│   │   └── alert.py              #  语音报警（现在的 AlertConfig 逻辑，重复播报直到处理）
│   ├── demo_learning/            # 【占位】人的 Demo 视频 → 抓取点位参考（设计稿标「后续接入」）
│   └── simulation/               # 仿真 + viser（默认不启动，见 D3）
│
├── config/
│   ├── settings.py               # PathConfig、ServerConfig（两个端口）、日志
│   ├── robot.py                  # RobotConfig、TeleopConfig、RestPoseConfig、CameraStreamConfig、JointMappingConfig、ArmConfig
│   ├── perception.py             # PerceptionConfig、HeadCameraConfig
│   ├── planning.py               # MotionConfig、SelectionConfig、RealPlanningConfig、ReachMapConfig、DeskGuardConfig、GripperConfig、GraspConfig、SceneConfig
│   ├── task.py                   # RealRobotConfig、PlaceConfig、MoveRightConfig、WristAlignConfig
│   ├── calibration.py            # 板型、字典、方格数等（标定页的表单默认值）
│   ├── voice.py                  # AlertConfig、ByteplusTtsConfig、ASR
│   ├── vlm.py                    # OpenAI 模型名、超时、图片尺寸；key 只从 .env 读（OPENAI_API_KEY）
│   ├── ui.py                     # 页面列表、相机标签、刷新频率
│   └── calibration/              # 标定数据（JSON），按版本存
│
├── util/                         # 和业务无关的工具（模型封装、通信、绘图）
│   ├── log_helper.py             #  三份合成一份
│   ├── robot_client_helper.py    #  网页进程调机器人进程（原 teleop_helper.py）
│   ├── proxy_helper.py           #  转发（原 xlerobot-ui）
│   ├── joint_mapping_helper.py  motion_plan_helper.py  moveit_helper.py
│   ├── detection_helper.py  segment_helper.py  stereo_depth_helper.py  grasp_helper.py
│   ├── openai_vlm_helper.py      #  【新】调 OpenAI GPT 看图（一张头部 RGB + 文字，返回 JSON：目标、框、理由）
│   ├── charuco_helper.py         #  【新】板子检测、PnP
│   ├── image_helper.py           #  【新】JPEG 编码、标注绘制（现在散落在 preview 里）
│   ├── byteplus_tts_helper.py    #  已有（移植自 g1-action-recorder 的 speech_generation）
│   ├── byteplus_asr_helper.py    #  【新】BytePlus 流式语音识别（WebSocket）
│   ├── speech_helper.py          #  播放
│   └── physics_sim_helper.py  scene_view_helper.py   # 仅仿真用
│
├── model/                        # 模型权重（不进 git）：graspgenx、sam3、yoloe、fast-foundationstereo
├── data/                         # 采集的数据：标定会话、深度评估集、以后的 Demo 视频（大文件不进 git）
├── log/                          # 两个进程的日志：grasp_YYYY-MM-DD.log、robot_YYYY-MM-DD.log（按天滚动，保留 14 天）
├── launch/                       # start_all.sh、restart_*.sh、stop_*.sh、keepalive.sh、usbip 挂载说明
├── output/                       # 程序生成的结果和缓存：reach_map、desk_plane、规划图、视频、sim_cache；scratch_* 只放临时文件
├── asset/                        # 进 git 的小文件：cuRobo 配置、物体网格、夹爪描述
├── ros_ws/  doc/
└── pyproject.toml                # 合并三个服务的依赖（teleop 的 lerobot、ui 的 jinja2）
```

### 2.2 只运行 main.py：启动顺序和子进程管理

`python app/main.py` 按顺序做下面这些事（每一步都会先检查已经在运行的，有就直接用，不会重复启动）：

1. **环境检查**：WSL 保活（keepalive）；systemd-timesyncd 必须是关闭的；四个机器人 USB 设备和 RealSense 都在（不在就在页面「系统设置」里报出来，网页照样起来）。
2. **机器人进程** `app/robot_main.py`（8011）：
   - 如果 8011 已经有健康的机器人进程，就**复用**它，不重启。这样 main.py 重启（比如改了代码）时手臂不会掉，力矩状态也不变。
   - 没有就用 `subprocess` 把它作为独立进程拉起来（新的进程组，日志写到 `log/robot_*.log`），等 `/api/status` 正常再继续。
3. **ROS bringup**（`ros2 launch xlerobot_moveit_config bringup.launch.py`，rmw_zenoh）：同样先检查 `ros2_control_node` 是否已经在跑，没有才启动，日志写到 `log/bringup_*.log`。
4. **本进程**：装配 component（感知、规划、任务、语音、标定、自检），启动 FastAPI（页面 + API + 转发），端口 8010。可达图在后台线程里算。

**`app/supervisor.py` 的职责：**
- 每 5 s 检查一次子进程是否健康。机器人进程挂了会自动重启（重启后它按现有逻辑接管仍有力矩的部件，不会让手臂掉下来），并在顶栏和「系统设置」里显示出来。
- **Ctrl+C 停止 main.py 时**：
  - 网页进程和 ROS bringup 退出；
  - **机器人进程默认保留**，手臂保持当前状态，下次 main.py 启动时复用；
  - `python app/main.py --stop-all` 会连机器人进程一起停：先回停靠位、关力矩，再退出。
- 「系统设置」页的「重新连接异常设备」「重启服务」按钮，都调 supervisor 去重启对应的子进程。

**main.py 做不到的（需要人在 Windows 上做）：** `usbipd attach` 把 USB 设备挂进 WSL（需要 Windows 管理员权限），以及 RealSense 偶尔出现「Set Address Failed」时需要拔插。main.py 检测到设备缺失会在页面上提示具体步骤；`launch/attach_usb.ps1` 提供 Windows 一键挂载脚本。

`launch/` 里只剩这些很薄的脚本：
- `start.sh`：`uv run python app/main.py`；
- `stop.sh`：调 `--stop-all`；
- `attach_usb.ps1`：Windows 一键挂载 USB 设备。

不再有一堆 restart_*.sh。

### 2.1 模型 / 数据 / 日志 / 启动脚本的搬迁

| 现在 | 以后 | 大小 | 做法 |
|---|---|---|---|
| `~/workspace/model/graspgenx` | `model/graspgenx` | 3.2 GB | `mv`（同一块盘，瞬间完成）；旧位置留软链接，防止别的程序还在用 |
| `~/workspace/model/sam3` | `model/sam3` | 3.3 GB | 同上 |
| `~/workspace/model/yoloe` | `model/yoloe` | 409 MB | 同上（YOLOE 的文本编码器 mobileclip2_b.ts 也在里面） |
| `~/workspace/model/fast-foundationstereo` | `model/fast-foundationstereo` | 1.2 GB | 同上 |
| `~/workspace/model/lingbot-depth` | 删除 | 1.2 GB | D4 |
| `~/workspace/model/wrist_cam_mount*` | 不搬 | 5 MB | 两个 STL 都不对，删除 |
| `output/calibration`（各次标定会话） | `data/calibration/` | 122 MB | 原始采样是数据，求解结果进 `config/calibration/`（按版本） |
| `output/depth_compare`、`output/depth_eval` | `data/depth/` | 11 MB | 评估用的帧 |
| `output/logs`（三个服务各一份，teleop 742 MB、ui 293 MB） | `log/` | 约 1 GB | 只留最近 14 天；loguru 按天滚动并压缩 |
| `output/scratch_20261006/*.sh` | `launch/` | — | 路径改成相对项目目录 |
| `~/workspace/launch`（空） | 删除 | — | |

- **不搬的第三方源码**：`~/workspace/app/` 下的 fast-foundationstereo、curobo、IsaacLab。它们是外部仓库，`config/settings.py` 里统一用 `THIRD_PARTY_DIR` 指过去。
- **路径集中定义**：`PathConfig` 统一定义 `MODEL_DIR`、`DATA_DIR`、`LOG_DIR`、`OUTPUT_DIR`、`ASSET_DIR`，全部相对项目根目录，代码里不再出现 `Path.home() / "workspace" / ...`。
- **`.gitignore`**：加上 `model/`、`log/`、`data/` 里的大文件、`output/`。

**分层规则：**
- `app/` 不写业务逻辑，只做参数检查、调用、返回。
- `component/` 之间可以互相调用，但不碰 HTTP。
- `util/` 不 import `component/`。
- `config/` 只放常量和数据。
- 每个模块都可以单独 `python -m component.xxx` 跑它的 `demo_`（沿用现在的约定）。

---

## 3. 设计稿每一页对应什么

| 页面（设计稿） | 现在有 | 要新做 |
|---|---|---|
| 键盘控制 | teleop 页（键盘、四路画面、停止、回停靠位） | 控制面板按钮（触摸可用）、速度三档、运行状态条 |
| 物体抓取 | 交互抓取页（点选 / 文字、可达提示、选手、分步执行） | **语音输入**（录音 → 转写 → 可编辑 → 发送）、「已选择」卡片、任务进度条、执行前确认 |
| 抓取摆放 | pick-place 页（瓶子 + 盒子、放进盒子） | 第 2 步「放置位置：桌面位置 / 盒子内部」点选模式、橙色标记 |
| 抓取规划 | 规划结果图 `plan.jpg`、对齐图 `align.jpg` | 候选列表（分数 / 可达 / 碰撞风险）、选中点位详情、预览接近路径、腕部偏差面板与历史曲线 |
| 深度对比 → 相机标定页的标签 | 原始深度 + FFS 深度两张图 | 三栏同色标、同步缩放、冻结当前帧、导出对比图 |
| 相机标定 | 只有 scratch 脚本 | **整页新做**：采样 → 计算 → 验证 → 版本管理 |
| 系统设置 | 只有 start_all.sh 和日志 | **整页新做**：自检五阶段、相机/关节状态、设备自动匹配 |
| 交互状态 12 种 | 部分（运行中、失败提示） | 统一状态机：语音 4 种 + 点选 3 种 + 任务 4 种 + 麦克风不可用 |
| 人的 Demo 视频 | 无 | 只放占位页（后续接入） |

---

## 3.1 每个页面用到的 component 模块

| 页面 | 接口（app/api） | component 模块 |
|---|---|---|
| 键盘控制 `/teleop` | `robot_proxy_api` → 机器人进程 `teleop_ws_api`、`camera_api` | `robot/keyboard/`、`robot/link.py`、`robot/camera_stream.py`、`robot/robot_application.py` |
| 物体抓取 `/grasp` | `task_api`、`perception_api`、`voice_api` | `voice/asr.py`（新）→ `perception/target.py`、`head_frame.py`、`preview.py` → `planning/arm_choice.py`、`arm.py`、`reach_map.py`、`grasp_planning.py`、`desk_guard.py` + `grasp_generation/` → `task/runner.py`、`grasp_task.py`、`bridge.py`、`wrist_alignment.py`、`checks.py`；出错时 `voice/alert.py` |
| 抓取摆放 `/pick-place` | 同上 | 物体抓取的全部，加 `perception/target.py`（放置位置点选）、`planning/box_placement.py`、`task/pick_place_task.py` |
| 抓取规划 `/planning` | `planning_api` | `planning/candidates.py`（新）、`planning/grasp_planning.py`（只预览不运动）、`task/wrist_alignment.py`（偏差、建议、历史）、`demo_learning/`（占位） |
| 深度对比 `/depth` | `perception_api` | `perception/head_frame.py`、`preview.py`、`depth_compare.py`（新） |
| 相机标定 `/calibration` | `calibration_api` | `calibration/capture.py`、`solve.py`、`versions.py`、`check.py`、`mount_model.py` + `util/charuco_helper.py`（新） |
| 系统设置 `/settings` | `system_api` | `system_check/self_check.py`、`device_match.py`，经机器人进程用 `robot/link.py`、`camera_stream.py` |
| 所有页面的顶栏和调试抽屉 | `robot_proxy_api`、`task_api` | `robot/link.py`（连接、力矩、停止）、`task/runner.py`（进度、日志） |

两个抓取页和抓取规划页共用 `perception/`、`planning/`、`task/` 的大部分；标定和系统设置相对独立。

## 3.2 界面修订（10-09 和用户讨论后确定）

**侧边栏六页：** 键盘控制、物体抓取、抓取摆放、抓取规划、相机标定、系统设置。「深度对比」不再是单独一页。

| 页面 | 定位 | 相对设计稿的修改 |
|---|---|---|
| 物体抓取 | 抓起来，再放回原处 | 见下面的「任务页共同修改」 |
| 抓取摆放 | 抓起来，放到指定的桌面位置或指定的盒子里 | 同上；第 2 步「放置位置」：在头部画面点选桌面点（橙色十字）或点选盒子（橙框），右栏显示是否可达 |
| 抓取规划 | **研究页**：抓哪里最合适。后续要从人的 Demo 视频学习抓取位置 | 保留为单独一页。<br>• GraspGenX 候选列表：分数、可达、碰撞风险、离示范点的距离。<br>• 选中候选的点位和接近路径预览（只规划不运动）。<br>• 腕部对齐偏差。<br>• Demo 视频面板：导入视频 → 关键帧 → 接触点 → 和候选对照。<br>• 评估记录：每次真机抓取的候选、结果和残余误差，用来研究「合适的抓取位置」 |
| 相机标定 | 标定加深度质量 | 页内分两个标签：「ChArUco 标定」和「深度对比」（原始深度 / FFS / 同色标、冻结、导出；显示 FFS 尺度 1.0072 的来源） |
| 键盘控制 | 不变 | 四路画面保持一样大（两只手都要看） |
| 系统设置 | 不变 | — |

**任务页共同修改（物体抓取、抓取摆放），第 4、5 点由 Claude 决定：**
1. **头部画面**：
   - 叠加右手可达区（浅绿）、左手可达区（浅蓝）、分手线（白）、目标（品红）、放置点（橙），右上角有图例开关；
   - 点击即选目标。
2. **手臂选择**：「已选择」卡片里有「手臂：自动（离哪边肩膀近）/ 左 / 右」。手动选的那只手够不到时会说明原因，并建议另一只手或把物体挪到哪里。
3. **执行方式**：
   - 「确认并开始」一次跑完；
   - 「单步」开关打开后，任务进度条每一步都可以点，配一个「下一步」按钮（现在调试最常用的方式）。
4. **安全与结果说明**：
   - 「需要处理」卡片写清楚原因和下一步，例如「桌面守卫：右手腕离桌面只剩 3.2 cm，不执行」「够不到：往右挪 6 cm」「没夹住，已张开退回」；
   - 完成卡片写结果，例如「瓶子立着（高 14.4 cm）」「已放进盒子」。
5. **页面布局**（用户 10-09 定）：
   - 头部 RGB 最大，在左上（可点选；叠加可达区、分手线、目标、放置点）；
   - 下面一排三个小画面，从左到右：**左腕相机 · 头部深度 · 右腕相机**。正在执行的那只手加蓝框和「使用中」，并叠加对齐十字和偏差；
   - **任务设置横放**在画面下面一行：目标（点选 / 文字 / 语音）· 已选择 · 手臂（自动 / 左 / 右）·（摆放页）放置位置 · 单步开关 · 确认并开始；
   - **任务进度竖放**在右侧一列：每一步一行（状态、耗时、说明），单步时可以点，底部是「下一步」按钮和结果 / 需要处理卡片。

   ```
   ┌───────────────────────────────────────────┬──────────────┐
   │ 头部 RGB（最大，点选）                    │ 任务进度     │
   │                                           │ ✓ 准备       │
   │                                           │ ● 识别与规划 │
   │                                           │ ○ 到瓶子后方 │
   ├─────────────┬─────────────┬───────────────┤ ○ 对准       │
   │ 左腕相机    │ 头部深度    │ 右腕相机[使用中]│ ○ 抓取 …    │
   ├─────────────┴─────────────┴───────────────┤ ○ 返回       │
   │ 任务设置：目标 [点选|文字|语音] 已选择 手臂 │ [下一步]     │
   │ （放置位置） □单步  [确认并开始]           │ 结果/需处理  │
   ├───────────────────────────────────────────┴──────────────┤
   │ ▸ 调试详情                                                │
   └───────────────────────────────────────────────────────────┘
   ```
6. **调试抽屉**（底部「调试详情」）：关节表、步骤日志、规划图（plan.jpg）、对齐图（align.jpg）。默认收起。

## 4. 分阶段做（每阶段结束都真机验证一次）

1. **搬家，不改行为**：新目录、合并依赖、两个进程入口，旧接口路径保持兼容。旧的三个页面能在 8010 打开，键盘控制和两只手抓取各跑一次。
2. **拆大文件**：`execution.py` 拆进 `component/task/`，`settings.py` 按领域拆开，scratch 里的标定脚本收进 `component/calibration/`。
3. **按设计稿重做外框和前四页**：base 布局、键盘控制、物体抓取、抓取摆放、深度对比（含 iPad 横/竖屏）。
4. **新页面**：抓取规划、相机标定、系统设置。
5. **语音输入**和 12 种交互状态收尾。旧服务归档。

---

## 5. 风险与注意

- 两个进程之间仍是 HTTP + WebSocket（和现在一样），没有新的通信方式。
- 「停止运动」在机器人进程里处理，网页进程挂了也能停（键盘 Esc 直连机器人进程的 WebSocket）。
- 每次 URDF 改动会重算可达图（8–10 分钟），重构期间尽量别动 URDF。
- iPad 录音需要 HTTPS：阶段 5 再配（自签证书或 Tailscale）。
- **BytePlus 语音识别**：工作区里还没有 ASR 代码（g1-action-recorder 只有 TTS）。按 BytePlus Seed Speech 文档的「ASR 双向流式 WebSocket」（`docs.byteplus.com/en/docs/byteplusvoice/asrbidirect`）来写，鉴权和 TTS 一样用 `.env` 里的 key（resource id 另配）。旧版 ASR 1.0 的接口（文档里 Legacy 的 ASR-Streaming）不要混用。文档页面是 JS 渲染的，开工时要对照原文核对：URL、请求头、帧格式、音频格式（16 kHz 16 位单声道 PCM 待确认）、中文支持。
- **VLM（10-09 用户提供 OpenAI key，存在 `.env` 的 `OPENAI_API_KEY`，权限 600，.gitignore 已包含 .env）**：
  - 用在 YOLOE 的词表说不清楚的时候：属性、相对位置、指代（「左边那个」「快空的那瓶」），以及语音对话里的理解。
  - 流程：`util/openai_vlm_helper.py` 输出 JSON，`component/perception/scene_understanding.py` 把它变成目标和框，然后接 YOLOE（框内找）→ SAM（分割）→ DINOv3（权限批下来后用于跨视角复核）。
  - VLM 只负责「理解是哪一个」，不负责坐标精度；精确位置仍然来自 SAM 掩码加 FFS 深度。
  - 网络失败或超时时退回现在的 YOLOE 文字提示，不阻塞抓取。
  - 模型名放在 `config/vlm.py` 里，可以改。

---

## 6. 进度（2026-10-09 20:00）

| 阶段 | 状态 | 提交 |
|---|---|---|
| 1 搬家：一个项目、`app/main.py` 一个入口、两个进程、model/data/log/launch | 完成 | 7657946 |
| 2 拆分：component 分目录、config 按领域拆、execution.py 拆成六个文件 | 完成 | 7bf5f52、af68146、d58aad2 |
| 3 按修订设计重做外框和两个任务页 | 完成 | 3f54dc5 |
| 4 新页面：系统设置（自检）、相机标定（ChArUco 实时检测、结果、深度对比）、抓取规划（候选、重排序、算法） | 完成 | 2b4f3b7、e5defb1、553ba40 |
| 5 语音输入（BytePlus ASR / 对话）、12 种交互状态收尾、旧服务归档 | 未开始 | — |

其他待办：
- 两只手的真机完整抓取各一次（重构后还没有完整跑过）；
- 放到桌面点；
- 标定页的采样、计算、验证；
- iPad 的 HTTPS。

