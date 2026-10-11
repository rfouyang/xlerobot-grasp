# xlerobot-grasp

XLeRobot（底盘 + 两只 SO-101 手臂 + 头部 RealSense D415）的操作台：键盘遥操作、物体抓取、抓取摆放、抓取规划、相机标定、系统自检。
全部在 Windows 上的 WSL Ubuntu-24.04 里运行，浏览器打开网页操作（电脑或同一局域网里的 iPad）。

## 每次开机：从机器人上电到可以用

1. **摆好机器人。** 两只手臂和头放在**休息位**（上次断力矩时停的位置，手臂收在身前）。上力矩时每个关节都要在休息位或抓取位附近 8° 以内，否则不给上力矩。桌面上手臂会经过的地方不要放东西。
2. **接线、上电。** 打开机器人电源。5 根 USB 线都插到这台 Windows 电脑：
   - 2 根电机总线（CH343 串口，左右各一）
   - 2 个腕部相机
   - 头部 RealSense D415（插 USB 3 口，蓝色口）
3. **打开 WSL。** 在 Windows 上打开「Ubuntu-24.04」终端。
4. **启动。** 在终端里一行一行输入：

   ```bash
   cd ~/workspace/services/xlerobot-grasp     # 1. 进项目目录（后面的命令都在这个目录下运行）
   source .venv/bin/activate                  # 2. 进入项目的 Python 环境
   python app/main.py                         # 3. 启动
   ```

   - 第 2 步之后，命令行最前面会多出 `(xlerobot-grasp)`，表示现在的 `python` 是项目自己的 Python 3.12（`.venv/bin/python`），里面装着项目用到的所有包。没看到 `(xlerobot-grasp)` 就是没进去：检查是不是在项目目录里。
   - **不要跳过第 2 步。** 没进环境时，Ubuntu 上没有 `python` 这个命令（只有系统的 `python3`，它没有装项目的包）。
   - 每开一个新的终端窗口都要重新做第 1、2 步；`deactivate` 退出这个环境。
   - 不需要另外 `source` ROS：`app/main.py` 会自己带上 ROS 2 Jazzy 和 `ros_ws` 的环境重新启动一次。机器人进程用的是另一个环境 `robot_env/.venv`（lerobot 和主环境的包冲突），也由 `app/main.py` 自己调用，不用手动进。

   第 3 步这一条命令会依次做完下面所有事情，约 20 秒：
   - 把机器人的 5 个 USB 设备从 Windows 挂进 WSL（不需要管理员；前提是它们已经共享过，见下面「USB 设备：怎么从 Windows 连进 WSL」）；
   - 启动机器人进程（电机、相机、键盘遥操作，端口 8011）；
   - 启动 ROS 2 bringup（MoveIt 规划用）；
   - 启动网页和抓取服务（http 8010，https 8443）。

   看到 `XLeRobot：http://localhost:8010` 就好了。这个终端窗口保持打开；按 Ctrl+C 停止网页（机器人进程继续运行，手臂不会掉）。
   想放到后台运行（关掉终端也继续跑）就把第 3 步换成 `bash launch/start.sh`：它自己用 `.venv/bin/python`，可以不做第 2 步；输出写到 `log/main_stdout.log`，用 `tail -f log/main_stdout.log` 看。

5. **打开网页。**
   - 这台电脑：<http://localhost:8010>
   - 局域网里的 iPad / 别的电脑：`https://<这台电脑的局域网 IP>:8443`（现在是 `https://192.168.31.57:8443`）。**每个 iPad 第一次用之前要装一次根证书**（见「只需要做一次」第 5 步），装好之后地址栏没有警告，语音输入才能用。没装证书也能打开（选「显示详细信息 → 访问此网站」），但麦克风用不了。
6. **自检。** 进「系统设置」：
   - 上面的总结应该是绿色的「机器人就绪」；
   - 换过 USB 口或第一次用：在「设备匹配」里确认左右腕相机没有接反（张开左手夹爪，看动的是「左腕」那一格）。
7. **上力矩。** 顶栏的「力矩」开关打开。之后就可以用键盘控制、物体抓取、抓取摆放了。任何时候按 **Esc / 空格** 或顶栏红色「停止运动」都能停下。

第一次点选目标会慢一些（约 20 秒，在加载识别模型），之后每次 1–5 秒。

## 关机

1. 顶栏「力矩」开关关闭：手臂和头先以 20°/s 走回最近的停靠位（休息位或抓取位），停稳后断力矩。**不要直接拔电**，没有力矩的手臂会掉到桌上。
2. 在 WSL 终端里：

   ```bash
   cd ~/workspace/services/xlerobot-grasp
   bash launch/stop.sh --all
   ```

   停掉网页、机器人进程和 ROS bringup（`--all` 会再确认一遍手臂回到休息位、力矩全关）。不加 `--all` 只停网页。
