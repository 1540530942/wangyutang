const ACTION_BASE = "/action";
const CAMERA_BASE = "/camera";

const SKILLS = [
  { id: "emergency_stop", name: "急停", group: "system", type: "base_stop", definition: "零速度 Twist", executor: "edge_ros_controller.py", verify: "任务返回 complete 且 /cmd_vel 被置零；相机只能辅助观察停止状态。" },
  { id: "reset_pose", name: "复位", group: "system", type: "reset_pose", definition: "底盘停止，PWM servo 1/2 回中 1500", executor: "edge_ros_controller.py", verify: "相机画面应回到居中视角；任务输出必须来自真实边缘执行器。" },
  { id: "rgb_on", name: "RGB 开灯", group: "light", type: "rgb_light", definition: "settings.rgb_red/green/blue，默认 200/200/200", executor: "edge_ros_controller.py + Sonar SDK", verify: "若灯在相机视野内，可用前后图确认；否则只能确认任务返回和 GPIO/相机元数据。" },
  { id: "rgb_off", name: "RGB 关灯", group: "light", type: "rgb_light", definition: "RGB 全 0", executor: "edge_ros_controller.py + Sonar SDK", verify: "若灯在相机视野内，可用前后图确认变暗；否则只展示真实任务结果。" },
  { id: "front_distance", name: "前方测距", group: "sensor", type: "front_distance", definition: "Sonar().getDistance() 采样 7 次，过滤后取最小值", executor: "edge_ros_controller.py", verify: "以任务 output 中的 front_distance_estimate_cm、raw_mm_samples、confidence 为准；相机不能证明绝对距离精度。" },
  { id: "look_left", name: "向左看", group: "servo", type: "camera_servo", definition: "servo 2, delta +100, 1200..1800", executor: "edge_ros_controller.py", verify: "相机前后画面的水平视角应发生变化。" },
  { id: "look_right", name: "向右看", group: "servo", type: "camera_servo", definition: "servo 2, delta -100, 1200..1800", executor: "edge_ros_controller.py", verify: "相机前后画面的水平视角应发生变化。" },
  { id: "look_up", name: "向上看", group: "servo", type: "camera_servo", definition: "servo 1, delta -100, 1000..1700", executor: "edge_ros_controller.py", verify: "相机前后画面的垂直视角应发生变化。" },
  { id: "look_down", name: "向下看", group: "servo", type: "camera_servo", definition: "servo 1, delta +100, 1000..1700", executor: "edge_ros_controller.py", verify: "相机前后画面的垂直视角应发生变化。" },
  { id: "move_forward", name: "前进", group: "motion", type: "base_move", twist: { x: 0.35, y: 0, z: 0 }, definition: "linear_x=0.35", executor: "edge_ros_controller.py", verify: "安全验证使用 1cm；相机前后图应体现底盘相对环境的轻微前移。" },
  { id: "move_backward", name: "后退", group: "motion", type: "base_move", twist: { x: -0.35, y: 0, z: 0 }, definition: "linear_x=-0.35", executor: "edge_ros_controller.py", verify: "安全验证使用 1cm；相机前后图应体现底盘轻微后移。" },
  { id: "move_left", name: "左平移", group: "motion", type: "base_move", twist: { x: 0, y: 0.35, z: 0 }, definition: "linear_y=0.35", executor: "edge_ros_controller.py", verify: "安全验证使用 1cm；相机前后图应体现横向位移。" },
  { id: "move_right", name: "右平移", group: "motion", type: "base_move", twist: { x: 0, y: -0.45, z: 0 }, definition: "linear_y=-0.45", executor: "edge_ros_controller.py", verify: "安全验证使用 1cm；相机前后图应体现横向位移。" },
  { id: "turn_left", name: "左转", group: "motion", type: "base_turn", twist: { x: 0, y: 0, z: 5.0 }, definition: "angular_z=5.0", executor: "edge_ros_controller.py", verify: "安全验证使用 1deg；相机前后图应体现视角轻微左转。" },
  { id: "turn_right", name: "右转", group: "motion", type: "base_turn", twist: { x: 0, y: 0, z: -5.0 }, definition: "angular_z=-5.0", executor: "edge_ros_controller.py", verify: "安全验证使用 1deg；相机前后图应体现视角轻微右转。" },
  { id: "rotate_in_place", name: "原地旋转", group: "motion", type: "base_turn", twist: { x: 0, y: 0, z: 5.0 }, motor: "M1=35, M2=35, M3=35, M4=35, 2000ms", definition: "angular_z=5.0 + motor_override", executor: "edge_ros_controller.py 或 action_move_executor.py fallback", verify: "安全验证使用 1deg；若走 motor_override，需观察相机视角明显旋转。" },
  { id: "move_diagonal_forward_left", name: "左前斜移", group: "motion", type: "base_move", twist: { x: 0.25, y: 0.25, z: 0 }, motor: "M1=0, M2=50, M3=-50, M4=0, 300ms", definition: "linear_x=0.25, linear_y=0.25 + motor_override", executor: "edge_ros_controller.py 或 action_move_executor.py fallback", verify: "安全验证使用 1cm；相机前后图应体现左前方向位移。" },
  { id: "move_diagonal_forward_right", name: "右前斜移", group: "motion", type: "base_move", twist: { x: 0.25, y: -0.25, z: 0 }, motor: "M1=-50, M2=0, M3=0, M4=50, 300ms", definition: "linear_x=0.25, linear_y=-0.25 + motor_override", executor: "edge_ros_controller.py 或 action_move_executor.py fallback", verify: "安全验证使用 1cm；相机前后图应体现右前方向位移。" },
  { id: "camera_snapshot", name: "拍照", group: "sensor", type: "camera_snapshot", definition: "相机抓拍任务，读取 /image_raw 到 JPEG", executor: "action_move_executor.py -> camera_snapshot 服务", verify: "以 /camera/api/latest 元数据和 latest.jpg 的 frame_id/task_id/updated_at 为准；edge_ros_controller 当前可能返回 camera_snapshot unsupported。" },
  { id: "remote_shutdown", name: "远程关机", group: "system", type: "system_shutdown", definition: "sudo shutdown -h now，需要 verification_code=123", executor: "edge_action_poller.py / action_move_executor.py", verify: "破坏性技能，页面默认不执行；只能展示指令和安全边界。" },
  { id: "speak", name: "说话", group: "system", type: "speak", definition: "params.text（≤200字）经 /common/api/tts/speech 合成 WAV，再由 aplay 在本机音箱播报；可选 params.voice / params.instructions", executor: "edge_action_poller.py（fetch_tts_audio + aplay），不经 edge_ros_controller /execute", verify: "以任务 output 的 speak_played chars/device/voice 为准；需现场听到播报，相机无法验证音频。" },
];

