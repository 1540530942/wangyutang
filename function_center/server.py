"""
TurboPi 底层直接控制 Web Server
在树莓派上运行：python3 server.py
浏览器打开：http://raspberrypi:8088

修复要点：
- 驱动通过 edge_ros_controller(127.0.0.1:8765)持久 rclpy 节点——无 subprocess 开销，不崩溃
- 后台线程持续驱动，pointerdown 一次，pointerup 一次停止
- stop_publish_times=0 消除 burst 之间的微停顿，运动更平滑
- subprocess 操作（motor/servo/rgb/sonar）加 Semaphore 防并发堆积
- 在线状态每 4s 实时检测，同时检查 turbopi container 和 edge_ros_controller
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 8088
TURBOPI = "turbopi"
ROS_SETUP = (
    "source /opt/ros/humble/setup.bash && "
    "source /home/ubuntu/ros2_ws/install/setup.bash"
)
EDGE_CONTROLLER = "http://127.0.0.1:8765"

_log: list[str] = []
_log_lock = threading.Lock()


def _log_append(line: str) -> None:
    with _log_lock:
        _log.append(line)
        del _log[:-200]


# ── Edge ROS Controller 客户端（持久 rclpy 节点）───────────────────────────────

def edge_execute(action: str, settings: dict[str, Any] | None = None) -> tuple[bool, str]:
    """POST 到 edge_ros_controller，通过持久 rclpy Node 发布——无 subprocess 开销。"""
    payload = json.dumps({"action": action, "settings": settings or {}}).encode()
    try:
        req = urllib.request.Request(
            f"{EDGE_CONTROLLER}/execute",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        ok = bool(result.get("ok"))
        out = str(result.get("output", ""))
        _log_append(f"[{'OK' if ok else 'ERR'}] edge→{action}  {out[:120]}")
        return ok, out
    except urllib.error.URLError as e:
        msg = f"edge_ros_controller 不可达: {e.reason}"
        _log_append(f"[ERR] {msg}")
        return False, msg
    except Exception as e:
        msg = f"edge_execute {type(e).__name__}: {e}"
        _log_append(f"[ERR] {msg}")
        return False, msg


def edge_health() -> dict[str, Any]:
    """GET /health from edge_ros_controller."""
    try:
        with urllib.request.urlopen(f"{EDGE_CONTROLLER}/health", timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ── 驱动后台线程（根治并发 subprocess 崩溃）───────────────────────────────────

_drive_skill: str = ""
_drive_settings: dict[str, Any] = {}
_drive_stop_event = threading.Event()
_drive_lock = threading.Lock()
_drive_thread: threading.Thread | None = None


def _drive_loop() -> None:
    """持续驱动线程：反复调用 edge_ros_controller 同一技能直到 stop_event 置位。
    unit_distance_cm=1 → 最短 burst(min 180ms)
    stop_publish_times=0 → burst 间无停止指令，运动连续平滑
    emergency_stop 会在 50ms 内中断 burst（stop_event 机制）。
    """
    skill = _drive_skill
    settings = dict(_drive_settings)
    while not _drive_stop_event.is_set():
        ok, _ = edge_execute(skill, settings)
        if not ok:
            time.sleep(0.2)  # 失败时避免空转


def do_drive_start(skill_id: str, sensitivity: float = 1.0) -> dict[str, Any]:
    """启动持续驱动（仅发一次，pointerdown 触发）。"""
    global _drive_thread, _drive_skill, _drive_settings
    with _drive_lock:
        # 先停掉之前的驱动
        _drive_stop_event.set()
        edge_execute("emergency_stop")
        if _drive_thread and _drive_thread.is_alive():
            _drive_thread.join(timeout=1.0)
        _drive_stop_event.clear()

        _drive_skill = skill_id
        _drive_settings = {
            "unit_distance_cm": 1.0,       # 最短 burst(min 180ms)
            "sensitivity": max(0.2, min(2.0, float(sensitivity))),
            "stop_publish_times": 0,        # burst 间不发停止，运动平滑
        }
        _drive_thread = threading.Thread(target=_drive_loop, daemon=True, name="drive_loop")
        _drive_thread.start()

    _log_append(f"[INFO] drive_start {skill_id} s={sensitivity:.2f}")
    return {"ok": True, "skill_id": skill_id}


def do_drive_stop() -> dict[str, Any]:
    """停止持续驱动（pointerup/离开 触发）。"""
    _drive_stop_event.set()
    ok, out = edge_execute("emergency_stop")
    _log_append("[INFO] drive_stop → emergency_stop")
    return {"ok": ok, "output": out}


# ── Subprocess 操作（Semaphore(1) 防并发堆积）─────────────────────────────────

_exec_sem = threading.Semaphore(1)


def ros_exec(cmd: str, timeout: int = 8) -> tuple[bool, str]:
    """docker exec 执行 ROS2 命令，同时只允许 1 个，防资源耗尽。"""
    if not _exec_sem.acquire(blocking=True, timeout=5.0):
        msg = "busy: 上一条操作执行中，请稍后"
        _log_append(f"[BUSY] {cmd[:80]}")
        return False, msg
    try:
        full = f"{ROS_SETUP} && {cmd}"
        r = subprocess.run(
            ["docker", "exec", "-u", "ubuntu", TURBOPI, "bash", "-lc", full],
            text=True, capture_output=True, timeout=timeout,
        )
        out = (r.stdout + r.stderr).strip()[:500]
        ok = r.returncode == 0
        _log_append(f"[{'OK' if ok else 'ERR'}] {cmd[:120]}\n  {out[:120]}")
        return ok, out
    except subprocess.TimeoutExpired:
        _log_append(f"[TIMEOUT] {cmd[:80]}")
        return False, "超时"
    except OSError as e:
        _log_append(f"[OSError] {e}")
        return False, str(e)
    finally:
        _exec_sem.release()


def pub_once(topic: str, msg_type: str, msg: str) -> tuple[bool, str]:
    cmd = (f"ros2 topic pub --once --wait-matching-subscriptions 0 "
           f"{topic} {msg_type} '{msg}'")
    return ros_exec(cmd)


# ── 原子操作 ──────────────────────────────────────────────────────────────────

def do_stop() -> dict[str, Any]:
    _drive_stop_event.set()
    ok, out = edge_execute("emergency_stop")
    return {"ok": ok, "output": out}


def do_motors(speeds: list[dict[str, Any]]) -> dict[str, Any]:
    """直接控制四电机，绕过 /cmd_vel 和 mecanum 节点。
    物理符号(mecanum.py): −M1 · M3 · −M2 · M4
    """
    data = ", ".join(
        f"{{id: {int(s['id'])}, speed: {float(s['speed']):.1f}}}"
        for s in speeds
    )
    ok, out = pub_once(
        "/ros_robot_controller/set_motor_speeds",
        "ros_robot_controller_msgs/msg/MotorsSpeedControl",
        f"{{data: [{data}]}}",
    )
    return {"ok": ok, "output": out}


def do_servo(servo_id: int, position: int, duration: float) -> dict[str, Any]:
    """精确设置 PWM 舵机（500-2500，中心 1500）。
    id=1 → 俯仰 Tilt，id=2 → 水平 Pan
    """
    pos = max(500, min(2500, position))
    msg = (f"{{duration: {duration:.2f}, "
           f"state: [{{id: [{servo_id}], position: [{pos}], offset: []}}]}}")
    ok, out = pub_once(
        "/ros_robot_controller/pwm_servo/set_state",
        "ros_robot_controller_msgs/msg/SetPWMServoState",
        msg,
    )
    return {"ok": ok, "output": out}


def do_rgb(r: int, g: int, b: int) -> dict[str, Any]:
    r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
    rgb_msg = (f"{{states: ["
               f"{{index: 1, red: {r}, green: {g}, blue: {b}}}, "
               f"{{index: 2, red: {r}, green: {g}, blue: {b}}}]}}")
    ok1, o1 = pub_once("/ros_robot_controller/set_rgb",
                       "ros_robot_controller_msgs/msg/RGBStates", rgb_msg)
    code = (f"from sdk.sonar import Sonar; s=Sonar(); s.setRGBMode(0); "
            f"s.setPixelColor(0,({r},{g},{b})); s.setPixelColor(1,({r},{g},{b}))")
    ok2, o2 = ros_exec(f'python3 -c "{code}"', timeout=4)
    return {"ok": ok1, "output": "\n".join(x for x in [o1, o2] if x)}


def do_sonar() -> dict[str, Any]:
    code = (
        "import time;"
        "from sdk.sonar import Sonar; s=Sonar(); vals=[];"
        "[vals.append(s.getDistance()) or time.sleep(0.04) for _ in range(5)];"
        "vals=[v for v in vals if 0<v<=5000];"
        "print(round(min(vals)/10.0,1) if vals else -1)"
    )
    ok, out = ros_exec(f'python3 -c "{code}"', timeout=6)
    try:
        dist = float(out.strip().splitlines()[-1])
        return {"ok": True, "distance_cm": dist}
    except (ValueError, IndexError):
        return {"ok": False, "distance_cm": -1.0, "error": out}


def do_health() -> dict[str, Any]:
    # 检查 turbopi 容器
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", TURBOPI],
            text=True, capture_output=True, timeout=3,
        )
        container_status = r.stdout.strip()
        container_ok = container_status == "running"
    except OSError as e:
        container_status = str(e)
        container_ok = False

    # 检查 edge_ros_controller
    edge_info = edge_health()
    edge_ok = edge_info.get("status") == "ok"

    return {
        "ok": container_ok and edge_ok,
        "container": container_status,
        "edge_ok": edge_ok,
        "edge_last_action": edge_info.get("last_action", ""),
    }


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._respond(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
        elif path == "/api/sonar":
            self._json(do_sonar())
        elif path == "/api/health":
            self._json(do_health())
        elif path == "/api/log":
            with _log_lock:
                self._json({"lines": list(_log)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        try:
            payload: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        path = self.path.split("?")[0]
        if path == "/api/stop":
            self._json(do_stop())
        elif path == "/api/drive_start":
            self._json(do_drive_start(
                str(payload.get("skill_id", "move_forward")),
                float(payload.get("sensitivity", 1.0)),
            ))
        elif path == "/api/drive_stop":
            self._json(do_drive_stop())
        elif path == "/api/motors":
            self._json(do_motors(payload.get("speeds", [])))
        elif path == "/api/servo":
            self._json(do_servo(
                int(payload.get("id", 1)),
                int(payload.get("pos", 1500)),
                float(payload.get("dur", 0.35)),
            ))
        elif path == "/api/rgb":
            self._json(do_rgb(
                int(payload.get("r", 0)),
                int(payload.get("g", 0)),
                int(payload.get("b", 0)),
            ))
        else:
            self._json({"error": "not found"}, 404)

    def _json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._respond(status, "application/json; charset=utf-8", body)

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # 静默 access log，重要信息写 _log


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>TurboPi 底层控制</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0a0f1e;--card:#111827;--border:#1f2937;
  --text:#f1f5f9;--muted:#6b7280;
  --blue:#3b82f6;--red:#ef4444;--green:#10b981;--amber:#f59e0b;
  --btn-h:64px;
}
html,body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;font-size:14px}
body{padding:12px;max-width:700px;margin:0 auto}
h2{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:12px}

.header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.header h1{font-size:16px;font-weight:700;flex:1}
.status-chip{display:flex;align-items:center;gap:6px;
  background:var(--card);border:1px solid var(--border);border-radius:20px;
  padding:4px 12px;font-size:11px;cursor:pointer;transition:border-color .2s}
.status-chip:hover{border-color:var(--blue)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--red);
  transition:background .4s;flex-shrink:0}
.dot.ok{background:var(--green)}.dot.warn{background:var(--amber)}
.dot.pulse{animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

.estop{width:100%;height:56px;background:var(--red);color:#fff;font-size:17px;
  font-weight:800;border:none;border-radius:10px;cursor:pointer;letter-spacing:.05em;
  transition:transform .1s,background .2s}
.estop:active{transform:scale(.97);background:#b91c1c}
.status-bar{font-size:11px;text-align:center;margin-top:6px;min-height:16px;color:var(--muted)}

.dpad-wrap{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap}
.dpad{display:grid;grid-template-columns:repeat(3,var(--btn-h));grid-template-rows:repeat(3,var(--btn-h));gap:6px}
.dbtn{width:var(--btn-h);height:var(--btn-h);border:1px solid var(--border);background:#1a2235;
  color:var(--text);font-size:24px;border-radius:10px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  user-select:none;touch-action:none;-webkit-user-select:none;
  transition:background .1s,transform .1s}
.dbtn.active{background:var(--blue);border-color:var(--blue);transform:scale(.93)}
.dbtn.stop-center{font-size:20px;color:var(--muted)}
.dbtn.empty{background:transparent;border:none;pointer-events:none}
.drive-info{flex:1;min-width:120px;font-size:12px;color:var(--muted);line-height:1.9}
.drive-info b{color:var(--text);display:block;margin-bottom:6px;font-size:13px}
.drive-tag{display:inline-block;background:#064e3b;color:#34d399;
  border-radius:6px;padding:2px 8px;font-size:10px;font-weight:700;margin-top:6px}

.srow{margin-bottom:14px}
.srow label{display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px}
.sval{font-weight:700;color:var(--blue);font-variant-numeric:tabular-nums}
input[type=range]{width:100%;accent-color:var(--blue);cursor:pointer;height:22px}

.motor-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.motor-item label{display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;color:var(--muted)}
.mval{font-weight:700;color:var(--amber)}

.rgb-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.color-preview{width:42px;height:42px;border-radius:8px;border:2px solid var(--border);
  position:relative;cursor:pointer;overflow:hidden}
.color-preview input[type=color]{position:absolute;width:200%;height:200%;top:-50%;left:-50%;opacity:0;cursor:pointer}
.rgb-inputs{display:flex;gap:6px}
.rgb-grp{display:flex;flex-direction:column;align-items:center;gap:2px}
.rgb-grp span{font-size:10px;color:var(--muted)}
.rgb-grp input[type=number]{width:52px;background:#1a2235;border:1px solid var(--border);
  color:var(--text);border-radius:6px;padding:4px 6px;font-size:13px;text-align:center}

.sonar-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.sonar-num{font-size:34px;font-weight:800;color:var(--green);
  min-width:110px;font-variant-numeric:tabular-nums;transition:color .3s}
.sonar-num.warn{color:var(--amber)}.sonar-num.danger{color:var(--red)}

.btn{padding:8px 16px;border-radius:8px;border:1px solid var(--border);background:#1a2235;
  color:var(--text);cursor:pointer;font-size:13px;font-weight:600;
  transition:transform .1s;white-space:nowrap}
.btn:active{transform:scale(.96)}
.btn.primary{background:var(--blue);border-color:var(--blue);color:#fff}
.btn.danger{background:var(--red);border-color:var(--red);color:#fff}
.btn.success{background:var(--green);border-color:var(--green);color:#fff}
.btn.sm{padding:4px 10px;font-size:11px}
.brow{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.toggle{display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12px;color:var(--muted);white-space:nowrap}
.toggle input{accent-color:var(--blue)}

#console{background:#0d1117;border-radius:8px;padding:10px;font-family:monospace;
  font-size:11px;color:#8b949e;max-height:180px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}
.console-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
</style>
</head>
<body>

<div class="header">
  <h1>🤖 TurboPi 底层控制</h1>
  <div class="status-chip" id="chip" onclick="checkHealth()" title="点击刷新">
    <span class="dot pulse" id="dot"></span>
    <span id="dot-label">检查中…</span>
  </div>
</div>

<div class="card" style="padding:12px">
  <button class="estop" id="btn-estop">⚡ EMERGENCY STOP 紧急停止</button>
  <div class="status-bar" id="status-bar"></div>
</div>

<!-- 底盘 -->
<div class="card">
  <h2>底盘驱动 / Drive</h2>
  <div class="dpad-wrap">
    <div class="dpad">
      <button class="dbtn" id="d-tl"    title="左转">↺</button>
      <button class="dbtn" id="d-fwd"   title="前进">▲</button>
      <button class="dbtn" id="d-tr"    title="右转">↻</button>
      <button class="dbtn" id="d-left"  title="左平移">◄</button>
      <button class="dbtn stop-center" id="d-stop" title="停止">■</button>
      <button class="dbtn" id="d-right" title="右平移">►</button>
      <div class="dbtn empty"></div>
      <button class="dbtn" id="d-bwd"   title="后退">▼</button>
      <div class="dbtn empty"></div>
    </div>
    <div class="drive-info">
      <b>按住移动 · 松手即停</b>
      ▲▼ 前进/后退<br>
      ◄► 麦轮横向平移<br>
      ↺↻ 原地左/右转<br>
      <div class="drive-tag" id="drive-tag" style="display:none"></div>
    </div>
  </div>
</div>

<!-- 云台 -->
<div class="card">
  <h2>云台精确控制 / Servo PWM</h2>
  <div class="srow">
    <label>
      <span>Servo 2 · 水平 Pan</span>
      <span style="font-size:11px;color:var(--muted)">左1800 ↔ 1200右</span>
      <span class="sval" id="s2-val">1500</span>
    </label>
    <input type="range" id="s2" min="500" max="2500" step="10" value="1500">
  </div>
  <div class="srow">
    <label>
      <span>Servo 1 · 俯仰 Tilt</span>
      <span style="font-size:11px;color:var(--muted)">上1000 ↔ 1700下</span>
      <span class="sval" id="s1-val">1500</span>
    </label>
    <input type="range" id="s1" min="500" max="2500" step="10" value="1500">
  </div>
  <div class="srow">
    <label><span>移动时间</span><span class="sval" id="sdur-val">0.35 s</span></label>
    <input type="range" id="sdur" min="0.1" max="1.5" step="0.05" value="0.35">
  </div>
  <div class="brow">
    <button class="btn primary" id="btn-s2">发送 Pan</button>
    <button class="btn primary" id="btn-s1">发送 Tilt</button>
    <button class="btn" id="btn-center">居中 1500</button>
  </div>
</div>

<!-- 电机直接 -->
<div class="card">
  <h2>电机直接控制 / Motor Direct ⚙</h2>
  <p style="font-size:11px;color:var(--muted);margin-bottom:12px">
    直发 <code>/ros_robot_controller/set_motor_speeds</code>（绕过 /cmd_vel 和 mecanum 节点）<br>
    物理符号：<b>−M1 · M3 · −M2 · M4</b>
  </p>
  <div class="motor-grid">
    <div class="motor-item">
      <label>M1 前左(取反) <span class="mval" id="m1v">0</span></label>
      <input type="range" id="m1" min="-100" max="100" value="0">
    </div>
    <div class="motor-item">
      <label>M2 前右(取反) <span class="mval" id="m2v">0</span></label>
      <input type="range" id="m2" min="-100" max="100" value="0">
    </div>
    <div class="motor-item">
      <label>M3 后左 <span class="mval" id="m3v">0</span></label>
      <input type="range" id="m3" min="-100" max="100" value="0">
    </div>
    <div class="motor-item">
      <label>M4 后右 <span class="mval" id="m4v">0</span></label>
      <input type="range" id="m4" min="-100" max="100" value="0">
    </div>
  </div>
  <div class="brow">
    <button class="btn primary" id="btn-motors">发送</button>
    <button class="btn danger" id="btn-motors-stop">全部归零</button>
  </div>
</div>

<!-- 灯光 & 传感器 -->
<div class="card">
  <h2>灯光 & 传感器 / Lights & Sensors</h2>
  <div style="font-size:12px;color:var(--muted);margin-bottom:8px">RGB（机身 LED 1/2 + 声纳灯 0/1）</div>
  <div class="rgb-row">
    <div class="color-preview" id="color-preview" style="background:#0000ff">
      <input type="color" id="color-pick" value="#0000ff">
    </div>
    <div class="rgb-inputs">
      <div class="rgb-grp"><span>R</span><input type="number" id="rgb-r" min="0" max="255" value="0"></div>
      <div class="rgb-grp"><span>G</span><input type="number" id="rgb-g" min="0" max="255" value="0"></div>
      <div class="rgb-grp"><span>B</span><input type="number" id="rgb-b" min="0" max="255" value="255"></div>
    </div>
    <button class="btn success" id="btn-rgb-on">点亮</button>
    <button class="btn" id="btn-rgb-off">关灯</button>
  </div>

  <div style="font-size:12px;color:var(--muted);margin-bottom:8px">超声波（5 次采样取最小值）</div>
  <div class="sonar-row">
    <span class="sonar-num" id="sonar-num">-- cm</span>
    <button class="btn" id="btn-sonar">📡 测量</button>
    <label class="toggle"><input type="checkbox" id="sonar-auto"> 自动刷新(2s)</label>
  </div>
</div>

<!-- Console -->
<div class="card">
  <div class="console-hdr">
    <h2 style="margin:0">指令日志 / Console</h2>
    <button class="btn sm" id="btn-clear">清空</button>
  </div>
  <div id="console">等待指令…</div>
</div>

<script>
// 自动检测 API 前缀：直接访问 :8088 用 /api，通过 Caddy 代理用 /function_center/api
const API = window.location.pathname.startsWith('/function_center') ? '/function_center/api' : '/api';

async function post(path, data) {
  try {
    const r = await fetch(API + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    return await r.json();
  } catch(e) { return {ok: false, error: String(e)}; }
}
async function get(path) {
  try { return await (await fetch(API + path)).json(); }
  catch(e) { return {ok: false, error: String(e)}; }
}

function setStatus(msg, color) {
  const el = document.getElementById('status-bar');
  el.textContent = msg;
  el.style.color = color || 'var(--muted)';
}

// ── 实时在线状态（每 4s，HTTP GET /health）────────────────────────────────────
async function checkHealth() {
  const dot = document.getElementById('dot');
  const label = document.getElementById('dot-label');
  dot.className = 'dot pulse';

  const d = await get('/health');

  if (d.ok) {
    // container running + edge ok
    dot.className = 'dot ok';
    const act = d.edge_last_action ? ` · ${d.edge_last_action}` : '';
    label.textContent = '在线' + act;
  } else if (d.container === 'running' && !d.edge_ok) {
    dot.className = 'dot warn';
    label.textContent = 'edge 离线';
  } else if (d.error) {
    dot.className = 'dot';
    label.textContent = '网络错误';
  } else {
    dot.className = 'dot';
    label.textContent = d.container || '离线';
  }
}
checkHealth();
setInterval(checkHealth, 4000);

// ── 指令日志自动刷新（每 1.5s）──────────────────────────────────────────────
setInterval(async () => {
  const d = await get('/log');
  if (!d.lines || !d.lines.length) return;
  const el = document.getElementById('console');
  const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 20;
  el.textContent = d.lines.join('\n');
  if (atBottom) el.scrollTop = el.scrollHeight;
}, 1500);
document.getElementById('btn-clear').onclick = () => {
  document.getElementById('console').textContent = '';
};

// ── Emergency Stop ────────────────────────────────────────────────────────────
document.getElementById('btn-estop').addEventListener('click', async () => {
  await clearDrive();
  setStatus('急停中…', 'var(--amber)');
  const d = await post('/stop', {});
  setStatus(d.ok ? '■ 已急停' : '✗ ' + (d.error || ''), d.ok ? 'var(--green)' : 'var(--red)');
});

// ── 驱动（pointerdown → drive_start 一次，pointerup → drive_stop 一次）─────────
const SKILL_MAP = {
  fwd:'move_forward', bwd:'move_backward',
  left:'move_left',   right:'move_right',
  tl:'turn_left',     tr:'turn_right',
};
const LABEL_MAP = {
  fwd:'▲ 前进', bwd:'▼ 后退',
  left:'◄ 左平移', right:'► 右平移',
  tl:'↺ 左转', tr:'↻ 右转',
};

let activeBtn = null;
let driving = false;

async function startDrive(key, btn) {
  if (driving) await clearDrive();
  activeBtn = btn;
  btn.classList.add('active');
  driving = true;
  const tag = document.getElementById('drive-tag');
  tag.textContent = LABEL_MAP[key];
  tag.style.display = '';
  const d = await post('/drive_start', {skill_id: SKILL_MAP[key], sensitivity: 1.0});
  if (!d.ok) setStatus('✗ 驱动失败: ' + (d.error || ''), 'var(--red)');
}

async function clearDrive() {
  if (!driving && !activeBtn) return;
  driving = false;
  if (activeBtn) { activeBtn.classList.remove('active'); activeBtn = null; }
  document.getElementById('drive-tag').style.display = 'none';
  await post('/drive_stop', {});
}

['fwd','bwd','left','right','tl','tr'].forEach(key => {
  const btn = document.getElementById('d-' + key);
  btn.addEventListener('pointerdown', e => {
    e.preventDefault();
    btn.setPointerCapture(e.pointerId);
    startDrive(key, btn);
  });
  btn.addEventListener('pointerup',     clearDrive);
  btn.addEventListener('pointercancel', clearDrive);
  btn.addEventListener('pointerleave',  () => { if (driving && activeBtn === btn) clearDrive(); });
});

document.getElementById('d-stop').addEventListener('click', async () => {
  await clearDrive();
  await post('/stop', {});
  setStatus('■ 停止', 'var(--green)');
});

// 切标签/关窗口自动停车
window.addEventListener('beforeunload', clearDrive);
document.addEventListener('visibilitychange', () => { if (document.hidden) clearDrive(); });

// ── 云台 ─────────────────────────────────────────────────────────────────────
document.getElementById('s1').oninput = function() { document.getElementById('s1-val').textContent = this.value; };
document.getElementById('s2').oninput = function() { document.getElementById('s2-val').textContent = this.value; };
document.getElementById('sdur').oninput = function() {
  document.getElementById('sdur-val').textContent = parseFloat(this.value).toFixed(2) + ' s';
};

// 拖滑杆时有 120ms 防抖实时发送
let servoTimer = {};
['s1','s2'].forEach(id => {
  const sid = id === 's1' ? 1 : 2;
  document.getElementById(id).addEventListener('input', function() {
    document.getElementById(id + '-val').textContent = this.value;
    clearTimeout(servoTimer[id]);
    const v = this.value;
    servoTimer[id] = setTimeout(async () => {
      const dur = parseFloat(document.getElementById('sdur').value);
      await post('/servo', {id: sid, pos: parseInt(v), dur});
    }, 120);
  });
});

document.getElementById('btn-s2').onclick = async () => {
  const pos = parseInt(document.getElementById('s2').value);
  const dur = parseFloat(document.getElementById('sdur').value);
  const d = await post('/servo', {id:2, pos, dur});
  setStatus(d.ok ? `✓ Pan → ${pos}` : '✗ ' + (d.error||''), d.ok?'var(--green)':'var(--red)');
};
document.getElementById('btn-s1').onclick = async () => {
  const pos = parseInt(document.getElementById('s1').value);
  const dur = parseFloat(document.getElementById('sdur').value);
  const d = await post('/servo', {id:1, pos, dur});
  setStatus(d.ok ? `✓ Tilt → ${pos}` : '✗ ' + (d.error||''), d.ok?'var(--green)':'var(--red)');
};
document.getElementById('btn-center').onclick = async () => {
  ['s1','s2'].forEach(id => {
    document.getElementById(id).value = 1500;
    document.getElementById(id + '-val').textContent = '1500';
  });
  const dur = parseFloat(document.getElementById('sdur').value);
  await post('/servo', {id:1, pos:1500, dur});
  const d = await post('/servo', {id:2, pos:1500, dur});
  setStatus(d.ok ? '✓ 已居中' : '✗', d.ok?'var(--green)':'var(--red)');
};

// ── 电机直接 ──────────────────────────────────────────────────────────────────
[1,2,3,4].forEach(i => {
  document.getElementById('m'+i).oninput = function() {
    document.getElementById('m'+i+'v').textContent = this.value;
  };
});
document.getElementById('btn-motors').onclick = async () => {
  const speeds = [1,2,3,4].map(i => ({id:i, speed: parseFloat(document.getElementById('m'+i).value)}));
  const d = await post('/motors', {speeds});
  setStatus(d.ok ? `✓ Motor [${speeds.map(s=>s.speed).join('/')}]` : '✗', d.ok?'var(--green)':'var(--red)');
};
document.getElementById('btn-motors-stop').onclick = async () => {
  [1,2,3,4].forEach(i => {
    document.getElementById('m'+i).value = 0;
    document.getElementById('m'+i+'v').textContent = '0';
  });
  const d = await post('/motors', {speeds:[1,2,3,4].map(i=>({id:i,speed:0}))});
  setStatus(d.ok ? '✓ 电机全归零' : '✗', d.ok?'var(--green)':'var(--red)');
};

// ── RGB ───────────────────────────────────────────────────────────────────────
function hexToRgb(h) {
  return {r:parseInt(h.slice(1,3),16), g:parseInt(h.slice(3,5),16), b:parseInt(h.slice(5,7),16)};
}
function rgbToHex(r,g,b) {
  return '#' + [r,g,b].map(v=>Math.max(0,Math.min(255,v)).toString(16).padStart(2,'0')).join('');
}
document.getElementById('color-pick').oninput = function() {
  const {r,g,b} = hexToRgb(this.value);
  document.getElementById('rgb-r').value = r;
  document.getElementById('rgb-g').value = g;
  document.getElementById('rgb-b').value = b;
  document.getElementById('color-preview').style.background = this.value;
};
['rgb-r','rgb-g','rgb-b'].forEach(id => {
  document.getElementById(id).oninput = () => {
    const r=parseInt(document.getElementById('rgb-r').value)||0;
    const g=parseInt(document.getElementById('rgb-g').value)||0;
    const b=parseInt(document.getElementById('rgb-b').value)||0;
    const hex = rgbToHex(r,g,b);
    document.getElementById('color-pick').value = hex;
    document.getElementById('color-preview').style.background = hex;
  };
});
document.getElementById('btn-rgb-on').onclick = async () => {
  const r=parseInt(document.getElementById('rgb-r').value)||0;
  const g=parseInt(document.getElementById('rgb-g').value)||0;
  const b=parseInt(document.getElementById('rgb-b').value)||0;
  const d = await post('/rgb', {r,g,b});
  setStatus(d.ok ? `✓ RGB(${r},${g},${b})` : '✗', d.ok?'var(--green)':'var(--red)');
};
document.getElementById('btn-rgb-off').onclick = async () => {
  const d = await post('/rgb', {r:0,g:0,b:0});
  setStatus(d.ok ? '✓ 灯已关' : '✗', d.ok?'var(--green)':'var(--red)');
};

// ── 超声波 ────────────────────────────────────────────────────────────────────
async function measureSonar() {
  const el = document.getElementById('sonar-num');
  el.textContent = '…';
  el.className = 'sonar-num';
  const d = await get('/sonar');
  if (d.ok && d.distance_cm > 0) {
    el.textContent = d.distance_cm.toFixed(1) + ' cm';
    el.className = 'sonar-num' + (d.distance_cm<15?' danger':d.distance_cm<30?' warn':'');
    setStatus(`📡 前方 ${d.distance_cm} cm`, 'var(--green)');
  } else {
    el.textContent = '-- cm';
    setStatus('✗ 测距失败: ' + (d.error||''), 'var(--red)');
  }
}
document.getElementById('btn-sonar').onclick = measureSonar;
let sonarTimer = null;
document.getElementById('sonar-auto').onchange = function() {
  if (this.checked) { measureSonar(); sonarTimer = setInterval(measureSonar, 2000); }
  else { clearInterval(sonarTimer); sonarTimer = null; }
};
</script>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

class ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    server = ReusableServer(("0.0.0.0", PORT), Handler)
    print(f"[OK] TurboPi 底层控制  http://0.0.0.0:{PORT}")
    print(f"[OK] edge_ros_controller: {EDGE_CONTROLLER}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
