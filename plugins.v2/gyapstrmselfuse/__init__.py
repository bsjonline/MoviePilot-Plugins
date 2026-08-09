"""
光鸭云盘 STRM 助手

基于 MD5 秒传获取 file_id，再获取下载地址 302 播放。
不依赖 file_id 持久化，STRM 中只保留 md5。
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.plugins import _PluginBase
from app.utils.string import StringUtils
from app.helper.dict import DictHelper
from app.schemas import MediaInfo
from app.helper.mdc import MDC
from app.helper.media import MediaHelper
from app.log import logger
from tool.tool import GuangyaAutoClient

from app.core.config import settings
from app.helper.web import WebHelper
from app.helper.site import SiteHelper


class GYAPStrmSelfuse(_PluginBase):
    """
    光鸭云盘 STRM 助手
    """

    # 插件名称
    plugin_name = "gyapstrmselfuse"
    # 插件描述
    plugin_desc = "光鸭云盘 STRM 助手，基于 MD5 秒传播放，尽量不用转存"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/bsjonline/MoviePilot-Plugins/main/icons/P123Disk.png"
    # 插件版本
    plugin_version = "0.1.0"
    # 插件作者
    plugin_author = "bsjonline"
    # 作者主页
    plugin_url = "https://github.com/bsjonline/MoviePilot-Plugins"
    # 插件配置项
    plugin_config = {
        "gyap_access_token": {
            "desc": "光鸭云盘 Access Token",
            "type": "string",
            "value": "",
        },
        "gyap_md5_dir": {
            "desc": "MD5 秒传目录（留空使用默认目录）",
            "type": "string",
            "value": "",
        },
        "gyap_clear_receive_path": {
            "desc": "清空秒传目录（危险操作）",
            "type": "string",
            "value": "no",
            "options": ["yes", "no"],
        },
        "gyap_enabled": {
            "desc": "启用插件",
            "type": "string",
            "value": "yes",
            "options": ["yes", "no"],
        },
    }
    # 插件系统配置
    system_version = ">=2.13.0"

    def init_plugin(self, config: dict):
        """
        插件初始化
        """
        if not config:
            return

        self._enabled = config.get("gyap_enabled", "yes") == "yes"
        if not self._enabled:
            logger.info("【光鸭STRM】插件未启用")
            return

        access_token = config.get("gyap_access_token", "").strip()
        if not access_token:
            logger.error("【光鸭STRM】请配置光鸭云盘 Access Token")
            self._enabled = False
            return

        try:
            self._client = GuangyaAutoClient(access_token=access_token)
            logger.info("【光鸭STRM】光鸭云盘客户端初始化成功")
        except Exception as e:
            logger.error(f"【光鸭STRM】光鸭云盘客户端创建失败: {e}")
            self._client = None

    def get_state(self) -> bool:
        """
        插件状态
        """
        return self._enabled and self._client is not None

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        返回插件远程命令列表
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        返回插件 API 路由

        BASE_URL: {server_url}/api/v1/plugin/GYAPStrmSelfuse/redirect_url?apikey={APIKEY}&name={name}&size={size}&md5={md5}
        """
        return [
            {
                "path": "/redirect_url",
                "endpoint": self.redirect_url,
                "methods": ["GET", "POST", "HEAD"],
                "summary": "302跳转",
                "description": "光鸭云盘302跳转到下载地址",
            },
            {
                "path": "/play/{md5}/{name}",
                "endpoint": self.play,
                "methods": ["GET", "HEAD"],
                "summary": "播放跳转",
                "description": "光鸭云盘播放302跳转",
            },
        ]

    def redirect_url(
        self,
        request: Request,
        name: str = "",
        size: int = 0,
        md5: str = "",
    ):
        """
        光鸭云盘302跳转

        流程：upload_token(md5) -> upload_info(task_id) -> download_url(file_id) -> 302
        """
        if not self._client:
            return JSONResponse({"state": False, "message": "光鸭云盘客户端未初始化"}, 500)

        try:
            file_id = None
            download_url = None

            if md5:
                # 通过 MD5 秒传获取 file_id
                logger.info(f"【302跳转服务】尝试通过 MD5 秒传: {md5}")
                try:
                    token_resp = self._client.upload_token(
                        name=name,
                        file_size=int(size) if size else 0,
                        parent_id="",
                        md5=md5,
                    )
                    task_id = token_resp.get("data", {}).get("taskId")
                    if task_id:
                        info_resp = self._client.upload_info(task_id=task_id)
                        file_info = info_resp.get("data") or info_resp
                        file_id = (
                            file_info.get("fileId")
                            or file_info.get("file_id")
                            or file_info.get("FileId")
                        )
                        if file_id:
                            logger.info(
                                f"【302跳转服务】MD5 秒传获取 file_id 成功: {file_id}"
                            )
                except Exception as e:
                    logger.warning(f"【302跳转服务】MD5 秒传失败: {e}")

            if not file_id:
                return JSONResponse(
                    {"state": False, "message": "无法获取文件下载地址"}, 500
                )

            user_agent = request.headers.get("User-Agent") or ""
            resp = self._client.download_url(
                file_id=file_id,
            )
            download_url = resp.get("data", {}).get("downloadUrl") or resp.get("data", {}).get("DownloadUrl")
            if not download_url:
                return JSONResponse(
                    {"state": False, "message": f"获取下载地址失败: {resp}"}, 500
                )
            logger.info(f"【302跳转服务】获取光鸭下载地址成功: {download_url}")
        except Exception as e:
            logger.error(f"【302跳转服务】获取下载地址失败: {e}")
            return JSONResponse(f"【302跳转服务】获取下载地址失败: {e}")

        return RedirectResponse(download_url, 302)

    def play(
        self,
        request: Request,
        md5: str = "",
        name: str = "",
    ):
        """
        光鸭云盘播放跳转

        直接复用 redirect_url 逻辑
        """
        return self.redirect_url(request=request, name=name, size=0, md5=md5)

    def get_service(self) -> Optional[List[Dict[str, Any]]]:
        """
        注册后台服务
        """
        return []

    def get_form(self) -> List[Dict[str, Any]]:
        """
        返回插件配置表单
        """
        return []

    def get_page(self) -> List[Dict[str, Any]]:
        """
        返回插件页面
        """
        return []

    def get_media_servers(self) -> List[Dict[str, Any]]:
        """
        获取媒体服务器列表
        """
        active_services = MDC().media_servers or []
        if not active_services:
            logger.warning("【光鸭STRM】没有已连接的媒体服务器")
        return active_services
