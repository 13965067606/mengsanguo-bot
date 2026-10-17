"""Tk front end; network work never touches Tk from a worker thread."""
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import updater


def show_update_dialog(app, current_version):
    existing = getattr(app, "update_dialog", None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return
    dialog = tk.Toplevel(app.root)
    app.update_dialog = dialog
    dialog.title("检查更新 / 一键更新")
    dialog.geometry("560x380")
    dialog.configure(bg="#09121f")
    dialog.transient(app.root)
    dialog.grab_set()
    events = queue.Queue()
    state = {"busy": True, "info": None}
    status = tk.StringVar(value=f"当前版本 {current_version} · 正在连接 GitHub…")
    tk.Label(dialog, textvariable=status, bg="#09121f", fg="#69e0ff", wraplength=510, justify="left").pack(padx=20, pady=18, anchor="w")
    notes = tk.Text(dialog, height=10, bg="#050b13", fg="#d6e7f2", wrap="word", relief="flat")
    notes.pack(padx=20, fill="both", expand=True)
    notes.insert("1.0", "只从指定 GitHub 仓库下载正式发布的更新包。\n\n更新前请停止挂机和录制；配置、战绩、学习数据和录像不会被覆盖。")
    notes.configure(state="disabled")
    install = tk.Button(dialog, text="一键更新并重启", state="disabled", bg="#14647c", fg="white")
    install.pack(pady=18)

    def close():
        if app.update_in_progress:
            messagebox.showinfo("正在更新", "下载和校验尚未完成，请稍候。", parent=dialog)
            return
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", close)

    def can_install():
        return not (app.engine.active.is_set() or app.recorder.is_active or app.capture_stopping or app.analysis_in_progress)

    def download():
        if state["busy"] or state["info"] is None:
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("源码运行", "一键替换适用于打包程序；源码版本请通过 Git 更新。", parent=dialog)
            return
        if not can_install():
            messagebox.showwarning("请先停止", "请先按 F9 停止脚本、F12 结束录制，并等待采集分析完成，再更新。", parent=dialog)
            return
        if not messagebox.askyesno("确认更新", f"升级到 {state['info']['version']}？\n下载完成后软件将关闭并重新启动。\n个人配置和采集数据保留。", parent=dialog):
            return
        state["busy"] = True
        app.update_in_progress = True
        install.configure(state="disabled")
        status.set("正在下载… 0%")

        def worker():
            try:
                stage = updater.prepare_update(state["info"], lambda percent: events.put(("progress", percent)))
                events.put(("ready", stage))
            except Exception as exc:
                events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="UpdateDownload").start()

    install.configure(command=download)

    def check():
        try:
            events.put(("checked", updater.check_latest(current_version)))
        except Exception as exc:
            events.put(("error", str(exc)))

    def poll():
        if not dialog.winfo_exists():
            return
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == "checked":
                state["busy"] = False
                state["info"] = value
                status.set("当前已是最新正式版" if value is None else f"发现新版 {value['version']} · 当前 {current_version}")
                if value:
                    notes.configure(state="normal")
                    notes.delete("1.0", "end")
                    notes.insert("1.0", value["notes"])
                    notes.configure(state="disabled")
                    install.configure(state="normal")
            elif kind == "progress":
                status.set(f"下载 / 校验中… {value}%")
            elif kind == "error":
                state["busy"] = False
                app.update_in_progress = False
                status.set("更新未完成，原程序未替换")
                app.log.warning("检查/下载更新失败：%s", value)
                messagebox.showerror("无法更新", str(value)[:800] + "\n\n请稍后重新检查更新。", parent=dialog)
                install.configure(state="normal" if state["info"] else "disabled")
            elif kind == "ready":
                try:
                    if not can_install():
                        raise ValueError("挂机或录制仍在运行，已取消替换，请停止后重试")
                    updater.launch_installer(value, app.base)
                except Exception as exc:
                    shutil.rmtree(value, ignore_errors=True)
                    state["busy"] = False
                    app.update_in_progress = False
                    install.configure(state="normal")
                    messagebox.showerror("未安装更新", str(exc), parent=dialog)
                else:
                    # Shutdown before the helper touches files. No auto-starting gameplay.
                    app._close()
                    return
        dialog.after(100, poll)

    threading.Thread(target=check, daemon=True, name="UpdateCheck").start()
    poll()
