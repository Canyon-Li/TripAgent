# AGENTS.md

环境事实指针（权威细节看 README / project.md，本文件只记「不写在别处会踩坑」的）：

- **Python / pytest**：venv 在主仓库根 `.venv`（GitHub worktree 不自带）；跑法 `"/d/Desktop/Code/TripAgent/.venv/Scripts/python.exe" -m pytest -q`。pytest `pythonpath=["."]` 已配置——裸 `pytest` 与 `python -m pytest` 等价（历史上不等价，别依赖 cwd 注入）。
- **真实 LLM 跑数**：`.env` 不入库、不随 worktree 出现；需要真 key 时从主仓复制 `cp /d/Desktop/Code/TripAgent/.env .`（保持 gitignored）。
- **评测 / 演示命令**：eval runner、check_plans、badcase_to_eval 的权威用法在 README「评测与观测」节。
- **浏览器自动化**：browser-use 插件在 worktree 会话报 "Request workspace is not owned by this CLI"，不可用；fallback = 系统 Python（`D:\Python3-14\python.exe`，已装 playwright）+ `executable_path` 指向 `%LOCALAPPDATA%\ms-playwright\chromium-1228\chrome-win64\chrome.exe`（版本必须与已安装目录匹配，`playwright install` 可能装出另一个版本号）。
- **本地演示端口**：web 8890（静态目录）/ api 8002；8000 常被遗留实例占用，起服务前先探测。
