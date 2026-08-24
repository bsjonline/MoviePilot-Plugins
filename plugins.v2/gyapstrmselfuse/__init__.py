"""
光鸭云盘 STRM 助手

基于 MD5 秒传（光鸭服务端库存查询）获取 file_id，再取签名直链 302 播放。
STRM 中不持久化 file_id，只保留 md5+size+name，fileId 播放时临时解析。
"""

from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.plugins import _PluginBase
from app.log import logger

from .tool import GuangyaAutoClient


class GYAPStrmSelfuse(_PluginBase):
    """
    光鸭云盘 STRM 助手
    """

    # 插件名称
    plugin_name = "gyapstrmselfuse"
    # 插件描述
    plugin_desc = "光鸭云盘 STRM 助手，MD5秒传播放，STRM不含file_id"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/bsjonline/MoviePilot-Plugins/main/icons/P123Disk.png"
    # 插件版本
    plugin_version = "0.2.1"
    # 插件作者
    plugin_author = "bsjonline"
    # 作者主页
    plugin_url = "https://github.com/bsjonline/MoviePilot-Plugins"
    # 插件配置项
    plugin_config = {
        "gyap_access_token": {
            "desc": "光鸭云盘 Access Token（Bearer，不带 Bearer 前缀）",
            "type": "string",
            "value": "",
        },
        "gyap_device_id": {
            "desc": "设备 ID did（可选）",
            "type": "string",
            "value": "",
        },
        "gyap_md5_dir": {
            "desc": "秒传目录名（网盘根目录下自动创建，默认 我的秒传）",
            "type": "string",
            "value": "我的秒传",
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
        self._enabled = (config or {}).get("gyap_enabled", "yes") == "yes"
        if not config:
            return
        if not self._enabled:
            logger.info("【光鸭STRM】插件未启用")
            self._client = None
            return

        access_token = str(config.get("gyap_access_token", "") or "").strip()
        device_id = str(config.get("gyap_device_id", "") or "").strip()
        if not access_token:
            logger.error("【光鸭STRM】请配置光鸭云盘 Access Token")
            self._client = None
            return

        try:
            self._client = GuangyaAutoClient(access_token=access_token, device_id=device_id)
            logger.info("【光鸭STRM】光鸭云盘客户端初始化成功")
        except Exception as e:
            logger.error(f"【光鸭STRM】光鸭云盘客户端创建失败: {e}")
            self._client = None

    def get_state(self) -> bool:
        """
        插件状态
        """
        return bool(getattr(self, "_enabled", False) and getattr(self, "_client", None))

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
                "description": "光鸭云盘302跳转到签名直链",
            },
        ]

    def _resolve_target_dir(self) -> Optional[str]:
        """
        确保秒传目录存在，返回其 ID；失败返回 None
        """
        dir_name = str(getattr(self, "_dir_name", "") or "我的秒传").strip() or "我的秒传"
        try:
            dir_id = client.ensure_dir(dir_name, parent_id="")
            if not dir_id:
                logger.error(f"【302跳转服务】秒传目录 {dir_name} 创建/定位失败")
            return dir_id
        except Exception as e:
            logger.error(f"【302跳转服务】确保秒传目录异常: {e}")
            return None

    def redirect_url(
        self,
        request: Request,
        name: str = "",
        size: int = 0,
        md5: str = "",
    ):
        """
        光鸭云盘302跳转

        流程：
          ① 查秒传目录已有文件 → 命中直接拿 fileId
          ② get_res_center_token(md5+size) → code 156 库存命中 → 再查列表拿 fileId
          ③ 未命中 → 报错（无本地文件无法实际上传）
          ④ download_url(fileId) → 302
        """
        client = getattr(self, "_client", None)
        if not self.get_state() or client is None:
            return JSONResponse({"state": False, "message": "光鸭云盘客户端未初始化或插件未启用"}, 500)

        name = (name or "").strip()
        size = int(size or 0)
        md5 = (md5 or "").strip().lower()
        if not name or not size or not md5:
            return JSONResponse(
                {"state": False, "message": "缺少必要参数 name/size/md5"}, 400
            )

        try:
            file_id = None

            # Step 0: 确保秒传目录存在
            target_dir_id = self._resolve_target_dir()
            if not target_dir_id:
                return JSONResponse({"state": False, "message": "秒传目录不可用"}, 500)

            # Step 1: 目录里已有同名同大小文件 → 直接复用
            file_id = client.find_file(parent_id=target_dir_id, name=name, size=size)
            if file_id:
                logger.info(f"【302跳转服务】秒传目录已存在 {name}，复用 fileId={file_id}")

            # Step 2: 秒传库存查询（code 156 = 命中）
            if not file_id:
                token_resp = client.get_res_center_token(
                    name=name, size=size, parent_id=target_dir_id, md5=md5
                )
                code = token_resp.get("code")
                logger.info(f"【302跳转服务】get_res_center_token code={code} resp={str(token_resp)[:300]}")

                if code == client.CODE_RES_TOKEN_INSTANT:
                    # 秒传命中：文件已进入网盘，查列表拿 fileId
                    file_id = client.find_file(parent_id=target_dir_id, name=name, size=size)
                    if not file_id:
                        # 兜底：从响应中递归抽取 fileId
                        file_id = client.extract_dir_id(token_resp)
                    if file_id:
                        logger.info(f"【302跳转服务】MD5秒传命中 {name} fileId={file_id}")
                else:
                    # 清理未命中的上传任务，避免残留
                    task_id = token_resp.get("data", {}) .get("taskId") if isinstance(token_resp.get("data"), dict) else None
                    if task_id:
                        client.delete_upload_task([task_id])
                    logger.warning(f"【302跳转服务】秒传库存未命中 code={code} {name}")
                    return JSONResponse(
                        {"state": False, "message": f"秒传库存未命中(code={code})，光鸭服务端没有该文件"},
                        502,
                    )

            if not file_id:
                return JSONResponse({"state": False, "message": "未能获取 fileId"}, 500)

            # Step 3: 取签名直链并 302
            download_url = client.download_url(file_id=file_id)
            if not download_url:
                return JSONResponse({"state": False, "message": "获取下载直链失败"}, 500)
            logger.info(f"【302跳转服务】获取光鸭直链成功: {download_url[:120]}...")
        except Exception as e:
            logger.error(f"【302跳转服务】获取下载地址失败: {e}")
            return JSONResponse(f"【302跳转服务】获取下载地址失败: {e}")

        return RedirectResponse(download_url, 302)

    def get_service(self) -> Optional[List[Dict[str, Any]]]:
        """
        注册后台服务
        """
        return []

    def get_form(self) -> List[Dict[str, Any]]:
        """
        返回插件配置表单
        """
        return [
            {
                "component": "VForm",
                "content": [
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
                                            "model": "gyap_access_token",
                                            "label": "Access Token",
                                            "placeholder": "Bearer 后面的部分",
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
                                            "model": "gyap_device_id",
                                            "label": "设备ID did（可选）",
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
                                            "model": "gyap_md5_dir",
                                            "label": "秒传目录名",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "gyap_enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ]

    def get_page(self) -> List[Dict[str, Any]]:
        """
        返回插件页面
        """
        return []

    def stop_service(self):
        pass
