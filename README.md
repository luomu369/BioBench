# BioBench

> 一台 DGX Spark，一个 Agent，二十三个技能，和一个网页。
> **用自然语言做分子设计。**

BioBench 是一个生物分子设计工作台：它把本地 GPU 上的一批 AI 模型——结构预测、
结合剂设计、分子生成、分子对接、分子动力学——包装成一组 Agent Skills，让大模型
harness 用自然语言驱动它们，并在一个 Web 查看器里看到结果。

你对它说"在这个口袋设计一个 12 氨基酸的环肽"，它就去跑 BoltzGen；你说"把选中
的那个氢换成苯环"，它就去改结构；你说"做一下动力学模拟看这个姿态稳不稳"，它
就去跑 OpenMM。每一步的结果都发布到同一个网页，你看着结构一步步长出来。

- **仓库**：https://gitee.com/LRC-bit/biobench
- **硬件**：NVIDIA DGX Spark（GB10，aarch64，CUDA 13.0，121 GB 统一内存）
- **智能体模型**：StepFun 阶跃星辰 `step-5-preview`
- **许可**：MIT

---

## 一、作品特点与核心亮点

### 1.1 三个判断

**模型已经到位，接口没有。** BoltzGen、ProteinMPNN、GenMol、ResGen、Vina、
OpenMM 这些开源模型单个都能用，合起来用不了——每一个都有自己的 conda 环境、
权重下载方式、输入格式、输出解析。一个课题想走完"找口袋 → 生成分子 → 对接 →
动力学验证"这条链，要在四套环境、五种文件格式之间手动搬运。BioBench 做的
就是这层编排。

**硬件门槛降到了一台机器。** DGX Spark 的 121 GB 统一内存，是 BoltzGen 一次
典型设计所需（约 40 GB）的三倍。过去需要集群的工作，现在一台桌面盒子就能做。
但"能跑"和"能用"之间还有距离。

**Agent 改变了交互的单位。** 大模型 harness 让"用自然语言驱动命令行"成为现实，
Agent Skills 规范（`SKILL.md` + 脚本）给了它标准形态。BioBench 把这套形态用在
生物分子设计上——它不训练模型，不做推理优化，它解决编排问题。

### 1.2 核心亮点

**① 查看器里没有 LLM，也没有后端逻辑。** 它是一个静态页面，渲染技能发布到
磁盘上的三个文件。Agent 在命令行工作，查看器只负责显示。这个划分是整件事能
跑起来的原因：技能可以用任何语言写，查看器重启不丢状态，任何一个技能可以单独
测试，Agent 也能自己读这些文件回答"跑到哪了"。

**② 长任务由面板接管，不占着对话。** 模型提交一个 50 分钟的任务后就结束
turn，但它说"完成后我会汇报"——这个承诺原来没人守。现在面板用 **pid + 进程
启动时间**监控任务（pid 会被系统回收重用，光看 pid 会永远等一个早就死了的
任务），完成后自动唤醒模型并**附带日志尾部**，报告从结果写起而不是从一次轮询
写起。

**③ 拒绝多于猜测。** 生物分子计算的失败大多是静默的——一个错位的序列号、一个
被丢弃的残基、一条虚假的键，都不会报错，只会让数字错。所以 BioBench 的脚本
大量使用"检查然后退出"：PDB 列宽不合就抛异常，pid 死了就报告，选中的原子和
其他残基重叠就警告。`skills/_shared/silent-failures.md` 记录了 12 个真实发生
过的静默失败，这是项目最珍贵的资产。

**④ 领域知识与 Agent 循环分离。** 工具调用循环有无数现成实现，但"什么情况下
该用柔性对接"、"meeko 会丢哪些残基"、"这台机器的 CUDA 配哪个 PyTorch wheel"
只能写成文档。`SKILL.md` 是文档，`scripts/` 是操作，Agent 负责把两者接起来。
换一个 harness 不需要重写任何领域知识。

---

## 二、快速开始

```bash
# 一条命令启动全部：查看器 + Agent
bash start.sh
#    → http://localhost:8282

# 停止
bash stop.sh
```

`start.sh` 会按需安装 opencode（首次运行自动下载），先起 Agent 再起查看器，
两者都以 `setsid` 脱离终端——**VS Code 重启也杀不掉它们**。

也可以只起查看器（Agent 面板不可用，但结构照常显示）：

