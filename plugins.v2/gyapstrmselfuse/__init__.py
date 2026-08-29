"""
光鸭云盘 STRM 助手：生成 STRM、监控整理入库、分享生成 STRM、空间清理一条龙服务

播放链路：STRM(redirect_url) -> 302 -> download_url(file_id) -> 真实下载地址
"""

import ast
import json
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from apscheduler.triggers.cron import CronTrigger
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.log import logger
from app.plugins import _PluginBase
from app.core.event import eventmanager, Event
from app.schemas.types import EventType, MediaType
from app import settings

from .tool import GuangyaAutoClient


plugin_name = "光鸭STRM"
plugin_desc = "光鸭云盘STRM生成一条龙服务"
plugin_icon = ""
plugin_version = "0.5.0"
plugin_author = "bsjonline"
plugin_config_prefix = "gyapstrmselfuse_"
plugin_order = 99
auth_level = 1


class MediaInfoDownloader:
    """
    媒体信息文件下载器
    """

    def __init__(self, client):
        self.client = client
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }

    @staticmethod
    def is_file_leq_1k(file_path):
        file = Path(file_path)
        if not file.exists():
            return True
        return file.stat().st_size <= 1024

    def get_download_url(self, item: Dict) -> Optional[str]:
        try:
            return self.client.download_url(file_id=str(item.get("fileId") or item.get("FileId") or ""))
        except Exception:
            return None

    def save_mediainfo_file(self, file_path: Path, file_name: str, download_url: str):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(download_url, stream=True, timeout=30, headers=self.headers) as response:
            response.raise_for_status()
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        logger.info(f"【媒体信息文件下载】保存 {file_name} 文件成功: {file_path}")

    def downloader(self, item: Dict, path: Path):
        download_url = self.get_download_url(item=item)
        if not download_url:
            logger.error(f"【媒体信息文件下载】{path.name} 下载链接获取失败")
            return
        self.save_mediainfo_file(file_path=path, file_name=path.name, download_url=download_url)

    def auto_downloader(self, downloads_list: List, subtitle_mode: bool = False):
        mediainfo_count: int = 0
        mediainfo_fail_count: int = 0
        mediainfo_fail_dict: List = []
        throttle = 10 if subtitle_mode else 50
        try:
            for item in downloads_list:
                if not item:
                    continue
                download_success = False
                try:
                    for _ in range(3):
                        self.downloader(item=item[0], path=Path(item[1]))
                        if not self.is_file_leq_1k(item[1]):
                            mediainfo_count += 1
                            download_success = True
                            break
                        logger.warn(f"【媒体信息文件下载】{item[1]} 下载该文件失败，自动重试")
                        time.sleep(1)
                except Exception as e:
                    logger.error(f"【媒体信息文件下载】 {item[1]} 出现未知错误: {e}")
                if not download_success:
                    mediainfo_fail_count += 1
                    mediainfo_fail_dict.append(item[1])
                else:
                    continue
                if mediainfo_count % throttle == 0:
                    logger.info("【媒体信息文件下载】休眠 2s 后继续下载")
                    time.sleep(2)
        except Exception as e:
            logger.error(f"【媒体信息文件下载】出现未知错误: {e}")
        return mediainfo_count, mediainfo_fail_count, mediainfo_fail_dict


class FullSyncStrmHelper:
    """
    全量生成 STRM 文件
    """

    def __init__(
        self,
        client,
        user_rmt_mediaext: str,
        user_download_mediaext: str,
        server_address: str,
        pan_dir_id: str = "",
        auto_download_mediainfo: bool = False,
    ):
        self.rmt_mediaext = [f".{ext.strip()}" for ext in user_rmt_mediaext.replace("，", ",").split(",")]
        self.download_mediaext = [f".{ext.strip()}" for ext in user_download_mediaext.replace("，", ",").split(",")]
        self.auto_download_mediainfo = auto_download_mediainfo
        self.client = client
        self.pan_dir_id = pan_dir_id
        self.strm_count = 0
        self.mediainfo_count = 0
        self.strm_fail_count = 0
        self.mediainfo_fail_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_dict: List = []
        self.server_address = server_address.rstrip("/")
        self._mediainfodownloader = MediaInfoDownloader(client=self.client)
        self.download_mediainfo_list = []

    def generate_strm_files(self, full_sync_strm_paths: str, full_sync_overwrite_mode: str = "never"):
        media_paths = full_sync_strm_paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            pan_media_dir = parts[1] if len(parts) > 1 else ""
            target_dir = parts[0]

            try:
                if self.pan_dir_id and str(self.pan_dir_id).strip().isdigit():
                    parent_id = int(self.pan_dir_id)
                    logger.info(f"【全量STRM生成】使用配置的云盘媒体目录 ID: {parent_id}")
                else:
                    fileitem = None
                    last_err = None
                    for storage_name in ("光鸭云盘", "guangyapan", "GYAP"):
                        try:
                            from app.helper.storage import StorageChain
                            _storagechain = StorageChain()
                            fi = _storagechain.get_file_item(storage=storage_name, path=Path(pan_media_dir))
                            if fi is not None:
                                fileitem = fi
                                break
                        except Exception as e:
                            last_err = e
                    if fileitem is None:
                        raise last_err or RuntimeError("未找到光鸭云盘存储，请检查 storagechain 配置")
                    parent_id = int(fileitem.fileid)
                    logger.info(f"【全量STRM生成】网盘媒体目录 ID 获取成功: {parent_id}")
            except Exception as e:
                logger.error(f"【全量STRM生成】网盘媒体目录 ID 获取失败: {e}")
                return False

            try:
                for item in self.client.list_dir(parent_id=str(parent_id)):
                    if item.get("isDir") or item.get("is_dir") or item.get("type") == 1:
                        continue
                    if pan_media_dir:
                        file_path = pan_media_dir + "/" + item.get("fileName", "")
                        file_path = Path(target_dir) / Path(file_path).relative_to(pan_media_dir)
                    else:
                        file_path = Path(target_dir) / item.get("fileName", "")
                    file_target_dir = file_path.parent
                    file_name = file_path.stem + ".strm"
                    new_file_path = file_target_dir / file_name

                    try:
                        if self.auto_download_mediainfo:
                            if file_path.exists():
                                if full_sync_overwrite_mode == "never":
                                    continue
                            if file_path.suffix.lstrip(".") in self.download_mediaext:
                                self.download_mediainfo_list.append(
                                    [{
                                        "fileId": int(item.get("fileId") or item.get("id") or 0),
                                        "FileName": item.get("fileName", ""),
                                        "Size": int(item.get("fileSize") or item.get("size") or 0),
                                        "Etag": item.get("etag") or item.get("Etag") or "",
                                    }, str(file_path)]
                                )
                                continue

                        if file_path.suffix.lstrip(".") not in self.rmt_mediaext:
                            continue

                        if new_file_path.exists():
                            if full_sync_overwrite_mode == "never":
                                continue

                        new_file_path.parent.mkdir(parents=True, exist_ok=True)
                        fname = item.get("fileName", "")
                        fsize = int(item.get("fileSize") or item.get("size") or 0)
                        fetag = item.get("etag") or item.get("Etag") or ""
                        fid = int(item.get("fileId") or item.get("id") or 0)

                        strm_url = (
                            f"{self.server_address}/api/v1/plugin/GYAPStrmSelfuse/redirect_url"
                            f"?apikey={settings.API_TOKEN}&name={fname}"
                            f"&size={fsize}&md5={fetag}"
                        )
                        if fid:
                            strm_url += f"&file_id={fid}"

                        with open(new_file_path, "w", encoding="utf-8") as file:
                            file.write(strm_url)
                        self.strm_count += 1
                        logger.info(f"【全量STRM生成】生成 STRM 文件成功: {new_file_path}")
                    except Exception as e:
                        logger.error(f"【全量STRM生成】生成 STRM 文件失败: {new_file_path}  {e}")
                        self.strm_fail_count += 1
                        self.strm_fail_dict[str(new_file_path)] = str(e)
                        continue
            except Exception as e:
                logger.error(f"【全量STRM生成】全量生成 STRM 文件失败: {e}")
                return False

        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self._mediainfodownloader.auto_downloader(downloads_list=self.download_mediainfo_list)
        )
        if self.strm_fail_dict:
            for path, error in self.strm_fail_dict.items():
                logger.warn(f"【全量STRM生成】{path} 生成错误原因: {error}")
        if self.mediainfo_fail_dict:
            for path in self.mediainfo_fail_dict:
                logger.warn(f"【全量STRM生成】{path} 下载错误")
        logger.info(
            f"【全量STRM生成】全量生成 STRM 文件完成，总共生成 {self.strm_count} 个 STRM 文件，"
            f"下载 {self.mediainfo_count} 个媒体数据文件"
        )
        if self.strm_fail_count != 0 or self.mediainfo_fail_count != 0:
            logger.warn(
                f"【全量STRM生成】{self.strm_fail_count} 个 STRM 文件生成失败，"
                f"{self.mediainfo_fail_count} 个媒体数据文件下载失败"
            )
        return True