let selectedSkill = SKILLS.find((item) => item.id === "move_forward");

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function cloudCommand(skill) {
  if (skill.id === "speak") {
    return `curl -X POST https://www.wangyutang.cn/action/api/tasks \\
  -H "Content-Type: application/json" \\
  -d '{"action":"speak","params":{"text":"你好，我是机器人"},"source":"manual"}'`;
  }
  const payload = skill.id === "remote_shutdown"
    ? `{"action":"${skill.id}","verification_code":"123","source":"manual"}`
    : `{"action":"${skill.id}","source":"manual"}`;
  return `curl -X POST https://www.wangyutang.cn/action/api/tasks \\
  -H "Content-Type: application/json" \\
  -d '${payload}'`;
}

function localCommand(skill) {
  if (skill.id === "speak") {
    return `# speak 不走 edge_ros_controller /execute；由 edge_action_poller 取到任务后本机合成播报
curl -X POST https://www.wangyutang.cn/common/api/tts/speech \\
  -H "Content-Type: application/json" \\
  -d '{"model":"qwen3-tts-12hz-1.7b-customvoice","input":"你好","voice":"vivian","language":"chinese","response_format":"wav"}' \\
  --output speak.wav
aplay speak.wav`;
  }
  return `curl -X POST http://127.0.0.1:8765/execute \\
  -H "Content-Type: application/json" \\
  -d '{"action":"${skill.id}"}'`;
}

function stopTwistCommand() {
  return `ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist \\
  "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}"`;
}