```bash
bash start.sh --no-agent
# 或
bash webapp/run_viewer.sh --port 8282
```

启动后右侧是 Agent 面板。直接用自然语言描述要做什么；在结构上选中残基或原子，
选中的内容会连同问题一起发给模型。

首次使用需要 API key：

```bash
echo 'STEPFUN_API_KEY=sk-xxx' > webapp/agent.key
```

环境变量 `STEPFUN_API_KEY` 同样有效，但活不过终端关闭——查看器比启动它的终端
活得长，所以推荐用文件。

### 目录结构

```
biobench/
├── SKILL.md                  入口：环境检查、路由、编排
├── start.sh / stop.sh        一键启动 / 停止（查看器 + Agent）
├── skills/                   23 个技能（部署 8 / 运行 13 / 发布 1 + 共享）
│   ├── _shared/              biobench_paths.py, pdb_format.py,
│   │                          selection.py, silent-failures.md, pytorch-cuda.md
│   └── <skill>/
│       ├── SKILL.md          领域知识（何时用、参数怎么定、坑在哪）
│       └── scripts/          可执行操作（preflight.py / 业务脚本）
├── webapp/
│   ├── serve.py              查看器服务端（静态服务 + Agent 面板 + 状态读写）
│   ├── agent.py              opencode 客户端：会话、流式、权限、任务监控
│   └── static/
│       ├── index.html        查看器与 Agent 面板（3Dmol.js，约 8400 行）
│       ├── current.cif       ← 发布的结构
│       ├── current.json      ← 发布的元数据
│       └── status.json       ← 发布的进度
└── vendor/                   内置的 opencode 二进制与项目配置
```

---

## 三、技术实现方案

### 3.1 三层划分，层间只通过文件通信

```
┌─────────────────────────────────────────────┐
│  Agent 层（opencode + step-5-preview）        │
│  读 SKILL.md，执行命令，决定做什么             │
└──────────────────┬──────────────────────────┘
                   │ 调用
┌──────────────────▼──────────────────────────┐
│  技能层（skills/，23 个）                     │
│  每个技能 = SKILL.md（知识）+ scripts/（操作） │
│  分三类：部署 / 运行 / 发布                   │
└──────────────────┬──────────────────────────┘
                   │ 读写文件
┌──────────────────▼──────────────────────────┐
│  查看器层（webapp/）                          │
│  静态页面 + 一个薄服务端                       │
│  渲染 current.cif / current.json / status.json│
└─────────────────────────────────────────────┘
```

没有 RPC，没有消息队列，没有数据库。技能把结果写到 `webapp/static/` 下的三个
文件，查看器每 2 秒轮询一次。

代价是轮询有 2 秒延迟。收益是：技能可以用任何语言写、查看器重启不丢状态、任何
一个技能可以单独测试、Agent 可以自己读这些文件回答"跑到哪了"。

### 3.2 发布契约：三个文件构成全部接口

| 文件 | 内容 | 谁写 |
|------|------|------|
| `current.cif` | 最新结构 | `publish.py --structure` |
| `current.json` | 结构元数据（链、角色、指标、参数、备注） | 同上 |
| `status.json` | 任务列表、当前步骤、进度消息、pid | `publish.py --status` |

`current.json` 里的 `params` 块是专门为解决"重对接时模型每次都要翻历史目录"而
加的——把盒心、穷举度、配体 SMILES 写成结构化数据，下一次运行直接读。这一个
字段把重对接的工具调用从 42 次降到 20 次左右。

`publish.py` 做原子写入（先写 `.tmp` 再 rename），所以查看器永远不会读到半个
文件。

### 3.3 Agent 面板：一个 opencode 客户端

```
浏览器                serve.py              agent.py            opencode
  │  POST /agent         │                      │                   │
  ├─────────────────────>│  agent.stream()      │                   │
  │                      ├─────────────────────>│  POST /session/id/prompt_async
  │                      │                      ├──────────────────>│
  │                      │                      │  GET /event (SSE) │
  │                      │                      │<──────────────────┤
  │  SSE: text/reasoning/tool/permission/waiting/done                │
  │<─────────────────────┼──────────────────────┤                   │
```

四个关键设计：

