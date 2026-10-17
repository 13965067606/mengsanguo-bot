# mengsanguo-bot
梦三国2巅峰争霸脚本

## 4.9 源码

本软件永久免费，为爱发电，请勿用于商业用途。请设置游戏界面风格为经典画面。

这是 4.9 版本源码，不含个人录像、操作采集、战绩、学习数据和运行日志。4.9 新增右上方“检查更新 / 一键更新”，战斗逻辑保持 4.8。
游戏及宣传素材版权归各自权利人所有；本仓库不对这些素材授予额外许可。

### 运行

需要 Windows、Python 3.11 或更新版本。在仓库根目录打开 PowerShell：

```powershell
Copy-Item config.example.json config.json
python -m pip install -r source/requirements.txt
python source/main.py
```

请先复制示例配置，再按自己的快捷键修改。自动操作适配 1024×768 游戏客户区；录制选项不代表自动操作支持其他分辨率。游戏需保持前台，F8 启动或暂停，F9 停止。

### 打包

```powershell
powershell -ExecutionPolicy Bypass -File source/build.ps1
```

生成程序需与 `config.json`、`templates/`、`assets/` 保持在同一目录。
英雄名识别使用可选的 Tesseract OCR；未安装时自动识别可能不可用。
详细功能见 [使用说明](使用说明.md)。离线检查不能替代实战验证，不保证战绩或收益；请自行遵守游戏服务条款。

### 隐私

请勿提交个人配置、录像、采集文件、战绩或学习文件。参考整图已替换为程序实际使用的局部识别图，识别像素、区域及阈值不变。

## 一键更新与发布

首次从 4.8 升级到 4.9 仍需手动安装一次，4.9 起可在软件内更新。
仅下载本仓库最新正式 Release 中的 `MengSanGuoBot-update.zip`；源码提交、草稿或预发布不会触发更新。下载要求 GitHub 返回 SHA256 摘要，缺少或不一致时拒绝安装。

发布者每次修改 `source/main.py` 的 `APP_VERSION`，打包后执行：

```powershell
python source/package_release.py --version 4.9 --output release-output
```

在 GitHub 仓库 Releases 创建版本（例如标签 `v4.9`），上传生成的 `MengSanGuoBot-update.zip`、`MengSanGuoBot-4.9.zip` 和 `SHA256.txt`，填写说明并作为正式版本发布。后续版本使用相应版本号。请不要将 GitHub Token 写入软件。

安装前必须停止挂机、录制和分析。更新只替换程序、模板、背景图和示例配置，不覆盖个人配置、战绩、学习数据、OCR 安装或录像；新增配置项启动时补入内存，现有选项保留。替换失败尝试恢复，错误见 `update_error.txt`；成功后的旧文件备份位置见 `update_success.txt`。

关闭软件时正进行的下载可能中断，但未完成校验不会替换原程序。重启后需手动按 F8 启动挂机。