function rosCommand(skill) {
  if (skill.id === "speak") {
    return `# 无 ROS 话题；最底层就是把合成好的 WAV 交给 ALSA 播放\naplay -D plughw:1,0 speak.wav`;
  }
  if (skill.twist) {
    return `ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \\
  "{linear: {x: ${skill.twist.x}, y: ${skill.twist.y}, z: 0.0}, angular: {x: 0.0, y: 0.0, z: ${skill.twist.z}}}"

停止：

${stopTwistCommand()}`;
  }
  const servoExamples = {
    look_left: [2, 1600],
    look_right: [2, 1400],
    look_up: [1, 1400],
    look_down: [1, 1600],
    reset_pose: null,
  };
  if (skill.id === "reset_pose") {
    return `${stopTwistCommand()}

ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \\
  ros_robot_controller_msgs/msg/SetPWMServoState \\
  "{duration: 0.35, state: [{id: [1], position: [1500], offset: []}, {id: [2], position: [1500], offset: []}]}"`;
  }
  if (skill.id in servoExamples) {
    const [id, pos] = servoExamples[skill.id];
    return `ros2 topic pub --once /ros_robot_controller/pwm_servo/set_state \\
  ros_robot_controller_msgs/msg/SetPWMServoState \\
  "{duration: 0.35, state: [{id: [${id}], position: [${pos}], offset: []}]}"`;
  }
  if (skill.id === "rgb_on" || skill.id === "rgb_off") {
    const value = skill.id === "rgb_on" ? 200 : 0;
    return `ros2 topic pub --once /ros_robot_controller/set_rgb \\
  ros_robot_controller_msgs/msg/RGBStates \\
  "{states: [{index: 1, red: ${value}, green: ${value}, blue: ${value}}, {index: 2, red: ${value}, green: ${value}, blue: ${value}}]}"`;
  }
  if (skill.id === "front_distance") {
    return `python3 - <<'PY'
import time
from sdk.sonar import Sonar

sonar = Sonar()
values = []
for _ in range(7):
    value = int(sonar.getDistance())
    if 0 < value <= 5000:
        values.append(value)
    time.sleep(0.04)

raw_mm = min(values)
print(f'front_distance_estimate_cm={raw_mm / 10.0:.2f}')
print('raw_mm_samples=' + ','.join(str(v) for v in values))
print(f'confidence={len(values) / 7:.3f}')
PY`;
  }
  if (skill.id === "camera_snapshot") {
    return `curl -X POST https://www.wangyutang.cn/camera/api/capture \\
  -H "Content-Type: application/json" \\
  -d '{"kind":"camera","mode":"single","query_gpio":26}'

curl -o frame.jpg "http://127.0.0.1:8080/snapshot?topic=/image_raw"`;
  }
  if (skill.id === "remote_shutdown") {
    return "sudo shutdown -h now";
  }
  return stopTwistCommand();
}

function groupLabel(group) {
  return { motion: "底盘运动", servo: "摄像头舵机", sensor: "传感器/相机", light: "灯光", system: "停止/系统" }[group] || group;
}

function filteredSkills() {
  const keyword = $("skillSearch").value.trim().toLowerCase();
  const group = $("groupFilter").value;
  return SKILLS.filter((skill) => {
    const haystack = `${skill.id} ${skill.name} ${skill.type} ${skill.definition}`.toLowerCase();
    return (group === "all" || skill.group === group) && (!keyword || haystack.includes(keyword));
  });
}

