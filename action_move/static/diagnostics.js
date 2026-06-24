const onlinePill = document.querySelector("#onlinePill");
const refreshBtn = document.querySelector("#refreshBtn");
const deviceKv = document.querySelector("#deviceKv");
const safetyKv = document.querySelector("#safetyKv");
const servicesList = document.querySelector("#servicesList");
const processList = document.querySelector("#processList");
const nodesList = document.querySelector("#nodesList");
const topicsList = document.querySelector("#topicsList");
const dockerList = document.querySelector("#dockerList");
const tasksBox = document.querySelector("#tasksBox");
const servicesPre = document.querySelector("#servicesPre");
const dockerPre = document.querySelector("#dockerPre");
const rosPre = document.querySelector("#rosPre");
const processPre = document.querySelector("#processPre");

const SERVICE_ROLES = {
  "action-move-poller": "从云端领取动作任务，并把执行结果和心跳回传到网页。",
  "action-move-controller": "树莓派本地 ROS 控制服务，把动作任务转换为 ROS 消息。",
  "camera-snapshot-sender": "负责摄像头抓拍和上传，供 /camera 页面查看。",
  docker: "运行 TurboPi 的 ROS2 容器，里面包含底盘、摄像头、超声波等节点。",
};

const NODE_ROLES = {
  "/action_move_edge_controller": "我们新增的动作控制节点，接收云端任务并发布 /cmd_vel、舵机、RGB、测距等消息。",
  "/mecanum_chassis_node": "麦克纳姆底盘转换节点，把 /cmd_vel 转换成四个轮子的电机速度。",
  "/ros_robot_controller": "底层硬件控制节点，接收电机、舵机、RGB 等控制消息并写入控制板。",
  "/usb_cam": "USB 摄像头采集节点，产生原始图像。",
  "/web_video_server": "把 ROS 摄像头画面转换成网页可访问的视频/图片服务。",
  "/sonar_controller": "超声波测距和部分 RGB 灯控制节点。",
  "/gesture_control_node": "手势识别应用节点，可能也会发布 /cmd_vel。",
  "/object_tracking": "目标跟踪应用节点，可能会发布 /cmd_vel。",
  "/line_following": "巡线应用节点，可能会发布 /cmd_vel。",
  "/avoidance_node": "避障应用节点，可能会发布 /cmd_vel。",
  "/qrcode": "二维码识别应用节点，可能会发布 /cmd_vel。",
  "/rosbridge_websocket": "ROSBridge WebSocket 服务，给网页或外部程序访问 ROS。",
  "/rosapi": "ROSBridge 查询 API 节点。",
  "/startup_check_node": "TurboPi 启动检查节点。",
};

const TOPIC_ROLES = {
  "/cmd_vel": "底盘运动速度指令。linear.x 是前后，linear.y 是左右平移，angular.z 是转向。",
  "/controller/cmd_vel": "备用/兼容的底盘速度话题，目前主要观察是否有订阅者。",
  "/ros_robot_controller/set_motor_speeds": "四轮电机速度控制消息，由 mecanum_chassis_node 发布给 ros_robot_controller。",
  "/ros_robot_controller/pwm_servo/set_state": "PWM 舵机控制消息，用于摄像头朝向微调。",
  "/ros_robot_controller/bus_servo/set_position": "总线舵机位置控制消息。",
  "/ros_robot_controller/bus_servo/set_state": "总线舵机状态/配置控制消息。",
  "/image_raw": "摄像头原始图像流。",
  "/image_raw/compressed": "压缩后的摄像头图像流，网页预览常用。",
  "/camera_info": "摄像头标定和分辨率等信息。",
  "/sonar_controller/get_distance": "超声波距离读数。",
  "/sonar_controller/set_rgb": "超声波模块或灯珠 RGB 控制。",
};

