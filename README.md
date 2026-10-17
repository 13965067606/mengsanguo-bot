# mengsanguo-bot
梦三国2巅峰争霸脚本

## 4.8 源码

本软件永久免费，为爱发电，请勿用于商业用途。请设置游戏界面风格为经典画面。

这是 4.8 版本源码快照，不含个人录像、操作采集、战绩、学习数据和运行日志。
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
