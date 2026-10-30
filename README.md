# 刷题调度 · LeetCode Review Scheduler

把「刷题」从"做题数量竞赛"改成"题型卡的间隔复习"。本地运行，数据落在你自己的 Obsidian 库里。

A local spaced-repetition scheduler for LeetCode. The unit of review is a **pattern card**, not a problem. Data lives in your own Obsidian vault.

---

## 它解决什么

| 常见做法 | 结果 | 本工具的做法 |
|---|---|---|
| 复习 = 把旧题再做一遍 | 池子只进不出，越积越多，最后放弃 | 每张卡有档位，连续通过 5 档就**归档出池** |
| 看着答案写过程 | 识别 ≠ 生成，考场写不出 | 复习页强制三阶段，答案默认遮住 |
| 忘了哪天该复习 | 想起来的间隔已经太晚 | 到期日由系统算，每天只给你今天该做的 |
| 题是散的 | 100 道题 = 100 个记忆负担 | 题挂到卡上，负担从 N 降到题型数 k |

## 核心规则

- **三态队列**：候补（排队待学）→ 在途（正在复习，默认 15 题）→ 完成（归档出池）。出池一个，候补队头自动补一个；候补顺序可拖拽调整。
- **一道闸门**：在途 ≤ 15 道**不同题目**（可调）。同一题当月复习 4 次仍算 1 题；满了新题自动进候补，不拒收。
- **待归纳题型区**：录了但还没归到题型上的题，不设上限，但堆着不归纳等于没学。
- **间隔阶梯**：1 / 3 / 7 / 15 / 30 天（可调）。通过升一档，挂退一档，持平原地，连续两次持平整降档。
- **判定不看绝对项数**：`通过率 >= 阈值` 即通过。检验项可以自己增删改名。
- **三个时段**：早班新题、午班到期卡、晚班口述（不写码）。数量都能改；录入时可指定排进哪一班。
- **当日任务** = 今天要复习的 + 今天新录的，全部落在在途里。
- **归档**：最后一档通过 → 出池，不再复习；可从卡库手动"回炉"。
- **统计**：解题进度环（按难度分色）、过去一年日历热力图、最近通过列表。
- **浏览器扩展**：在 LeetCode 题目页直接抓题号/题名/难度/标签，点一下就录用。

## 浏览器扩展

Edge 打开 `edge://extensions/` → 开开发人员模式 → 加载解压缩的扩展 → 选 `F:\WB-box\刷题调度\extension`。

明细见 [docs/扩展安装.md](docs/扩展安装.md)。要点：**主程序没开也不会丢数据**，扩展会先存进本地队列，等主程序上线后自动补写（每分钟检查一次）。

主程序支持 `--silent`（起服务但不弹浏览器），配合开机启动可以做到"永远在线"：

```bash
python tools/install_autostart.py "F:\WB-box\刷题调度\刷题调度.exe"          # 安装
python tools/install_autostart.py "F:\WB-box\刷题调度\刷题调度.exe" --remove  # 卸载
```

## 跑起来

### 方式一：双击 exe

```
dist\刷题调度\刷题调度.exe
```

首次启动会在 `D:\MyNote\SelfNote\算法题目` 建库（不存在则自动创建），然后打开浏览器。

### 方式二：源码

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

### 数据搬家

设置页 → 填新路径 → 「迁移到此路径」。程序会把数据复制过去并改写指针文件，原目录保留。
代码里没有硬编码绝对路径，所以整个目录搬到 E 盘也不会报错。

## 目录结构

```
刷题调度/
├─ app/
│  ├─ api.py           HTTP 接口（含 /capture 供扩展一步入库）
│  ├─ scheduler.py     到期计算 / 闸门 / 每日排班
│  ├─ state.py         卡片状态机（纯函数，可单测）
│  ├─ db.py            连接 / 建表 / 迁移
│  ├─ config.py        数据根定位（多级回退）
│  ├─ schema.sql       建表语句
│  └─ web/             单页前端（零依赖）
├─ extension/          Edge/Chrome 扩展（MV3）
├─ tools/              打包辅助：快捷方式、开机启动
├─ assets/             图标
├─ tests/              调度与状态机的单元测试
├─ docs/SPEC.md        需求规格与数据模型
├─ docs/扩展安装.md     扩展安装与行为说明
└─ run.py              入口
```

## 开发

```bash
.venv\Scripts\python -m pytest -q
```

打包（onedir，避免 onefile 的自解压问题）：

```bash
.venv\Scripts\pyinstaller --onedir --name "刷题调度" \
  --add-data "app/web;app/web" --add-data "app/schema.sql;app" run.py
```

---

## English

A local app that schedules LeetCode review by **pattern card** instead of by problem.

- **Two gates**: at most 30 cards in flight, at most 20 unclassified problems. When full, you must clear, not add.
- **Ladder**: 1 / 3 / 7 / 15 / 30 days. Pass moves up, fail moves down, two consecutive holds drop a stage.
- **Pass = ratio, not count**: `passed / total >= threshold`. Add or remove check items freely.
- **Three slots a day**: morning new problems, noon due cards, evening oral recall.
- **Archive**: clearing the last stage removes the card from the queue.

Run the prebuilt binary at `dist\刷题调度\刷题调度.exe`, or from source with `python run.py`. Data location is configurable and relocatable — moving the folder to another drive will not break anything.

Stack: Python 3.13 · Flask · SQLite · vanilla JS. No build step, no cloud, no account.