const PROCESS_ROLES = [
  ["edge_action_poller.py", "动作云端轮询器"],
  ["edge_ros_controller.py", "动作 ROS 控制器"],
  ["pi_camera_sender.py", "摄像头上传"],
  ["sonar_controller", "超声波测距"],
  ["mecanum", "底盘运动转换"],
  ["ros_robot_controller", "底层硬件控制"],
  ["usb_cam", "摄像头采集"],
  ["rosbridge_websocket", "ROS 网页桥接"],
  ["gesture_control_node", "手势识别应用"],
  ["line_following", "巡线应用"],
  ["avoidance_node", "避障应用"],
  ["qrcode", "二维码应用"],
  ["qt_lcd_client.py", "LCD 表情/显示程序"],
  ["edge_audio_listener.py", "语音监听程序"],
];

function fmtTime(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString();
}

function fmtAge(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.round(value / 60)} 分钟`;
}

function lines(value) {
  if (Array.isArray(value)) return value.join("\n") || "-";
  if (typeof value === "string") return value || "-";
  return "-";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function kv(container, rows) {
  container.innerHTML = rows
    .map(([key, value]) => `<b>${escapeHtml(key)}</b><span>${escapeHtml(value === undefined || value === "" ? "-" : value)}</span>`)
    .join("");
}

function itemHtml(name, role, options = {}) {
  const state = options.state || "";
  const dotClass = state === "active" || state === "ok" ? "ok" : state === "bad" ? "bad" : "";
  return `
    <div class="item">
      <div class="name"><span class="status-dot ${dotClass}"></span>${escapeHtml(name)}</div>
      <div class="role">${escapeHtml(role || "暂未识别用途，保留原始名称供排查。")}</div>
    </div>
  `;
}

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function parseRosSections(rosLines = []) {
  const sections = { nodes: [], topics: [], cmdvel: [], motor: [] };
  let section = "";
  for (const line of rosLines) {
    if (line === "NODES") {
      section = "nodes";
      continue;
    }
    if (line === "TOPICS") {
      section = "topics";
      continue;
    }
    if (line === "CMDVEL") {
      section = "cmdvel";
      continue;
    }
    if (line === "MOTOR") {
      section = "motor";
      continue;
    }
    if (section && line.trim()) sections[section].push(line.trim());
  }
  return sections;
}

function renderServices(serviceLines = []) {
  const names = ["action-move-poller", "action-move-controller", "camera-snapshot-sender", "docker"];
  servicesList.innerHTML = names
    .map((name, index) => {
      const state = serviceLines[index] || "unknown";
      return itemHtml(`${name}: ${state}`, SERVICE_ROLES[name], { state: state === "active" ? "active" : "bad" });
    })
    .join("");
}

function renderDocker(dockerLines = []) {
  if (!dockerLines.length) {
    dockerList.innerHTML = itemHtml("未检测到 Docker 容器", "TurboPi ROS 容器可能未运行。", { state: "bad" });
    return;
  }
  dockerList.innerHTML = dockerLines
    .map((line) => itemHtml(line, "TurboPi ROS2 运行环境，底盘/摄像头/超声波节点都在这里面。", { state: "ok" }))
    .join("");
}

function renderNodes(nodes = []) {
  if (!nodes.length) {
    nodesList.innerHTML = itemHtml("未上报 ROS 节点", "等待树莓派下一次诊断心跳。", { state: "bad" });
    return;
  }
  nodesList.innerHTML = nodes.map((node) => itemHtml(node, NODE_ROLES[node], { state: "ok" })).join("");
}

function renderTopics(topics = []) {
  if (!topics.length) {
    topicsList.innerHTML = itemHtml("未上报 ROS topic", "等待树莓派下一次诊断心跳。", { state: "bad" });
    return;
  }
  topicsList.innerHTML = topics
    .map((line) => {
      const topic = line.split(" ")[0];
      const role = `${TOPIC_ROLES[topic] || "ROS 消息通道。"} ${line.includes("[") ? `类型：${line.slice(line.indexOf("[") + 1, line.lastIndexOf("]"))}` : ""}`;
      return itemHtml(topic, role, { state: "ok" });
    })
    .join("");
}

function processRole(line) {
  const match = PROCESS_ROLES.find(([needle]) => line.includes(needle));
  return match ? match[1] : "系统或 ROS 相关进程。";
}

function renderProcesses(processLines = []) {
  const rows = processLines.filter((line) => !line.startsWith("PID")).slice(0, 14);
  if (!rows.length) {
    processList.innerHTML = itemHtml("未上报进程", "等待树莓派下一次诊断心跳。", { state: "bad" });
    return;
  }
  processList.innerHTML = rows.map((line) => itemHtml(line, processRole(line), { state: "ok" })).join("");
}

function renderTasks(tasks) {
  tasksBox.innerHTML = "";
  if (!tasks.length) {
    tasksBox.textContent = "暂无任务";
    return;
  }
  for (const task of tasks) {
    const node = document.createElement("div");
    node.className = `task ${task.status || ""}`;
    const error = task.error ? `<div>错误：${escapeHtml(task.error)}</div>` : "";
    node.innerHTML = `
      <strong>${escapeHtml(task.name_zh || task.skill_id || task.action)}</strong>
      <div>${escapeHtml(task.id || "")}</div>
      <div>${escapeHtml(fmtTime(task.requested_at))} · ${escapeHtml(task.status || "-")} · ${escapeHtml(task.source || "-")}</div>
      ${error}
    `;
    tasksBox.appendChild(node);
  }
}

async function refresh() {
  const data = await api("../api/diagnostics");
  const device = data.device || {};
  const diagnostics = data.diagnostics || {};
  const online = Boolean(device.online);
  const rosSections = parseRosSections(diagnostics.ros || []);

  onlinePill.textContent = online ? `在线 ${device.status || "idle"}` : `离线 ${fmtAge(device.age_seconds)}`;
  onlinePill.classList.toggle("ok", online);
  onlinePill.classList.toggle("bad", !online);

  kv(deviceKv, [
    ["设备 ID", device.device_id],
    ["主机名", device.hostname],
    ["IP", device.ip_address],
    ["Wi-Fi", device.wifi_ssid],
    ["网关", device.gateway],
    ["最后心跳", fmtTime(device.last_seen_at)],
    ["心跳年龄", fmtAge(device.age_seconds)],
    ["当前任务", device.current_task_id || "-"],
  ]);

  kv(safetyKv, [
    ["底盘熔断", diagnostics.disabled_actions || "未上报"],
    ["诊断上报", fmtTime(diagnostics.reported_at)],
    ["Uptime", diagnostics.uptime],
    ["Throttled", diagnostics.throttled],
    ["网页设置", `${data.settings?.unit_distance_cm ?? "-"}cm / ${data.settings?.turn_angle_deg ?? "-"}° / ${data.settings?.sensitivity ?? "-"}x`],
    ["待处理任务", data.pending_tasks],
  ]);

  renderServices(diagnostics.services || []);
  renderDocker(diagnostics.docker || []);
  renderNodes(rosSections.nodes);
  renderTopics(rosSections.topics);
  renderProcesses(diagnostics.top_processes || []);
  renderTasks(data.recent_tasks || []);

  servicesPre.textContent = lines(diagnostics.services);
  dockerPre.textContent = lines(diagnostics.docker);
  rosPre.textContent = lines(diagnostics.ros);
  processPre.textContent = lines(diagnostics.top_processes);
}

refreshBtn.addEventListener("click", () => {
  refresh().catch((error) => {
    onlinePill.textContent = `读取失败 ${error.message}`;
    onlinePill.classList.add("bad");
  });
});

refresh().catch((error) => {
  onlinePill.textContent = `读取失败 ${error.message}`;
  onlinePill.classList.add("bad");
});
setInterval(() => {
  refresh().catch(() => {});
}, 5000);