**流式，且只信 opencode 的结束信号。** 一个跑技能的 turn 要几分钟，面板显示
空白会让人以为坏了，所以文本、思考、工具调用都实时转发。而"什么时候算结束"
只用 `session.idle` 和 `message.updated` + `finish`——所有基于超时的规则都试过，
都对一半：短了会在模型还在工作时报告"无回复"，长了让每个简短回答都像卡住。

**权限请求转发给用户。** Agent 要跑白名单外的命令时，opencode 会阻塞等待。这个
请求通过 SSE 转到面板，用户点"允许一次"或"拒绝"。另有一个全局开关可一律自动
批准，代价是 Agent 能不经询问执行任何命令。

**后台任务由面板监控。** 见 1.2 ②。实现要点：`stream()` 结束时检查
`status.json`，有未完成任务就发 `waiting` 事件；前端每 2 秒轮询 `/agent/jobs`；
`_pid_alive(pid, starttime)` 用 `kill(pid, 0)` 加 `/proc/<pid>/stat` 的启动时间
双重校验；唤醒时从 `/proc/<pid>/cmdline` 解析 `--out` 找到日志，把尾部附在消息里。

**会话永不自动更换。** 曾经 `ensure_session()` 在 session busy 时静默建一个新
的，结果用户在任务运行期间问"还在跑吗"，消息被扔进一个空会话——上下文全丢。
现在 session 的选择权完全在用户：`＋` 新建，`☰` 切换历史，停止按钮中断。

### 3.4 查看器

`webapp/static/index.html`，约 8400 行，3Dmol.js 渲染。左侧三栏（指标、序列、
选择），右侧 Agent 面板，中间是 3D 舞台。

- **多标签**：每个发布的结构一个标签，可切换、可关闭，状态按标签保存
- **选择模式**：残基 / 链 / 原子 / 键 / 口袋，选中内容随问题一起发给 Agent
- **背景可换**：四个预设色 + 自定义图片（canvas 用 alpha 0 透明白，图片在下层）
- **空状态**：一个分子结构 logo 加一行介绍

---

## 四、架构设计思路

### 4.1 为什么用 Agent Skills 而不是写一个 Agent 框架

因为**领域知识比 Agent 循环更难写**。工具调用循环有无数现成实现，但"什么情况下
该用柔性对接"、"meeko 会丢哪些残基"、"这台机器的 CUDA 配哪个 PyTorch wheel"
——这些只能写成文档，而文档需要人来维护。

Agent Skills 规范把这两半分开：`SKILL.md` 是文档，`scripts/` 是操作。Agent 负
责把两者接起来。换一个 harness（Claude Code → Copilot → opencode）不需要重写
任何领域知识。

### 4.2 为什么查看器是静态页面

一开始想加一个后端做实时推送。实际做出来发现：**技能已经在写文件了，查看器只
需要读。** 加一个 WebSocket 服务要多维护一个进程、一个协议、一个失败模式，而
轮询 2 秒的延迟在这个场景里完全够用。

静态页面还带来一个好处：Agent 可以自己读 `status.json` 回答"跑到哪了"，不需要
任何额外接口。

### 4.3 为什么内置 opencode 而不是让用户自己接

试过让用户配 Claude Code 或 Copilot，但每个 harness 的 skill 发现机制、权限
模型、会话存储都不一样。内置一个 opencode（vendored 在 `vendor/bin/`，184 MB）
换来的是：权限系统、会话持久化、上下文压缩、SSE 事件流，全都是现成的。

### 4.4 为什么拒绝多于猜测

```python
if value >= 10 ** width:
    raise PdbFormatError(f"{what} {value} does not fit in {width} columns...")
```

```python
if alive is not False:
    return True   # pid 死了就是死了，不猜
```

```python
if others:
    print(f"warning: serial {s} is within 1.6 A of atoms from other residues...")
```

因为生物分子计算的失败大多是静默的。**一个明确的错误消息比一个看似成功的运行
有价值得多。**

---

## 五、Agent Skills 设计

### 5.1 形态

每个技能是一个目录：

```
skills/binder-design/
├── SKILL.md           # YAML frontmatter（name + description）+ Markdown 正文
└── scripts/
    ├── preflight.py   # 部署类：跑之前先验证
    └── ...
```

`SKILL.md` 的 frontmatter 里 `description` 是**路由依据**——Agent 靠它决定用哪个
技能。所以 description 写得很密：做什么、什么时候用、什么时候**不**该用、依赖
什么。例如 `molecular-docking` 的 description 明确排除了"安装 Vina（用
vina-deploy）"和"生成配体本身（用 molecule-generation）"。

