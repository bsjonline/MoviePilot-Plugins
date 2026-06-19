import os
import re
from pathlib import Path
from collections import defaultdict

from app.plugins import _PluginBase
from app.log import logger


class StrmDedupPlugin(_PluginBase):
    """123STRM 纯 size+md5 去重插件"""

    plugin_name = "123StrmDedup"
    plugin_version = "1.0.0"
    plugin_author = "HermesAgent"
    plugin_desc = "按文件中 URL 的 size 与 md5 去重，保留文件名最长的一条"

    _enabled = False
    _paths = ""
    _keep_longest = True
    _scheduler = None

    def init_plugin(self, config: dict):
        if not config:
            return
        self._enabled = config.get("enabled", False)
        self._paths = (config.get("paths") or "").strip()
        self._keep_longest = config.get("keep_longest", True)

        if not self._enabled or not self._paths:
            return

        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self.dedup,
            trigger=CronTrigger(hour=4, minute=0),
            id="strm_dedup_daily",
            replace_existing=True,
        )
        self._scheduler.start()

    def get_commands(self):
        return {
            "cmd": "/strm_dedup_pure",
            "title": "STRM size+md5 去重",
            "description": "读取配置目录，按 size+md5 去重，保留文件名最长的一条",
            "func": self.dedup,
        }

    def get_page(self):
        return {
            "key": "123strmdedup",
            "title": "123STRM 去重",
            "icon": "123strmdedup",
            "schema": [
                {
                    "tab": "去重设置",
                    "group": "基础",
                    "items": [
                        {
                            "id": "enabled",
                            "title": "启用插件",
                            "type": "switch",
                            "placeholder": "开启后将自动每日 04:00 去重，并注册 /strm_dedup_pure 命令",
                        },
                        {
                            "id": "paths",
                            "title": "STRM 目录",
                            "type": "textarea",
                            "placeholder": "每行一个目录路径，可多行",
                        },
                        {
                            "id": "keep_longest",
                            "title": "保留文件名最长",
                            "type": "switch",
                            "placeholder": "同一 size+md5 组内保留文件名最长的一条，其余删除",
                        },
                        {
                            "id": "run_now",
                            "title": "立即执行",
                            "type": "button",
                            "placeholder": "立即执行一次去重",
                            "click": "/strm_dedup_pure",
                        },
                    ],
                }
            ],
        }

    def dedup(self):
        RE_SIZE = re.compile(r'[?&](size|s|filesize|file_size)=(\d+)', re.I)
        RE_MD5 = re.compile(r'[?&](md5|hash|file_md5|md5sum)=([A-Fa-f0-9]{32})', re.I)

        raw = (self._paths or "").strip()
        if not raw:
            logger.warning("【123StrmDedup】未配置目录路径")
            return

        roots = [Path(line) for line in raw.replace("，", "\n").splitlines() if line.strip()]
        if not roots:
            logger.warning("【123StrmDedup】未配置目录路径")
            return

        buckets: dict = defaultdict(list)
        for root in roots:
            if not root.exists() or not root.is_dir():
                logger.warning(f"【123StrmDedup】目录不存在或不是目录，跳过: {root}")
                continue
            for base, _dirs, files in os.walk(root):
                for name in files:
                    if not name.lower().endswith(".strm"):
                        continue
                    full = Path(base) / name
                    try:
                        content = full.read_text(encoding="utf-8", errors="ignore").strip()
                    except Exception as e:
                        logger.warning(f"【123StrmDedup】读取失败: {full} => {e}")
                        continue
                    size = md5 = None
                    m = RE_SIZE.search(content)
                    if m:
                        size = m.group(2)
                    m = RE_MD5.search(content)
                    if m:
                        md5 = m.group(2).lower()
                    if size or md5:
                        buckets[(size, md5)].append(full)

        dup_groups = {k: v for k, v in buckets.items() if len(v) > 1}
        if not dup_groups:
            logger.info("【123StrmDedup】未发现重复。")
            return

        total = 0
        for key, files in dup_groups.items():
            files_sorted = sorted(files, key=lambda p: len(p.name), reverse=True)
            keeper = files_sorted[0]
            logger.info(
                f"【123StrmDedup】size={key[0]} md5={key[1]} => 保留 {keeper.name}，"
                f"共 {len(files_sorted)-1} 条重复"
            )
            if self._keep_longest:
                for pth in files_sorted[1:]:
                    try:
                        pth.unlink()
                        logger.info(f"【123StrmDedup】已删除: {pth}")
                        total += 1
                    except Exception as e:
                        logger.error(f"【123StrmDedup】删除失败: {pth} => {e}")

        logger.info(f"【123StrmDedup】完成，共删除 {total} 个重复 STRM 文件")

    def stop_service(self):
        try:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            print(str(e))
