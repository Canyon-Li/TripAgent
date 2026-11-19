# AGENTS.md

环境事实指针（权威细节看 README / project.md / TASKS.md，本文件只记「不写在别处会踩坑」的）：

- **Python / pytest**：venv 在主仓库根 `.venv`（GitHub worktree 不自带）；跑法 `"/d/Desktop/Code/TripAgent/.venv/Scripts/python.exe" -m pytest -q`。
- **装 Python 包**：venv 无 pip，用 `uv pip install --python .venv <pkg>`；直连 pypi 的 TLS 可能被链路掐断，加 `UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`。
- **真实 LLM 跑数**：`.env` 不随 worktree 出现；需要真 key 时从主仓复制 `cp /d/Desktop/Code/TripAgent/.env .`。
- **评测 / 演示命令**：eval runner、check_plans、badcase_to_eval 用法见 README「评测与观测」等节与脚本 docstring。
- **浏览器自动化**：browser-use 插件在 worktree 会话报 "Request workspace is not owned by this CLI"；改用系统 Python（`D:\Python3-14\python.exe`，已装 playwright）+ `executable_path` 指向 `%LOCALAPPDATA%\ms-playwright\chromium-1228\chrome-win64\chrome.exe`（版本须与已装目录匹配，`playwright install` 可能装出别的版本号）。
- **本地起服务**：端口以 docker-compose.yml 为准；8000 常被遗留 uvicorn 实例占用，起前先探测。
- **阿里云服务器（2026-11-19 部署）**：是**轻量应用服务器**非 ECS——控制台 swas.console.aliyun.com，防火墙在实例详情「防火墙」页签（无安全组/VPC 概念），公网只放行 3000；1Panel 镜像为 RPM 系，装包用 dnf/yum（无 apt-get）。代码在服务器 `~/TripAgent`（本地 bundle + scp 中转克隆，origin 指向 bundle 文件），公网 demo 走 `http://<VPS-IP>:3000`（IP 不入库，见轻量控制台）。
- **服务器侧 Docker Hub 直连被掐**：`registry-1.docker.io` 超时，compose 构建拉不到基础镜像（python:3.11-slim / nginx:alpine 都在 Hub）；绕行 = 加速站（docker.m.daocloud.io / docker.1panel.live / docker.1ms.run 择活着者）`docker pull` 后 `docker tag` 回原名，不动 daemon.json（1Panel 共管）。
- **合并流程**（个人项目约定）：日常修复开 feature 分支 → **commit message需要简明易懂，且依照 Conventional Commits。merge 前先向作者出示完整 commit message 与改动清单，确认后才merge** `git merge --no-ff` → push main（main 保护只拦 PR、不拦直推，CI 事后照跑）；仅阶段收口等留档节点开 PR。
- **GitHub push 闪断**：SSH 22 与 https 直连都可能被链路掐断；绕行 `git push ssh://git@ssh.github.com:443/Canyon-Li/TripAgent.git <ref>:<ref>`。