### 5.2 三类技能

**部署类（8 个）** — 每个都是"安装 + 验证"。`preflight.py` 检查环境、导入、
权重、GPU，能修的加 `--fix`。设计原则是**先验证再跑**：一个缺权重的 BoltzGen
会在跑了 20 分钟后才失败，而 preflight 在 10 秒内就能说出来。

**运行类（13 个）** — 从找口袋到动力学验证的完整链条：

```
pocket-detection ──> pocket-molecule-design ──> molecular-docking
                         │                            │
                         ▼                            ▼
                    molecule-generation        molecular-dynamics
                         │
                         ▼
                    molecule-editing ──> sequence-design
```

每个运行类技能遵循同一形状：**确认参数 → 报告进度 → 执行 → 解释结果 → 发布**。

**发布类（1 个）** — `structure-viewer` 提供 `publish.py`，是所有技能的出口。

### 5.3 共享层

`skills/_shared/` 存放所有技能共用的东西，这是避免"每个技能写一份然后各自漂移"
的关键：

| 文件 | 作用 |
|------|------|
| `biobench_paths.py` | 所有路径从机器解析，不写死 |
| `pdb_format.py` | PDB 定宽列的读写，列宽不合就抛异常 |
| `selection.py` | 读查看器的选择、解析序列号、建 RDKit mol |
| `silent-failures.md` | 12 个真实发生过的静默失败 |
| `pytorch-cuda.md` | 哪个 PyTorch wheel 匹配哪个驱动 |

### 5.4 从对话中学习

Skills 不是一次写成的。项目运行中模型的每一轮困惑都会被回收成文档：

| 困惑 | 回收成 |
|------|--------|
| 重对接时翻 5 个目录找参数 | `publish.py --params` + SKILL.md「参数从 current.json 读」 |
| 环肽设计读 18 次 boltzgen 源码 | SKILL.md 补正确 yaml 模板 + 「别读源码，错误信息会告诉你」 |
| 苯环编辑折腾 100 次工具调用 | 脚本加重叠检测 + 「先验证再动手」+ publish 参数全表 |
| MD 用 `sleep 280` 傻等 | 系统提示加「后台任务提交后就结束 turn」 |
| `pgrep -f` 把已结束的任务报成在跑 | `silent-failures.md` 新增一节 |
| MD 发布 375,290 原子的溶剂化帧 | SKILL.md 改为发布去溶剂帧 + 完整脚本 |

---

## 六、本地算力部署说明

### 6.1 硬件与系统

| 项 | 值 |
|---|---|
| 机器 | NVIDIA DGX Spark |
| GPU | NVIDIA GB10 |
| 内存 | 121 GB 统一内存（CPU/GPU 共享） |
| 驱动 | 580.142 |
| CUDA | 13.0 |
| 架构 | aarch64 (ARM64) |

### 6.2 部署步骤

```bash
# 1. 克隆
git clone https://gitee.com/LRC-bit/biobench.git
cd biobench

# 2. 检查环境（加 --fix 修复）
bash skills/viewer-launch/scripts/bootstrap.sh

# 3. 配置模型 key
echo 'STEPFUN_API_KEY=sk-xxx' > webapp/agent.key

# 4. 一键启动（首次会自动下载 opencode）
bash start.sh
#    → http://localhost:8282
```

`bootstrap.sh` 检查：conda 环境是否齐全、CUDA 驱动版本、PyTorch wheel 是否匹配
驱动、各工具二进制是否在 PATH、模型权重是否就位。每一项失败都给出修复命令。

### 6.3 环境组织

八个 conda 环境，按依赖冲突隔离——这是让一批互不兼容的模型共存在一台机器上的
唯一办法：

| 环境 | 装了什么 |
|------|----------|
| `docking` | AutoDock Vina + meeko + RDKit + gemmi |
| `boltzgen` | BoltzGen + PyTorch (cu130) |
| `genmol` | GenMol |
| `resgen` | ResGen |
| `proteinmpnn` | ProteinMPNN |
| `openmm` | OpenMM + AmberTools + PDBFixer |
| `webapp` | 查看器服务端（纯标准库） |
| `comfyui` | 无关（机器上原有） |

