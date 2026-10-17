把从 1024x768 游戏客户区截图中裁剪出的 PNG 模板放在此目录：

room.png         房间等待
matching.png     匹配中
hero_select.png  选人界面
ready_button.png 准备按钮
talent.png       初始天赋弹窗
talent_5.png     初始天赋弹窗中的第 5 项图标（作为第 1–5 项自定义选择的定位基准）
shop_auto_buy_label.png 商店中的“自动出装”文字，用于确认商店是否真正打开
card_smart_sell.png 翻卡界面的“智能出售”按钮
card_one_click.png 翻卡初始界面的“一键翻卡”按钮，用于识别尚未翻开的面板
card_count_zero.png 右侧卡包数量 0 的数字模板，用于排除不可翻卡包
in_game.png      正常游戏底部 HUD
dead.png         死亡状态底部 HUD
showcase.png     结算后的个人展示页（识别后按 Esc）
result.png       完整战绩页的“关闭”按钮
town_reference.png       城镇地图“社会”图标（已裁剪）
guild_reference.png      军团主界面“军团出征”入口（已裁剪）
expedition_reference.png 军团出征弹窗标题（已裁剪）
teleport_reference.png   B 传送可用时的金色图标参考帧
buyback_available.png    死亡后金钱充足时的金色“购买”按钮
digits/                  右上角击杀、死亡、助攻和战绩数字模板（含不同缩放变体）

程序只在 config.json 为各模板设置的 search_region 相对区域中搜索；带 template_crop 的完整参考截图会在加载时先截取指定模板区域。点击识别到的实际中心，不使用桌面绝对坐标。
本源码快照为保护玩家隐私，已预先裁剪参考整图，并在 config.example.json 中移除对应的 template_crop；有效模板像素与原版相同。
建议尽量选文字、按钮、HUD 等固定区域，避开人物、特效和动态背景。