3. 关机器人电源。

## USB 设备：怎么从 Windows 连进 WSL

机器人的 5 个 USB 设备插在 Windows 上，WSL 看不到它们。要用 **usbipd-win** 把它们一个个转给 WSL，分两步：

| 步骤 | 做什么 | 要不要管理员 | 多久做一次 | 谁来做 |
|---|---|---|---|---|
| 共享（bind） | 允许这个设备转给 WSL | 要 | 一次就够（重启后仍然有效；换了 USB 口要重做） | 你：`launch/attach_usb.ps1` |
| 挂载（attach） | 把设备从 Windows 拿走，接到 WSL | 不要 | 每次开机、每次重插线、每次 `wsl --shutdown` 之后 | `app/main.py` 启动时自动做 |

挂载后这个设备在 Windows 上就用不了了（比如 Windows 的相机应用看不到腕部相机），直到解除挂载、重插或者 WSL 关掉。

### 这 5 个设备分别是什么

| 设备 | VID:PID | `usbipd list` 里的名字 | 在 WSL 里是 | 管什么 |
|---|---|---|---|---|
| 电机总线 1 | `1a86:55d3` | USB-Enhanced-SERIAL CH343（COM5） | `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0157459-if00` | 左臂（电机 ID 1–6）+ 头（ID 7–8） |
| 电机总线 2 | `1a86:55d3` | USB-Enhanced-SERIAL CH343（COM4） | `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0182377-if00` | 右臂（ID 1–6）+ 底盘轮子（ID 7–9） |
| 左腕相机 | `0c45:64ab` | DSJ-2062-309 | `/dev/v4l/by-path/platform-vhci_hcd.0-usb-0:3:1.0-video-index0` | 左手腕上的相机 |
| 右腕相机 | `0c45:64ab` | DSJ-2062-309 | `/dev/v4l/by-path/platform-vhci_hcd.0-usb-0:4:1.0-video-index0` | 右手腕上的相机 |
| 头部相机 | `8086:0ad3` | Intel(R) RealSense(TM) Depth Camera 415 | 由 librealsense 按序列号找到（USB 3 总线上） | 头部彩色、深度、红外 |

- **BUSID**（`usbipd list` 第一列，比如 `4-1`）是 Windows 上的「控制器-口」编号，线插到别的口就会变。所以脚本按 VID:PID 找设备，不按 BUSID。
- **两条电机总线**各有序列号（`5AB0157459`、`5AB0182377`），程序按序列号打开，插哪个口、先挂哪个都没关系。
- **两个腕部相机**型号相同、**没有序列号**，只能按它们在 WSL 里分到的口区分：WSL 按挂载顺序给设备编口号。`app/main.py` 固定按「电机总线 → 腕部相机 → RealSense」的顺序挂，于是电机总线占 1、2 号口，腕部相机占 3、4 号口：Windows 上 BUSID 小的那个（现在是 `4-3`）是 3 号口 = 左腕，另一个（`4-4`）是 4 号口 = 右腕。这个对应关系记在 `config/devices.json` 里。
  - 所以：**腕部相机的线不要换 Windows 上的口**；也不要手动先挂某个设备再启动程序（口号会错开）。
  - 画面左右反了：进「系统设置 → 设备匹配」点「交换左右腕」，或者在 Windows 上把所有机器人设备解除挂载后重新运行 `app/main.py`。
- **RealSense** 一定要插 USB 3 口（蓝色）。它最后挂；偶尔报 `Set Address Failed` 挂不上，拔下线重插再启动一次。

### 设备的三种状态

`usbipd list` 最后一列：

| 状态 | 意思 | 怎么办 |
|---|---|---|
| `Not shared` | 还没共享 | 管理员 PowerShell 运行一次 `launch/attach_usb.ps1`（或 `usbipd bind --busid <BUSID>`） |
| `Shared` | 已共享，还在 Windows 上 | 正常：`app/main.py` 启动时会挂过来（或手动 `usbipd attach --wsl --busid <BUSID>`） |
| `Attached` | 已经在 WSL 里了 | 正常 |

列表里没有这个设备：线没插好，或者机器人没上电。

### 常用命令

在 Windows PowerShell 里（WSL 里也能用，把 `usbipd` 换成 `"/mnt/c/Program Files/usbipd-win/usbipd.exe"`）：

```powershell
usbipd list                              # 看所有 USB 设备和状态
usbipd bind --busid 4-1                  # 共享一个设备（管理员；一次就够）
usbipd attach --wsl --busid 4-1          # 挂进 WSL（不需要管理员）
usbipd detach --busid 4-1                # 从 WSL 拿回 Windows
usbipd detach --all                      # 全部拿回
```