function renderRows() {
  const rows = filteredSkills();
  $("skillCount").textContent = `${rows.length} skills`;
  $("skillRows").innerHTML = rows.map((skill) => `
    <tr class="${selectedSkill && selectedSkill.id === skill.id ? "selected" : ""}">
      <td><span class="skill-id">${escapeHtml(skill.id)}</span><span class="skill-name">${escapeHtml(skill.name)}</span></td>
      <td><span class="tag">${escapeHtml(groupLabel(skill.group))}</span><span class="tag">${escapeHtml(skill.type)}</span></td>
      <td>${escapeHtml(skill.definition)}<br><small>${escapeHtml(skill.executor)}</small></td>
      <td><pre class="mini-code">${escapeHtml(cloudCommand(skill))}</pre></td>
      <td>${escapeHtml(skill.verify)}</td>
      <td><div class="row-actions">
        <button data-detail="${skill.id}">详情</button>
        <button data-run="${skill.id}" ${skill.id === "remote_shutdown" ? "disabled" : ""}>验证</button>
      </div></td>
    </tr>
  `).join("");

  document.querySelectorAll("[data-detail]").forEach((btn) => btn.addEventListener("click", () => selectSkill(btn.dataset.detail)));
  document.querySelectorAll("[data-run]").forEach((btn) => btn.addEventListener("click", () => runSkill(btn.dataset.run)));
}

function selectSkill(skillId) {
  selectedSkill = SKILLS.find((item) => item.id === skillId) || selectedSkill;
  renderRows();
  renderDetail();
}