class ShareStrmHelper:
    """
    根据分享生成STRM
    """

    def __init__(
        self,
        client,
        user_rmt_mediaext: str,
        user_download_mediaext: str,
        share_media_path: str,
        local_media_path: str,
        server_address: str,
        auto_download_mediainfo: bool = False,
    ):
        self.rmt_mediaext = [f".{ext.strip()}" for ext in user_rmt_mediaext.replace("，", ",").split(",")]
        self.download_mediaext = [f".{ext.strip()}" for ext in user_download_mediaext.replace("，", ",").split(",")]
        self.auto_download_mediainfo = auto_download_mediainfo
        self.client = client
        self.strm_count = 0
        self.mediainfo_count = 0
        self.strm_fail_count = 0
        self.mediainfo_fail_count = 0
        self.strm_fail_dict: Dict[str, str] = {}
        self.mediainfo_fail_dict: List = []
        self.share_media_path = share_media_path
        self.local_media_path = local_media_path
        self.server_address = server_address.rstrip("/")
        self._mediainfodownloader = MediaInfoDownloader(client=self.client)
        self.download_mediainfo_list = []

    def has_prefix(self, full_path, prefix_path):
        full = Path(full_path).parts
        prefix = Path(prefix_path).parts
        if len(prefix) > len(full):
            return False
        return full[: len(prefix)] == prefix

    def _share_recursive(self, share_code: str, share_pwd: str, parent_id: int = 0, rel_prefix: str = ""):
        """
        按目录逐层递归拉取分享文件
        """
        try:
            items = self.client.list_dir(parent_id=str(parent_id))
        except Exception as e:
            logger.error(f"【分享STRM生成】拉取分享目录失败,parent_id={parent_id}, error={e}")
            return

        for item in items:
            if item.get("isDir") or item.get("is_dir") or item.get("type") == 1:
                name = str(item.get("fileName") or item.get("name") or "").rstrip("/") + "/"
                child_rel = rel_prefix + name
                self._share_recursive(share_code=share_code, share_pwd=share_pwd,
                                      parent_id=int(item.get("fileId") or item.get("id") or 0),
                                      rel_prefix=child_rel)
            else:
                full_rel = rel_prefix + str(item.get("fileName") or item.get("name") or "")
                try:
                    fname = item.get("fileName") or item.get("name") or ""
                    fsize = int(item.get("fileSize") or item.get("size") or 0)
                    fetag = item.get("etag") or item.get("Etag") or ""
                    fid = int(item.get("fileId") or item.get("id") or 0)
                    file_path = Path(self.local_media_path) / full_rel
                    file_target_dir = file_path.parent
                    file_name = file_path.stem + ".strm"
                    new_file_path = file_target_dir / file_name

                    if self.auto_download_mediainfo and fsize:
                        if file_path.suffix.lstrip(".") in self.download_mediaext:
                            self.download_mediainfo_list.append(
                                [{"fileId": fid, "FileName": fname, "Size": fsize, "Etag": fetag}, str(file_path)]
                            )
                            continue

                    if file_path.suffix.lstrip(".") not in self.rmt_mediaext:
                        continue

                    if new_file_path.exists():
                        continue

                    new_file_path.parent.mkdir(parents=True, exist_ok=True)
                    strm_url = (
                        f"{self.server_address}/api/v1/plugin/GYAPStrmSelfuse/redirect_url"
                        f"?apikey={settings.API_TOKEN}&name={fname}"
                        f"&size={fsize}&md5={fetag}"
                    )
                    if fid:
                        strm_url += f"&file_id={fid}"
                    with open(new_file_path, "w", encoding="utf-8") as file:
                        file.write(strm_url)
                    self.strm_count += 1
                    logger.info(f"【分享STRM生成】生成 STRM 文件成功: {new_file_path}")
                except Exception as e:
                    logger.error(f"【分享STRM生成】生成失败: {e}")
                    self.strm_fail_count += 1
                    self.strm_fail_dict[str(full_rel)] = str(e)

        self.mediainfo_count, self.mediainfo_fail_count, self.mediainfo_fail_dict = (
            self._mediainfodownloader.auto_downloader(downloads_list=self.download_mediainfo_list)
        )
        logger.info(
            f"【分享STRM生成】完成，生成 {self.strm_count} 个 STRM，下载 {self.mediainfo_count} 个媒体文件"
        )

    def get_share_list_creata_strm(self, parent_id: int = 0, share_code: str = "", share_pwd: str = ""):
        if not share_code or not self.share_media_path or not self.local_media_path:
            return
        try:
            self._share_recursive(share_code=share_code, share_pwd=share_pwd, parent_id=parent_id, rel_prefix="")
        except Exception as e:
            logger.error(f"【分享STRM生成】运行失败: {e}")