外部工具装在 `~/tools/`：`vina`、`foldseek`、`fpocket`、`mmseqs`、`ResGen`。
模型权重在 `~/models/`：`boltzgen-1`（18 GB）、`genmol`（1.4 GB）、
`proteinmpnn`（45 MB）。

### 6.4 智能体如何利用本地算力

```
用户（浏览器）
  │ 自然语言 + 选中的残基
  ▼
webapp/serve.py ──> webapp/agent.py ──> opencode (vendor/bin/opencode)
                                            │
                                            │ 读 skills/*/SKILL.md
                                            │ 生成 bash 命令
                                            ▼
                                      conda run -n <env> python scripts/xxx.py
                                            │
                                            │ 调用本地 GPU / CPU
                                            ▼
                                      BoltzGen / Vina / OpenMM / ...
                                            │
                                            │ 写 webapp/static/
                                            ▼
                                      publish.py ──> 查看器（2 秒轮询）
```

关键点：**Agent 不直接调 GPU**。它生成 shell 命令，由各 conda 环境的 Python 解释
器执行，PyTorch/OpenMM 通过 CUDA 13.0 驱动访问 GB10。这样 Agent 无需理解 CUDA
版本、wheel 兼容性、环境隔离——这些都在 `preflight.py` 和 `pytorch-cuda.md` 里
处理掉了。

---

## 七、大模型优化方案

### 7.1 模型选择

**StepFun 阶跃星辰 `step-5-preview`**，通过 OpenAI 兼容接口接入：

```jsonc
"provider": {
  "stepfun": {
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "https://api.stepfun.com/step_plan/v1",
      "apiKey": "{env:STEPFUN_API_KEY}"
    },
    "models": {
      "step-5-preview": { "limit": { "context": 1000000, "output": 65536 } }
    }
  }
}
```

选它的理由：**reasoning 模型**。它会返回 `reasoning` 和 `content` 两部分，前者
是工作过程。在生物分子任务里，"它看了什么文件、算了什么距离"往往和答案同样重
要——一个亲和力数字，能看到它是从哪个 `summary.json` 读出来的，才敢信。

查看器把 reasoning 渲染成可折叠的「思考过程」块，工具调用渲染成「工具调用」块，
顺序是：思考 → 工具 → 回答。跟着这个顺序读，就是跟着推理走到结论。

### 7.2 上下文优化

**① 选中内容随问题一起发送。** Agent 能自己读 `selection.json`，但那要花一次
工具调用和一轮往返。而在"为这个设计一个结合剂"这类问题里，选中内容**就是**问
题。所以 `agentSelectionSummary()` 生成一行摘要，直接附在消息里。

**② 运行参数写成结构化字段。** `current.json` 的 `params` 块让重对接不必翻历史
目录——模型第一次读 `current.json` 就拿到盒心、穷举度、配体 SMILES。

**③ 消息窗口要宽。** opencode 的 `?limit=N` 返回**最后** N 条消息，而一个跑了
skill 的 turn 会为每一步产生一条 assistant 消息——一次对接加检查是 40 条。
`history()` 和 `list_sessions()` 都用 `limit=200`，否则用户的那句提问会被挤出
窗口，对话从列表里消失。

**④ 唤醒时附带日志尾部。** 模型醒来时，最后几行日志已经在消息里了，不需要第一
个动作就是读文件。

### 7.3 工具调用优化

这是本项目花力气最多的地方。一个苯环编辑曾经调用 100 次工具，根因是**信息不在
模型手边**。优化手段：

| 手段 | 效果 |
|------|------|
| SKILL.md 给可抄的模板（环肽 yaml、发布参数全表） | 省 15-20 次读源码 |
| 脚本第一时间报告异常（重叠检测、序列号校验） | 省 50 次盲目排查 |
| 「先验证再动手」写进文档 | 省 30 次 edit→跑→改 循环 |
| 后台任务提交后就结束 turn | 省 12 次 `sleep 280` |
| `--params` 结构化参数 | 省 10 次目录考古 |

### 7.4 权限与安全

`opencode.jsonc` 里 `bash: "ask"` —— 一切命令先问。另有一个面板开关可一律自动
批准，适合长任务无人值守，但默认关闭。

`webapp/agent.key` 存放 API key，从不发到浏览器（静态页面里的任何东西都能被读
取），也从不进 git。

---

## 八、技术栈说明