`usbipd bind` 如果提示 `Unknown USB filter 'nxusbf' may be incompatible`，一般可以忽略；真的共享不了时改用 `usbipd bind --force --busid <BUSID>`，然后重启 Windows。

在 WSL 里：

```bash
cd ~/workspace/services/xlerobot-grasp
.venv/bin/python util/usbip_helper.py    # （进了环境也可以写 python util/usbip_helper.py）列出机器人的设备，把已共享、还没挂的挂进来（就是 app/main.py 启动时做的）
ls -l /dev/serial/by-id                  # 应该有两行：…5AB0157459… 和 …5AB0182377…
ls /dev/v4l/by-path | grep video-index0  # 应该有 usb-0:3:1.0 和 usb-0:4:1.0（两个腕部相机）
for d in /sys/bus/usb/devices/*; do [ -f $d/idVendor ] && echo "$(cat $d/idVendor):$(cat $d/idProduct) $(cat $d/product)"; done
                                         # WSL 里看到的所有 USB 设备：两个 1a86:55d3、两个 0c45:64ab、一个 8086:0ad3
```

启动时的挂载结果也会写在 `app/main.py` 的输出里：每个设备一行 `USB 已挂进 WSL：…`，有问题时是 `USB：…` 加上处理办法。

## 出问题时

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动日志里有 `USB：… 还没共享` | 这台电脑上这个设备从没共享过 | 按「只需要做一次」第 2 步运行一次 `attach_usb.ps1` |
| 启动日志里有 `USB：… Windows 上只看到 0/2 个` | 线没插好，或机器人没上电 | 检查线和电源，再重新启动（见下面「重新启动」） |
| `log/robot_stdout.log` 里有 `could not open port /dev/serial/by-id/…` | 电机总线没挂进 WSL | 同上两条；在 WSL 里 `ls /dev/serial/by-id` 应该有 2 行 |
| 左右腕相机的画面反了 | 腕部相机换了 Windows 上的口，或挂载顺序乱了 | 「系统设置 → 设备匹配」点「交换左右腕」；见「USB 设备」一节的腕部相机说明 |
| 某个设备在 Windows 上用不了了（比如电脑相机应用里看不到） | 它已经挂进了 WSL | 正常；要还给 Windows 就 `usbipd detach --busid <BUSID>` |
| 头部画面一直「等待头部画面」，或 RealSense 挂载报 `Set Address Failed` | RealSense 偶尔挂不上 | 拔下 RealSense 的线再插上，再重新启动（见下面「重新启动」） |
| 上力矩时提示「要从休息位或抓取位开始」 | 有关节离停靠位超过 8° | 力矩关着时用手把提示里的关节摆回去，再上力矩 |
| 网页打不开 | 网页服务没在运行 | 重新启动（见下面「重新启动」），看终端输出 |
| iPad 打不开 | Windows 防火墙挡住了 | 按「只需要做一次」第 4 步放行端口 |
| iPad 上「● 语音」说用不了麦克风 | 没用 https 地址，或根证书没装 / 没打开完全信任 | 按「只需要做一次」第 5 步；第 3 小步（证书信任设置）最容易漏 |
| 机器人进程突然没响应 | 内存不够（WSL 只有 15 GB，网页服务自己约 7 GB） | 不要另外再开跑模型的程序；`app/main.py` 会在约 15 秒内自动重启机器人进程 |

日志都在 `log/`：

- `main_stdout.log`：网页和抓取服务（用 `launch/start.sh` 启动时）；
- `robot_stdout.log`、`robot_<日期>.log`：机器人进程；
- `grasp_<日期>.log`：抓取流程；
- `bringup.log`：ROS bringup。

「系统设置」页的自检会把服务、相机、关节、模型、USB 设备逐项列出来，并写明怎么处理。

### 重新启动

先停掉正在运行的，再按「每次开机」第 4 步启动：

- **只重启网页和抓取服务**（改了网页或抓取的代码、网页打不开）：
  - 前台启动的：在那个终端按 Ctrl+C，等它退出，再输入 `python app/main.py`（还在 `(xlerobot-grasp)` 环境里，不用重新 `source`）；
  - 用 `launch/start.sh` 在后台启动的，或者不知道是怎么启动的：`bash launch/stop.sh`，然后按第 4 步。

  机器人进程不会停：已经上了力矩的手臂保持在原位，新启动的 `app/main.py` 会接着用它。
- **全部重启，包括机器人进程**（重插过 USB 线、换过电机或相机、机器人进程报错）：`bash launch/stop.sh --all`（手臂先回停靠位、断力矩，再停掉所有进程），然后按第 4 步。

## 只需要做一次（新电脑或重装后）