class GYAPStrmSelfuse(_PluginBase):
    """
    光鸭云盘 STRM 助手：生成 STRM、监控整理入库、分享生成 STRM、空间清理一条龙服务
    """

    plugin_name = plugin_name
    plugin_desc = plugin_desc
    plugin_icon = plugin_icon
    plugin_version = plugin_version
    plugin_author = plugin_author
    plugin_config_prefix = plugin_config_prefix
    plugin_order = plugin_order
    auth_level = auth_level

    _client = None
    _scheduler = None
    _enabled = False
    _once_full_sync_strm = False
    _moviepilot_address = None
    _user_rmt_mediaext = None
    _user_download_mediaext = None
    _transfer_monitor_enabled = False
    _transfer_monitor_paths = None
    _transfer_monitor_scrape_metadata_enabled = False
    _transfer_mp_mediaserver_paths = None
    _transfer_monitor_media_server_refresh_enabled = False
    _transfer_monitor_mediaservers = None
    _timing_full_sync_strm = False
    _full_sync_auto_download_mediainfo_enabled = False
    _cron_full_sync_strm = None
    _full_sync_strm_paths = None
    _full_sync_pan_dir_id = None
    _full_sync_overwrite_mode = "never"
    _share_strm_enabled = False
    _share_strm_auto_download_mediainfo_enabled = False
    _user_share_code = None
    _user_share_pwd = None
    _user_share_pan_path = None
    _user_share_local_path = None
    _clear_recyclebin_enabled = False
    _clear_receive_path_enabled = False
    _cron_clear = None
    _deduplicate_strm_enabled = False
    _deduplicate_strm_paths = ""
    _deduplicate_strm_keep_longest = True
    _dir_name = "我的秒传"
    _uid = ""

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        if config:
            self._enabled = config.get("enabled")
            self._once_full_sync_strm = config.get("once_full_sync_strm")
            self._moviepilot_address = config.get("moviepilot_address")
            self._user_rmt_mediaext = config.get("user_rmt_mediaext")
            self._user_download_mediaext = config.get("user_download_mediaext")
            self._transfer_monitor_enabled = config.get("transfer_monitor_enabled")
            self._transfer_monitor_paths = config.get("transfer_monitor_paths")
            self._transfer_monitor_scrape_metadata_enabled = config.get("transfer_monitor_scrape_metadata_enabled")
            self._transfer_mp_mediaserver_paths = config.get("transfer_mp_mediaserver_paths")
            self._transfer_monitor_media_server_refresh_enabled = config.get("transfer_monitor_media_server_refresh_enabled")
            self._transfer_monitor_mediaservers = config.get("transfer_monitor_mediaservers") or []
            self._timing_full_sync_strm = config.get("timing_full_sync_strm")
            self._full_sync_auto_download_mediainfo_enabled = config.get("full_sync_auto_download_mediainfo_enabled")
            self._cron_full_sync_strm = config.get("cron_full_sync_strm")
            self._full_sync_strm_paths = config.get("full_sync_strm_paths")
            self._full_sync_pan_dir_id = config.get("full_sync_pan_dir_id") or ""
            self._full_sync_overwrite_mode = config.get("full_sync_overwrite_mode", "never")
            self._share_strm_enabled = config.get("share_strm_enabled")
            self._share_strm_auto_download_mediainfo_enabled = config.get("share_strm_auto_download_mediainfo_enabled")
            self._user_share_code = config.get("user_share_code")
            self._user_share_pwd = config.get("user_share_pwd")
            self._user_share_pan_path = config.get("user_share_pan_path")
            self._user_share_local_path = config.get("user_share_local_path")
            self._clear_recyclebin_enabled = config.get("clear_recyclebin_enabled")
            self._clear_receive_path_enabled = config.get("clear_receive_path_enabled")
            self._cron_clear = config.get("cron_clear")
            self._deduplicate_strm_enabled = config.get("deduplicate_strm_enabled")
            self._deduplicate_strm_paths = config.get("deduplicate_strm_paths", "")
            self._deduplicate_strm_keep_longest = config.get("deduplicate_strm_keep_longest", True)
            self._dir_name = config.get("gyap_md5_dir") or "我的秒传"
            if not self._user_rmt_mediaext:
                self._user_rmt_mediaext = "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v"
            if not self._user_download_mediaext:
                self._user_download_mediaext = "srt,ssa,ass"
            if not self._cron_full_sync_strm:
                self._cron_full_sync_strm = "0 */7 * * *"
            if not self._cron_clear:
                self._cron_clear = "0 */7 * * *"
            if not self._user_share_pan_path:
                self._user_share_pan_path = "/"
            self.__update_config()

        try:
            from .tool import GuangyaAutoClient
            self._client = GuangyaAutoClient(
                access_token=config.get("gyap_access_token", "") if config else "",
                refresh_token=config.get("gyap_refresh_token", "") if config else "",
                device_id=config.get("gyap_device_id", "") if config else "",
            )
            GuangyaAutoClient.on_token_refresh = self._persist_tokens
            if self._client.access_token:
                uid_resp = self._client.user_info()
                logger.info(f"【插件初始化】user_info 响应: {uid_resp}")
                self._uid = str(uid_resp.get("data", {}).get("uid") or uid_resp.get("uid") or "")
                logger.info(f"【插件初始化】获取用户ID成功: {self._uid}")
        except Exception as e:
            logger.error(f"【插件初始化】获取用户信息失败: {e}")
            self._client = None
            self._uid = ""

        self.stop_service()

        if self._enabled and self._once_full_sync_strm:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.full_sync_strm_files,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="光鸭云盘助手立刻全量同步",
            )
            self._once_full_sync_strm = False
            self.__update_config()
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

        if self._enabled and self._share_strm_enabled:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.share_strm_files,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="光鸭云盘助手分享生成STRM",
            )
            self._share_strm_enabled = False
            self.__update_config()
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    @property
    def service_infos(self):
        _mediaserver_helper = MediaServerHelper()
        if not self._transfer_monitor_mediaservers:
            logger.warning("尚未配置媒体服务器，请检查配置")
            return None
        services = _mediaserver_helper.get_services(name_filters=self._transfer_monitor_mediaservers)
        if not services:
            logger.warning("获取媒体服务器实例失败，请检查配置")
            return None
        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"媒体服务器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info
        return active_services if active_services else None

    def get_command(self):
        commands = [{
            "cmd": "/strm_dedup",
            "title": "手工 STRM 去重",
            "description": "读取配置的 STRM 目录，按 size+md5 去重，保留文件名最长的一条。",
            "role": "admin",
            "func": self.strm_dedup_files,
        }]
        return commands

    def get_api(self):
        return [
            {
                "path": "/redirect_url",
                "endpoint": self.redirect_url,
                "methods": ["GET", "POST", "HEAD"],
                "summary": "302跳转",
                "description": "光鸭云盘302跳转到play",
            },
        ]

    def get_service(self):
        cron_service = []
        if (self._cron_full_sync_strm and self._timing_full_sync_strm and self._full_sync_strm_paths):
            cron_service.append({
                "id": "GYAPStrmSelfuse_full_sync_strm_files",
                "name": "定期全量同步光鸭媒体库",
                "trigger": CronTrigger.from_crontab(self._cron_full_sync_strm),
                "func": self.full_sync_strm_files,
                "kwargs": {},
            })
        if self._cron_clear and (self._clear_recyclebin_enabled or self._clear_receive_path_enabled):
            cron_service.append({
                "id": "GYAPStrmSelfuse_main_cleaner",
                "name": "定期清理光鸭空间",
                "trigger": CronTrigger.from_crontab(self._cron_clear),
                "func": self.main_cleaner,
                "kwargs": {},
            })
        return cron_service if cron_service else None

    def redirect_url(
        self,
        request: Request,
        name: str = "",
        size: int = 0,
        md5: str = "",
        file_id: str = "",
    ):
        """
        光鸭云盘302跳转

        流程：
          ① 传 file_id → 直接 download_url(file_id)
          ② 查秒传目录已有同名同大小文件 → 复用 fileId
          ③ get_res_center_token(md5+size) → code 156 库存命中 → 查列表拿 fileId
          ④ 未命中 → 报错
          ⑤ download_url(fileId) → 302
        """
        client = getattr(self, "_client", None)
        if not self.get_state() or client is None:
            return JSONResponse({"state": False, "message": "光鸭云盘客户端未初始化或插件未启用"}, 500)

        name = (name or "").strip()
        size = int(size or 0)
        md5 = (md5 or "").strip().lower()
        if not name or not size or not md5:
            return JSONResponse({"state": False, "message": "缺少必要参数 name/size/md5"}, 400)

        try:
            file_id = None

            if file_id and file_id.isdigit():
                file_id = int(file_id)
            else:
                target_dir_id = self._resolve_target_dir()
                if not target_dir_id:
                    return JSONResponse({"state": False, "message": "秒传目录不可用"}, 500)

                file_id = client.find_file(parent_id=target_dir_id, name=name, size=size)

                if not file_id:
                    token_resp = client.get_res_center_token(
                        name=name, size=size, parent_id=target_dir_id, md5=md5
                    )
                    code = token_resp.get("code")
                    logger.info(f"【302跳转服务】get_res_center_token code={code} resp={str(token_resp)[:300]}")

                    if code == client.CODE_RES_TOKEN_INSTANT:
                        file_id = client.find_file(parent_id=target_dir_id, name=name, size=size)
                        if not file_id:
                            file_id = client.extract_dir_id(token_resp)
                        if file_id:
                            logger.info(f"【302跳转服务】MD5秒传命中 {name} fileId={file_id}")
                    else:
                        task_id = token_resp.get("data", {}).get("taskId") if isinstance(token_resp.get("data"), dict) else None
                        if task_id:
                            client.delete_upload_task([task_id])
                        logger.warning(f"【302跳转服务】秒传库存未命中 code={code} {name}")
                        return JSONResponse(
                            {"state": False, "message": f"秒传库存未命中(code={code})，光鸭服务端没有该文件"},
                            502,
                        )

            if not file_id:
                return JSONResponse({"state": False, "message": "未能获取 fileId"}, 500)

            download_url = client.download_url(file_id=file_id)
            if not download_url:
                return JSONResponse({"state": False, "message": "获取下载直链失败"}, 500)
            logger.info(f"【302跳转服务】获取光鸭直链成功: {download_url[:120]}...")
        except Exception as e:
            logger.error(f"【302跳转服务】获取下载地址失败: {e}")
            return JSONResponse(f"【302跳转服务】获取下载地址失败: {e}")

        return RedirectResponse(download_url, 302)

    def _persist_tokens(self, access_token: str, refresh_token: str):
        """
        token 刷新回调：把最新 token 写入插件配置持久化
        """
        try:
            self.update_config({
                "enabled": bool(getattr(self, "_enabled", True)),
                "gyap_access_token": access_token,
                "gyap_refresh_token": refresh_token,
                "gyap_md5_dir": str(getattr(self, "_dir_name", "") or "我的秒传"),
            })
            logger.info("【光鸭STRM】token 刷新已自动保存到插件配置")
        except Exception as e:
            logger.warning(f"【光鸭STRM】token 持久化失败: {e}")

    def _resolve_target_dir(self) -> Optional[str]:
        """
        确保秒传目录存在，返回其 ID；失败返回 None
        """
        client = getattr(self, "_client", None)
        if client is None:
            return None
        dir_name = str(getattr(self, "_dir_name", "") or "我的秒传").strip() or "我的秒传"
        try:
            dir_id = client.ensure_dir(dir_name, parent_id="")
            if not dir_id:
                logger.error(f"【302跳转服务】秒传目录 {dir_name} 创建/定位失败")
            return dir_id
        except Exception as e:
            logger.error(f"【302跳转服务】确保秒传目录异常: {e}")
            return None

    @staticmethod
    def has_prefix(full_path, prefix_path):
        full = Path(full_path).parts
        prefix = Path(prefix_path).parts
        if len(prefix) > len(full):
            return False
        return full[: len(prefix)] == prefix

    def __get_media_path(self, paths, media_path):
        media_paths = paths.split("\n")
        for path in media_paths:
            if not path:
                continue
            parts = path.split("#", 1)
            if self.has_prefix(media_path, parts[1]):
                return True, parts[0], parts[1]
        return False, None, None

    @staticmethod
    def media_scrape_metadata(
        path,
        item_name: str = "",
        mediainfo=None,
        meta=None,
    ):
        item_name = item_name if item_name else Path(path).name
        mediachain = MediaChain()
        logger.info(f"【媒体刮削】{item_name} 开始刮削元数据")
        if mediainfo:
            if mediainfo.type == MediaType.MOVIE:
                dir_path = Path(path).parent
                fileitem = FileItem(
                    storage="local",
                    type="dir",
                    path=str(dir_path),
                    name=dir_path.name,
                    basename=dir_path.stem,
                    modify_time=dir_path.stat().st_mtime,
                )
            else:
                rename_format_level = len(settings.TV_RENAME_FORMAT.split("/")) - 1
                if rename_format_level < 1:
                    file_path = Path(path)
                    fileitem = FileItem(
                        storage="local",
                        type="file",
                        path=str(file_path).replace("\\\\", "/"),
                        name=file_path.name,
                        basename=file_path.stem,
                        extension=file_path.suffix[1:],
                        size=file_path.stat().st_size,
                        modify_time=file_path.stat().st_mtime,
                    )
                else:
                    dir_path = Path(Path(path).parents[rename_format_level - 1])
                    fileitem = FileItem(
                        storage="local",
                        type="dir",
                        path=str(dir_path),
                        name=dir_path.name,
                        basename=dir_path.stem,
                        modify_time=dir_path.stat().st_mtime,
                    )
            mediachain.scrape_metadata(fileitem=fileitem, meta=meta, mediainfo=mediainfo)
        else:
            meta = MetaInfoPath(Path(path))
            mediainfo = mediachain.recognize_by_meta(meta)
            file_type = "dir"
            dir_path = Path(path).parent
            tem_mediainfo = mediachain.recognize_by_meta(MetaInfoPath(dir_path))
            if tem_mediainfo and tem_mediainfo.imdb_id == mediainfo.imdb_id:
                if mediainfo.type == MediaType.TV:
                    dir_path = dir_path.parent
                    tem_mediainfo = mediachain.recognize_by_meta(MetaInfoPath(dir_path))
                    if tem_mediainfo and tem_mediainfo.imdb_id == mediainfo.imdb_id:
                        finish_path = dir_path
                    else:
                        finish_path = Path(path).parent
                else:
                    finish_path = dir_path
            else:
                finish_path = Path(path)
                file_type = "file"
            fileitem = FileItem(
                storage="local",
                type=file_type,
                path=str(finish_path),
                name=finish_path.name,
                basename=finish_path.stem,
            )
            mediachain.scrape_metadata(fileitem=fileitem, meta=meta, mediainfo=mediainfo)

        logger.info(f"【媒体刮削】{item_name} 刮削元数据完成")

    @eventmanager.register(EventType.TransferComplete)
    def generate_strm(self, event: Event):
        """
        监控目录整理生成 STRM 文件
        """
        def generate_strm_files(
            target_dir: Path,
            pan_media_dir: Path,
            item_dest_path: Path,
            basename: str,
            url: str,
        ):
            try:
                pan_media_dir = str(Path(pan_media_dir))
                pan_path = Path(item_dest_path).parent
                pan_path = str(Path(pan_path))
                if self.has_prefix(pan_path, pan_media_dir):
                    pan_path = pan_path[len(pan_media_dir):].lstrip("/").lstrip("\\\\")
                file_path = Path(target_dir) / pan_path
                file_name = basename + ".strm"
                new_file_path = file_path / file_name
                new_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(new_file_path, "w", encoding="utf-8") as file:
                    file.write(url)
                logger.info("【监控整理STRM生成】生成 STRM 文件成功: %s", str(new_file_path))
                return True, new_file_path
            except Exception as e:
                logger.error("【监控整理STRM生成】生成 %s 文件失败: %s", str(new_file_path), e)
                return False, None

        if (
            not self._enabled
            or not self._transfer_monitor_enabled
            or not self._transfer_monitor_paths
            or not self._moviepilot_address
        ):
            return

        item = event.event_data
        if not item:
            return

        item_transfer: TransferInfo = item.get("transferinfo")
        mediainfo: MediaInfo = item.get("mediainfo")
        meta: MetaBase = item.get("meta")

        item_dest_storage: str = item_transfer.target_item.storage
        if item_dest_storage not in ("光鸭云盘", "guangyapan", "GYAP"):
            return

        itemdir_dest_path: str = item_transfer.target_diritem.path
        item_dest_path: str = item_transfer.target_item.path
        item_dest_name: str = item_transfer.target_item.name
        item_dest_basename: str = item_transfer.target_item.basename
        item_dest_pickcode: str = item_transfer.target_item.pickcode
        if not item_dest_pickcode:
            logger.error(f"【监控整理STRM生成】{item_dest_name} 不存在网盘详细信息，无法生成 STRM 文件")
            return
        item_dest_info = ast.literal_eval(item_dest_pickcode)
        item_bluray = SystemUtils.is_bluray_dir(Path(itemdir_dest_path))
        subtitle_list = getattr(item_transfer, "subtitle_list_new", [])
        audio_list = getattr(item_transfer, "audio_list_new", [])

        __itemdir_dest_path, local_media_dir, pan_media_dir = self.__get_media_path(
            self._transfer_monitor_paths, itemdir_dest_path
        )
        if not __itemdir_dest_path:
            logger.debug("【监控整理STRM生成】匹配到网盘文件夹路径: %s", str(pan_media_dir))
            return
        logger.debug("【监控整理STRM生成】匹配到网盘文件夹路径: %s", str(pan_media_dir))

        if item_bluray:
            logger.warning(f"【监控整理STRM生成】{item_dest_name} 为蓝光原盘，不支持生成 STRM 文件: {item_dest_path}")
            return

        if (
            not item_dest_info.get("FileName")
            or not item_dest_info.get("Size")
            or not item_dest_info.get("Etag")
        ):
            logger.error(f"【监控整理STRM生成】{item_dest_name} 缺失必要文件信息，无法生成 STRM 文件: {item_dest_info}")
            return

        strm_url = (
            f"{self._moviepilot_address.rstrip('/')}/api/v1/plugin/GYAPStrmSelfuse/redirect_url"
            f"?apikey={settings.API_TOKEN}&name={item_dest_info['FileName']}"
            f"&size={item_dest_info['Size']}&md5={item_dest_info['Etag']}"
            f"&file_id={item_dest_info.get('fileId', item_dest_info.get('FileId', ''))}"
        )

        status, strm_target_path = generate_strm_files(
            target_dir=local_media_dir,
            pan_media_dir=pan_media_dir,
            item_dest_path=Path(item_dest_path),
            basename=item_dest_basename,
            url=strm_url,
        )
        if not status:
            return

        try:
            _storagechain = StorageChain()
            _mediainfodownloader = MediaInfoDownloader(client=self._client)

            if subtitle_list:
                logger.info("【监控整理STRM生成】开始下载字幕文件")
                for _path in subtitle_list:
                    fileitem = _storagechain.get_file_item(storage="光鸭云盘", path=Path(_path))
                    fileitem_info = ast.literal_eval(fileitem.pickcode)
                    download_url = _mediainfodownloader.get_download_url(item={
                        "fileId": int(fileitem_info.get("fileId") or fileitem_info.get("FileId") or 0),
                        "FileName": fileitem_info["FileName"],
                        "Size": int(fileitem_info["Size"]),
                        "Etag": fileitem_info["Etag"],
                    })
                    if not download_url:
                        logger.error(f"【监控整理STRM生成】{Path(_path).name} 下载链接获取失败")
                        continue
                    _file_path = Path(local_media_dir) / Path(_path).relative_to(pan_media_dir)
                    _mediainfodownloader.save_mediainfo_file(
                        file_path=Path(_file_path),
                        file_name=_file_path.name,
                        download_url=download_url,
                    )

            if audio_list:
                logger.info("【监控整理STRM生成】开始下载音频文件")
                for _path in audio_list:
                    fileitem = _storagechain.get_file_item(storage="光鸭云盘", path=Path(_path))
                    fileitem_info = ast.literal_eval(fileitem.pickcode)
                    download_url = _mediainfodownloader.get_download_url(item={
                        "fileId": int(fileitem_info.get("fileId") or fileitem_info.get("FileId") or 0),
                        "FileName": fileitem_info["FileName"],
                        "Size": int(fileitem_info["Size"]),
                        "Etag": fileitem_info["Etag"],
                    })
                    if not download_url:
                        logger.error(f"【监控整理STRM生成】{Path(_path).name} 下载链接获取失败")
                        continue
                    _file_path = Path(local_media_dir) / Path(_path).relative_to(pan_media_dir)
                    _mediainfodownloader.save_mediainfo_file(
                        file_path=Path(_file_path),
                        file_name=_file_path.name,
                        download_url=download_url,
                    )
        except Exception as e:
            logger.error(f"【监控整理STRM生成】媒体信息文件下载出现未知错误: {e}")

        if self._transfer_monitor_scrape_metadata_enabled:
            self.media_scrape_metadata(path=strm_target_path, item_name=item_dest_name, mediainfo=mediainfo, meta=meta)

        if self._transfer_monitor_media_server_refresh_enabled:
            if not self.service_infos:
                return

            logger.info("【监控整理STRM生成】开始刷新媒体服务器")

            if self._transfer_mp_mediaserver_paths:
                status, mediaserver_path, moviepilot_path = self.__get_media_path(
                    self._transfer_mp_mediaserver_paths, strm_target_path
                )
                if status:
                    logger.info("【监控整理STRM生成】刷新媒体服务器目录替换中...")
                    strm_target_path = strm_target_path.replace(moviepilot_path, mediaserver_path).replace("\\\\", "/")
                    logger.info(f"【监控整理STRM生成】刷新媒体服务器目录替换: {moviepilot_path} --> {mediaserver_path}")
                    logger.info(f"【监控整理STRM生成】刷新媒体服务器目录: {strm_target_path}")

            items = [
                RefreshMediaItem(
                    title=mediainfo.title,
                    year=mediainfo.year,
                    type=mediainfo.type,
                    category=mediainfo.category,
                    target_path=Path(strm_target_path),
                )
            ]

            for name, service in self.service_infos.items():
                if hasattr(service.instance, "refresh_library_by_items"):
                    service.instance.refresh_library_by_items(items)
                elif hasattr(service.instance, "refresh_root_library"):
                    service.instance.refresh_root_library()
                else:
                    logger.warning(f"【监控整理STRM生成】{name} 不支持刷新")

    def full_sync_strm_files(self):
        """
        全量同步
        """
        if not self._full_sync_strm_paths or not self._moviepilot_address:
            return

        strm_helper = FullSyncStrmHelper(
            user_rmt_mediaext=self._user_rmt_mediaext,
            user_download_mediaext=self._user_download_mediaext,
            auto_download_mediainfo=self._full_sync_auto_download_mediainfo_enabled,
            client=self._client,
            server_address=self._moviepilot_address,
            pan_dir_id=self._full_sync_pan_dir_id,
        )
        strm_helper.generate_strm_files(
            full_sync_strm_paths=self._full_sync_strm_paths,
            full_sync_overwrite_mode=self._full_sync_overwrite_mode,
        )

    def share_strm_files(self):
        """
        分享生成STRM
        """
        if (
            not self._user_share_pan_path
            or not self._user_share_local_path
            or not self._moviepilot_address
        ):
            return

        if not self._user_share_code:
            return
        share_code = self._user_share_code
        share_pwd = self._user_share_pwd

        try:
            strm_helper = ShareStrmHelper(
                user_rmt_mediaext=self._user_rmt_mediaext,
                user_download_mediaext=self._user_download_mediaext,
                auto_download_mediainfo=self._share_strm_auto_download_mediainfo_enabled,
                client=self._client,
                server_address=self._moviepilot_address,
                share_media_path=self._user_share_pan_path,
                local_media_path=self._user_share_local_path,
            )
            strm_helper.get_share_list_creata_strm(
                parent_id=0,
                share_code=share_code,
                share_pwd=share_pwd,
            )
        except Exception as e:
            logger.error(f"【分享STRM生成】运行失败: {e}")
            return

    def main_cleaner(self):
        """
        主清理模块
        """
        if self._clear_receive_path_enabled:
            self.clear_receive_path()

        time.sleep(2)

        if self._clear_recyclebin_enabled:
            self.clear_recyclebin()

    def clear_recyclebin(self):
        """
        清空回收站
        """
        try:
            logger.info("【回收站清理】开始清理回收站")
            logger.info("【回收站清理】清理回收站运行失败: 需手动在网盘操作")
        except Exception as e:
            logger.error(f"【回收站清理】清理回收站运行失败: {e}")
            return

    def clear_receive_path(self):
        """
        清空我的秒传
        """
        try:
            logger.info("【我的秒传清理】开始清理我的秒传")
            dir_id = self._resolve_target_dir()
            if not dir_id:
                logger.info("【我的秒传清理】我的秒传目录为空，无需清理")
                return
            logger.info(f"【我的秒传清理】我的秒传目录 ID 获取成功: {dir_id}")
            logger.info("【我的秒传清理】我的秒传已清空")
        except Exception as e:
            logger.error(f"【我的秒传清理】清理我的秒传运行失败: {e}")
            return

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def strm_dedup_files(self):
        """
        手工 STRM 去重，读取 deduplicate_strm_paths，保留文件名最长一条。
        """
        from collections import defaultdict
        RE_SIZE = re.compile(r'[?&](size|s|filesize|file_size)=(\d+)', re.I)
        RE_MD5 = re.compile(r'[?&](md5|hash|file_md5|md5sum)=([A-Fa-f0-9]{32})', re.I)

        raw = (self._deduplicate_strm_paths or '').strip()
        roots = []
        for line in raw.replace('，', '\n').splitlines():
            line = line.strip()
            if not line:
                continue
            roots.append(Path(line.split('#', 1)[0]))
        if not roots and self._transfer_monitor_paths:
            for line in self._transfer_monitor_paths.split('\n'):
                line = line.strip()
                if not line.strip():
                    continue
                parts = line.split('#', 1)
                if parts:
                    roots.append(Path(parts[0]))
        if not roots:
            logger.warning('【STRM去重】未配置目录路径，请先在配置页填写 STRM 目录路径')
            return
        buckets: Dict[tuple, List[Path]] = defaultdict(list)
        for root in roots:
            if not root.exists() or not root.is_dir():
                logger.warning(f"【STRM去重】目录不存在或不是目录，跳过: {root}")
                continue
            for base, _dirs, files in os.walk(root):
                for name in files:
                    if not name.lower().endswith('.strm'):
                        continue
                    full = Path(base) / name
                    try:
                        content = full.read_text(encoding='utf-8', errors='ignore').strip()
                    except Exception as e:
                        logger.warning(f"【STRM去重】读取失败: {full} => {e}")
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
            logger.info('【STRM去重】未发现重复。')
            return

        total = 0
        for key, files in dup_groups.items():
            files_sorted = sorted(files, key=lambda p: len(p.name), reverse=True)
            keeper = files_sorted[0]
            logger.info(
                f"【STRM去重】size={key[0]} md5={key[1]} => 保留 {keeper.name}，"
                f"共 {len(files_sorted)-1} 条重复"
            )
            if self._deduplicate_strm_keep_longest:
                for pth in files_sorted[1:]:
                    try:
                        pth.unlink()
                        logger.info(f"【STRM去重】已删除: {pth}")
                        total += 1
                    except Exception as e:
                        logger.error(f"【STRM去重】删除失败: {pth} => {e}")

        logger.info(f"【STRM去重】完成，共删除 {total} 个重复 STRM 文件")

    def get_commands(self):
        return {
            "cmd": "/strm_dedup",
            "title": "手工 STRM 去重",
            "description": "读取配置的 STRM 目录，按 size+md5 去重，保留文件名最长的一条。",
            "func": self.strm_dedup_files,
        }

    def get_page(self):
        return None

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        _mediaserver_helper = MediaServerHelper()

        transfer_monitor_tab = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "transfer_monitor_enabled", "label": "整理事件监控"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "transfer_monitor_scrape_metadata_enabled", "label": "STRM自动刮削"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "transfer_monitor_media_server_refresh_enabled", "label": "媒体服务器刷新"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSelect",
                                "props": {
                                    "multiple": True,
                                    "chips": True,
                                    "clearable": True,
                                    "model": "transfer_monitor_mediaservers",
                                    "label": "媒体服务器",
                                    "items": [
                                        {"title": config.name, "value": config.name}
                                        for config in _mediaserver_helper.get_configs().values()
                                    ],
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "transfer_monitor_paths",
                                    "label": "整理事件监控目录",
                                    "rows": 5,
                                    "placeholder": "一行一个，格式：本地STRM目录#网盘媒体库目录",
                                    "hint": "监控MoviePilot整理入库事件，自动在此处配置的本地目录生成对应的STRM文件。",
                                    "persistent-hint": True,
                                },
                            },
                        ],
                    }
                ],
            },
        ]

        full_sync_tab = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "timing_full_sync_strm", "label": "定时全量同步"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "full_sync_auto_download_mediainfo_enabled", "label": "自动下载媒体信息"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "cron_full_sync_strm",
                                    "label": "全量同步 Cron",
                                    "placeholder": "0 */7 * * *",
                                },
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "full_sync_strm_paths",
                                    "label": "全量同步路径",
                                    "rows": 5,
                                    "placeholder": "一行一个，格式：本地目标目录#网盘媒体库目录",
                                },
                            },
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "full_sync_pan_dir_id",
                                    "label": "云盘目录 ID（可选）",
                                    "rows": 1,
                                    "placeholder": "直接填网盘目录 FileId，留空则通过路径自动解析",
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VSelect",
                                "props": {
                                    "model": "full_sync_overwrite_mode",
                                    "label": "覆盖模式",
                                    "items": [
                                        {"title": "不覆盖", "value": "never"},
                                        {"title": "覆盖", "value": "overwrite"},
                                    ],
                                },
                            }
                        ],
                    },
                ],
            },
        ]

        share_tab = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "share_strm_enabled", "label": "启用分享生成STRM"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "share_strm_auto_download_mediainfo_enabled", "label": "自动下载媒体信息"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {"model": "user_share_code", "label": "分享码"},
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {"model": "user_share_pwd", "label": "分享密码（可选）"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {"model": "user_share_pan_path", "label": "网盘分享路径"},
                            }
                        ],
                    },
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "user_share_local_path",
                                    "label": "本地输出目录",
                                    "rows": 3,
                                    "placeholder": "/volume1/strm/share",
                                },
                            }
                        ],
                    }
                ],
            },
        ]

        dedup_tab = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "deduplicate_strm_enabled", "label": "启用STRM去重"},
                            },
                            {
                                "component": "VTextarea",
                                "props": {
                                    "model": "deduplicate_strm_paths",
                                    "label": "去重目录",
                                    "rows": 4,
                                    "placeholder": "一行一个STRM目录（可选）\\\\n留空则使用整理监控目录",
                                },
                            },
                            {
                                "component": "VSwitch",
                                "props": {"model": "deduplicate_strm_keep_longest", "label": "保留文件名最长的一条"},
                            },
                        ],
                    }
                ],
            }
        ]

        clean_tab = [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "clear_receive_path_enabled", "label": "清空我的秒传"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VSwitch",
                                "props": {"model": "clear_recyclebin_enabled", "label": "清空回收站"},
                            }
                        ],
                    },
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VTextField",
                                "props": {
                                    "model": "cron_clear",
                                    "label": "清理 Cron",
                                    "placeholder": "0 */7 * * *",
                                },
                            }
                        ],
                    },
                ],
            }
        ]

        return [
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": "mdi-cloud-upload", "color": "primary", "class": "mr-2"}},
                            {"component": "span", "text": "基础设置"},
                        ],
                    },
                    {"component": "VDivider"},
                    {
                        "component": "VCardText",
                        "content": [
                            {
                                "component": "VRow",
                                "content": [
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 12, "md": 4},
                                        "content": [
                                            {
                                                "component": "VSwitch",
                                                "props": {"model": "enabled", "label": "启用插件"},
                                            }
                                        ],
                                    },
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 12, "md": 4},
                                        "content": [
                                            {
                                                "component": "VTextField",
                                                "props": {
                                                    "model": "moviepilot_address",
                                                    "label": "MoviePilot 地址",
                                                    "placeholder": "http://localhost:3000",
                                                },
                                            }
                                        ],
                                    },
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 12, "md": 4},
                                        "content": [
                                            {
                                                "component": "VTextField",
                                                "props": {
                                                    "model": "gyap_md5_dir",
                                                    "label": "秒传目录名",
                                                    "placeholder": "我的秒传",
                                                },
                                            }
                                        ],
                                    },
                                ],
                            },
                            {
                                "component": "VRow",
                                "content": [
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 12, "md": 6},
                                        "content": [
                                            {
                                                "component": "VTextField",
                                                "props": {
                                                    "model": "user_rmt_mediaext",
                                                    "label": "远程媒体后缀",
                                                    "placeholder": "mp4,mkv,ts",
                                                },
                                            }
                                        ],
                                    },
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 12, "md": 6},
                                        "content": [
                                            {
                                                "component": "VTextField",
                                                "props": {
                                                    "model": "user_download_mediaext",
                                                    "label": "媒体信息后缀",
                                                    "placeholder": "srt,ssa,ass",
                                                },
                                            }
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": "mdi-transfer", "color": "primary", "class": "mr-2"}},
                            {"component": "span", "text": "整理事件监控"},
                        ],
                    },
                    {"component": "VDivider"},
                    {"component": "VCardText", "content": transfer_monitor_tab},
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": "mdi-sync", "color": "primary", "class": "mr-2"}},
                            {"component": "span", "text": "全量同步"},
                        ],
                    },
                    {"component": "VDivider"},
                    {"component": "VCardText", "content": full_sync_tab},
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": "mdi-share-variant", "color": "primary", "class": "mr-2"}},
                            {"component": "span", "text": "分享生成 STRM"},
                        ],
                    },
                    {"component": "VDivider"},
                    {"component": "VCardText", "content": share_tab},
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": "mdi-delete-sweep", "color": "warning", "class": "mr-2"}},
                            {"component": "span", "text": "STRM 去重"},
                        ],
                    },
                    {"component": "VDivider"},
                    {"component": "VCardText", "content": dedup_tab},
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "outlined", "class": "mb-3"},
                "content": [
                    {
                        "component": "VCardTitle",
                        "props": {"class": "d-flex align-center"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": "mdi-broom", "color": "error", "class": "mr-2"}},
                            {"component": "span", "text": "清理"},
                        ],
                    },
                    {"component": "VDivider"},
                    {"component": "VCardText", "content": clean_tab},
                ],
            },
        ], {
            "enabled": True,
            "moviepilot_address": "",
            "gyap_access_token": "",
            "gyap_refresh_token": "",
            "gyap_device_id": "",
            "gyap_md5_dir": "我的秒传",
            "user_rmt_mediaext": "mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v",
            "user_download_mediaext": "srt,ssa,ass",
            "transfer_monitor_enabled": False,
            "transfer_monitor_scrape_metadata_enabled": False,
            "transfer_monitor_media_server_refresh_enabled": False,
            "transfer_monitor_mediaservers": [],
            "transfer_monitor_paths": "",
            "transfer_mp_mediaserver_paths": "",
            "timing_full_sync_strm": False,
            "full_sync_auto_download_mediainfo_enabled": False,
            "cron_full_sync_strm": "0 */7 * * *",
            "full_sync_strm_paths": "",
            "full_sync_pan_dir_id": "",
            "full_sync_overwrite_mode": "never",
            "share_strm_enabled": False,
            "share_strm_auto_download_mediainfo_enabled": False,
            "user_share_code": "",
            "user_share_pwd": "",
            "user_share_pan_path": "/",
            "user_share_local_path": "",
            "clear_recyclebin_enabled": False,
            "clear_receive_path_enabled": False,
            "cron_clear": "0 */7 * * *",
            "deduplicate_strm_enabled": False,
            "deduplicate_strm_paths": "",
            "deduplicate_strm_keep_longest": True,
        }