### 8.1 NVIDIA 相关

| 项 | 版本/型号 |
|---|---|
| 硬件 | NVIDIA DGX Spark |
| GPU | NVIDIA GB10（121 GB 统一内存） |
| 驱动 | 580.142 |
| CUDA | 13.0 |
| PyTorch wheel | `cu130` |
| 计算平台 | OpenCL / CUDA（OpenMM 自动选择） |

### 8.2 StepFun 阶跃星辰相关

| 项 | 值 |
|---|---|
| 模型 | `step-5-preview` |
| 接口 | `https://api.stepfun.com/step_plan/v1`（OpenAI 兼容） |
| 接入方式 | `@ai-sdk/openai-compatible` |
| 上下文 | 1,000,000 tokens |
| 输出 | 65,536 tokens |
| 用途 | Agent 的推理与工具调用（reasoning 模型） |

### 8.3 生物分子模型与工具

| 类别 | 名称 | 用途 |
|------|------|------|
| 设计 | BoltzGen | 结合剂/配体设计（扩散 + 结构预测） |
| 序列 | ProteinMPNN | 固定骨架的序列设计 |
| 生成 | GenMol | 小分子生成（全新/补全/连接） |
| 生成 | ResGen | 口袋条件的分子生成 |
| 对接 | AutoDock Vina | 分子对接与打分 |
| 动力学 | OpenMM + AmberTools | 分子动力学模拟 |
| 搜索 | Foldseek | 结构搜索与聚类 |
| 搜索 | MMseqs2 | 序列搜索 |
| 口袋 | fpocket | 结合口袋检测 |
| 化学 | RDKit | 分子图、几何、SMILES |
| 结构 | gemmi | mmCIF/PDB 解析 |
| 制备 | meeko | 配体/受体制备 |

### 8.4 软件框架

| 类别 | 名称 |
|------|------|
| 智能体 | opencode 1.18.32（vendored） |
| 3D 渲染 | 3Dmol.js |
| 服务端 | Python 3 标准库（`http.server`，无第三方依赖） |
| 环境管理 | conda / Miniconda3 |
| 规范 | Agent Skills（`SKILL.md` + scripts） |

---

## 九、技能清单

| 技能 | 职责 |
|------|------|
| `biobench`（根） | 入口 — 环境检查、路由、编排 |
| `viewer-launch` | 启动/重启查看器；环境引导 |
| `structure-viewer` | 结构与进度的发布契约 |

**部署（8）**：`vina-deploy`、`boltzgen-deploy`、`proteinmpnn-deploy`、
`genmol-deploy`、`resgen-deploy`、`foldseek-deploy`、`mmseqs-deploy`、
`molecular-dynamics-deploy`

**运行（13）**：`molecular-docking`、`molecular-dynamics`、`pocket-detection`、
`binder-design`、`ligand-binding-design`、`pocket-molecule-design`、
`molecule-generation`、`molecule-editing`、`sequence-design`、`sequence-search`、
`structure-comparison`、`conformational-analysis`、`admet-prediction`

技能遵循开放的 Agent Skills 规范，可单独安装：

```bash
ln -s "$PWD/skills/binder-design" ~/.claude/skills/binder-design
```

---

## 十、现状与边界

### 10.1 数字

| 项 | 数 |
|---|---|
| 技能 | 23 |
| 技能脚本（Python） | ~7,300 行 |
| 技能文档（Markdown） | ~7,100 行 |
| 查看器 + 服务端 | ~8,400 行 |
| 记录的静默失败 | 12 |
| conda 环境 | 8 |
| 模型权重 | ~20 GB |

### 10.2 已知边界

- **opencode 1.18.32 的 bash 工具会卡死**：子进程退出后状态机收不到信号，
  session 永远 busy。目前的解法是停止按钮 abort。
- **`pocket-molecule-design` 会把多个设计叠在一起发布**：10 个分子占同一位置，
  后续编辑会歧义。脚本现在会警告，但发布环节还没修。
- **单用户、单机**：没有多用户，没有远程部署，没有集群调度。
- **视频演示**：受 Gitee 单文件 100 MB 限制，计划放 Release 附件而非进 git。

### 10.3 一句话

**BioBench 把一台 DGX Spark 上的生物分子 AI 工具，变成一个可以用自然语言对话的
工作台——Agent 负责跑，查看器负责看，技能负责不出错。**