function renderDetail() {
  const skill = selectedSkill;
  $("detailTitle").textContent = `${skill.name} / ${skill.id}`;
  $("detailSubtitle").textContent = `${groupLabel(skill.group)} · ${skill.type}`;
  $("detailBody").classList.remove("empty-state");
  $("detailBody").innerHTML = `
    <table class="info-table">
      <tbody>
        <tr><th>原子能力定义</th><td><code>action_move/skill_catalog.json</code> 中的 <code>${escapeHtml(skill.id)}</code><br>${escapeHtml(skill.definition)}</td></tr>
        <tr><th>执行器</th><td>${escapeHtml(skill.executor)}</td></tr>
        <tr><th>验证边界</th><td>${escapeHtml(skill.verify)}</td></tr>
        ${skill.motor ? `<tr><th>motor_override</th><td>${escapeHtml(skill.motor)}</td></tr>` : ""}
      </tbody>
    </table>
    <div>
      <h2>云端任务指令</h2>
      <pre class="code-block">${escapeHtml(cloudCommand(skill))}</pre>
    </div>
    <div>
      <h2>Pi 本机执行指令</h2>
      <pre class="code-block">${escapeHtml(localCommand(skill))}</pre>
    </div>
    <div>
      <h2>最底层指令形式</h2>
      <pre class="code-block">${escapeHtml(rosCommand(skill))}</pre>
    </div>
  `;
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (error) {
    data = { raw: text };
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${JSON.stringify(data)}`);
  }
  return data;
}

async function refreshHealth() {
  try {
    const health = await apiJson(`${ACTION_BASE}/api/health`);
    const device = health.device || {};
    $("actionDot").className = `dot ${device.online ? "ok" : "bad"}`;
    $("actionStatus").textContent = device.online ? "动作服务在线" : "动作服务离线";
    $("actionDetail").textContent = `${device.device_id || "-"} ${device.status || ""}`;
  } catch (error) {
    $("actionDot").className = "dot bad";
    $("actionStatus").textContent = "动作服务异常";
    $("actionDetail").textContent = error.message;
  }

  try {
    const latest = await apiJson(`${CAMERA_BASE}/api/latest?kind=camera`);
    $("cameraDot").className = `dot ${latest.has_image ? "ok" : "bad"}`;
    $("cameraStatus").textContent = latest.has_image ? "相机有画面" : "相机无画面";
    $("cameraDetail").textContent = `${latest.capture_source || "-"} ${latest.frame_id || ""}`;
  } catch (error) {
    $("cameraDot").className = "dot bad";
    $("cameraStatus").textContent = "相机服务异常";
    $("cameraDetail").textContent = error.message;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function captureStill(label) {
  const start = Date.now() / 1000;
  const capture = await apiJson(`${CAMERA_BASE}/api/capture`, {
    method: "POST",
    body: JSON.stringify({ kind: "camera", mode: "single", query_gpio: 26 }),
  });
  const taskId = capture.task ? capture.task.id : "";
  let latest = null;
  for (let i = 0; i < 36; i += 1) {
    await sleep(1000);
    latest = await apiJson(`${CAMERA_BASE}/api/latest?kind=camera`);
    if ((taskId && latest.task_id === taskId) || Number(latest.updated_at || 0) >= start) {
      break;
    }
  }
  const src = `${CAMERA_BASE}/api/latest.jpg?kind=camera&t=${Date.now()}`;
  return { label, taskId, latest, src };
}

async function pollTask(taskId) {
  let task = null;
  for (let i = 0; i < 70; i += 1) {
    const data = await apiJson(`${ACTION_BASE}/api/tasks/${encodeURIComponent(taskId)}`);
    task = data.task;
    if (!task || !["pending", "claimed", "running"].includes(task.status)) {
      return task;
    }
    await sleep(1000);
  }
  return task;
}

function safeOverrides(skill) {
  if (!$("safeMode").checked) {
    return {};
  }
  if (skill.type === "base_move") {
    return { unit_distance_cm: 1, sensitivity: 2 };
  }
  if (skill.type === "base_turn") {
    return { turn_angle_deg: 1, sensitivity: 2 };
  }
  if (skill.id === "rgb_on") {
    return { rgb_red: 255, rgb_green: 255, rgb_blue: 255 };
  }
  return {};
}

function setFrame(prefix, capture) {
  if (!capture) {
    return;
  }
  $(`${prefix}Frame`).src = capture.src;
  const meta = capture.latest || {};
  $(`${prefix}Meta`).textContent = `${capture.label}: task=${capture.taskId || meta.task_id || "-"} frame=${meta.frame_id || "-"} source=${meta.capture_source || "-"}`;
}

async function runSkill(skillId) {
  const skill = SKILLS.find((item) => item.id === skillId);
  if (!skill || skill.id === "remote_shutdown") {
    return;
  }
  selectedSkill = skill;
  renderRows();
  renderDetail();
  const log = [];
  const append = (line, value) => {
    log.push(value === undefined ? line : `${line}\n${JSON.stringify(value, null, 2)}`);
    $("verifyLog").textContent = log.join("\n\n");
  };

  try {
    append(`开始验证 ${skill.id}`);
    let before = null;
    if ($("captureFrames").checked) {
      append("抓取验证前画面...");
      before = await captureStill("before");
      setFrame("before", before);
      append("验证前画面元数据", before.latest);
    }

    const payload = {
      action: skill.id,
      source: "robot_skills",
      note: "live verification from /common/robot-skills",
      ttl_seconds: 90,
      settings_override: safeOverrides(skill),
    };
    if (skill.id === "speak") {
      const text = window.prompt("输入要播报的文本（≤200 字）", "你好，我是机器人，语音技能验证。");
      if (!text || !text.trim()) {
        append("已取消：未输入播报文本");
        return;
      }
      payload.params = { text: text.trim() };
    }
    append("创建云端任务", payload);
    const created = await apiJson(`${ACTION_BASE}/api/tasks`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    append("任务已创建", created.task);

    const task = await pollTask(created.task.id);
    append("任务最终状态", task);

    let after = null;
    if ($("captureFrames").checked) {
      append("抓取验证后画面...");
      after = await captureStill("after");
      setFrame("after", after);
      append("验证后画面元数据", after.latest);
    }

    append("验证说明", {
      skill_id: skill.id,
      status_from_action_service: task ? task.status : "unknown",
      evidence: skill.verify,
      note: "页面不构造通过/失败结论；请以任务真实状态、output/error、相机前后帧和传感器返回为准。",
    });
    refreshHealth();
  } catch (error) {
    append("验证异常", { error: error.message });
    refreshHealth();
  }
}

async function emergencyStop() {
  $("verifyLog").textContent = "发送急停任务...";
  try {
    const data = await apiJson(`${ACTION_BASE}/api/tasks`, {
      method: "POST",
      body: JSON.stringify({ action: "emergency_stop", source: "robot_skills", ttl_seconds: 30 }),
    });
    $("verifyLog").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    $("verifyLog").textContent = error.message;
  }
}

$("skillSearch").addEventListener("input", renderRows);
$("groupFilter").addEventListener("change", renderRows);
$("refreshHealth").addEventListener("click", refreshHealth);
$("emergencyStop").addEventListener("click", emergencyStop);

renderRows();
renderDetail();
refreshHealth();
setInterval(refreshHealth, 15000);