1. **安装 usbipd-win**（Windows）：<https://github.com/dorssel/usbipd-win/releases>。
2. **共享 USB 设备**：机器人上电、5 根线插好后，在 Windows 的**管理员** PowerShell 里运行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\rfouyang\workspace\services\xlerobot-grasp\launch\attach_usb.ps1
   ```

   它按 VID:PID 找到 5 个设备，逐个共享（bind）并挂进 WSL（attach）。共享在重启后仍然有效，以后每次开机由 `app/main.py` 自动挂载。换了 USB 口时，再运行一次。每个设备是什么、怎么检查，见「USB 设备：怎么从 Windows 连进 WSL」。
3. **WSL 网络**：`C:\Users\<用户名>\.wslconfig` 里要有：

   ```ini
   [wsl2]
   networkingMode=mirrored

   [experimental]
   hostAddressLoopback=true
   ```

   改完在 PowerShell 里 `wsl --shutdown`，再重新打开 Ubuntu。
4. **局域网访问（iPad）**：在管理员 PowerShell 里放行端口：

   ```powershell
   New-NetFirewallRule -DisplayName "XLeRobot 8010 8443" -Direction Inbound -Protocol TCP -LocalPort 8010,8443 -Action Allow
   New-NetFirewallHyperVRule -Name XLeRobot -DisplayName XLeRobot -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8010,8443 -Action Allow
   ```

   最好在路由器里给这台电脑固定 IP，iPad 的地址就不会变。
5. **每个 iPad 装一次根证书（要用语音就必须装）**：Safari 只在受信任的 https 页面上给麦克风。`app/main.py` 第一次启动时会在 `config/tls/` 里生成这台电脑自己的根证书 `ca.pem`（名字是 XLeRobot Local Root CA，10 年有效），网页的 https 证书由它签发。每个 iPad 做一次：
   1. iPad 连上同一个局域网，用 **Safari** 打开 `http://192.168.31.57:8010/ca.crt`（必须是 Safari；把 IP 换成这台电脑的），弹出「此网站正尝试下载一个配置描述文件」时点「允许」；
   2. 打开 iPad 的「设置 → 通用 → VPN 与设备管理」，点「XLeRobot Local Root CA」→「安装」，输入 iPad 密码，再点「安装」；
   3. 打开「设置 → 通用 → 关于本机 → 证书信任设置」，把「XLeRobot Local Root CA」的开关打开（**这一步最容易漏**，不打开就不算信任）；
   4. 用 Safari 打开 `https://192.168.31.57:8443`：地址栏没有「不安全」的警告就成功了，「物体抓取」页的「● 语音」可以用了（第一次会问能不能用麦克风，选「允许」）。

   这台电脑换了 IP（换网络）时，网页证书会用同一个根证书自动重签，iPad 不用重装。只有删掉了 `config/tls/ca.pem`（或换了电脑）才要在每个 iPad 上删掉旧证书、按上面重装一次。`config/tls/` 里的 `ca_key.pem` 是根证书的私钥，不要发给别人，也不要提交到 git（已在 `.gitignore` 里）。
6. **WSL 里的环境**（在项目目录下）：

   ```bash
   # ROS 2 Jazzy 和 Zenoh 路由（systemd 服务 rmw-zenohd，要开机自启）；systemd-timesyncd 要关掉（它会让 WSL 时间跳变）
   uv venv --python /usr/bin/python3.12 --system-site-packages   # 主环境：网页、感知、规划（用到系统的 rclpy / moveit_py）
   uv sync
   (cd robot_env && uv sync)                                      # 机器人进程的环境：lerobot 和主环境的依赖冲突，单独一个
   (cd ros_ws && colcon build --symlink-install)
   ```

7. **模型和密钥**：
   - 模型权重放在 `model/`：`yoloe/`、`sam3/`、`fast-foundationstereo/`、`graspgenx/`；
   - 语音（BytePlus）和 GPT（OpenAI）的 key 放在 `.env`，不要提交到 git。

## 代码在哪

```
app/         入口：main.py（只运行这一个）、robot_main.py（机器人进程，main.py 会拉起它）、接口 api/、网页 ui/
component/   功能：robot（电机、相机、键盘）、perception（识别）、planning（抓取规划、桌面守卫）、task（任务流程）、
             calibration（标定）、system_check（自检、进程守护）、voice（语音对话）
config/      所有配置（settings.py 路径和端口，robot.py、perception.py、planning.py 等按领域分）
util/        通用工具（模型、相机、usbipd、TLS 证书……）
launch/      start.sh / stop.sh、attach_usb.ps1
doc/         current_state.md（现状和决定）、refactor_plan.md（结构和验收清单）
```

界面设计在 Penpot 文件「XLerobot Grasp」里，代码要和设计一致：改界面先改 Penpot。
