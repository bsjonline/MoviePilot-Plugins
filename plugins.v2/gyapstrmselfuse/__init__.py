"""
光鸭云盘 STRM 助手

基于 MD5 秒传（光鸭服务端库存查询）获取 file_id，再取签名直链 302 播放。
STRM 中不持久化 file_id，只保留 md5+size+name，fileId 播放时临时解析。
"""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.plugins import _PluginBase
from app.log import logger
from app.core.event import eventmanager, Event
from app.schemas.types import EventType, MessageType

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
    plugin_version = "0.3.1"
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
    }
    # 插件系统配置
    system_version = ">=2.13.0"

    def init_plugin(self, config: dict):
        """
        插件初始化
        """
        self._enabled = bool((config or {}).get("enabled", True))
        if not config:
            return
        if not self._enabled:
            logger.info("【光鸭STRM】插件未启用")
            self._client = None
            return

        access_token = str(config.get("gyap_access_token", "") or "").strip()
        refresh_token_cfg = str(config.get("gyap_refresh_token", "") or "").strip()
        device_id = str(config.get("gyap_device_id", "") or "").strip()
        token_source = "本插件配置"

        # 留空时自动从 GuangyaDisk 插件读取登录态（access_token/refresh_token/device_id）
        if not access_token:
            gyd_config = None
            try:
                gyd_config = self.get_config("GuangyaDisk")
            except Exception as e:
                logger.warning(f"【光鸭STRM】读取 GuangyaDisk 配置失败: {e}")
            if isinstance(gyd_config, dict):
                gyd_token = str(gyd_config.get("access_token", "") or "").strip()
                gyd_refresh = str(gyd_config.get("refresh_token", "") or "").strip()
                gyd_did = str(gyd_config.get("device_id", "") or "").strip()
                if gyd_token:
                    access_token = gyd_token
                    token_source = "GuangyaDisk插件"
                    if not device_id and gyd_did:
                        device_id = gyd_did
                    if not refresh_token_cfg and gyd_refresh:
                        refresh_token_cfg = gyd_refresh
                    logger.info(
                        f"【光鸭STRM】已从 GuangyaDisk 插件获取登录态: has_access={bool(gyd_token)}, has_refresh={bool(gyd_refresh)}, did={bool(device_id)}"
                    )

        if not access_token and not refresh_token_cfg:
            # 无登录态：仍创建空客户端供短信登录使用，业务接口会校验token
            logger.warning("【光鸭STRM】暂无登录态，请调用 /login_sms_init + /login_sms_verify 短信登录")
            try:
                self._client = GuangyaAutoClient(
                    access_token="",
                    device_id=device_id,
                    refresh_token="",
                )
                self._client.on_token_refresh = self._persist_tokens
            except Exception as e:
                logger.error(f"【光鸭STRM】光鸭云盘客户端创建失败: {e}")
                self._client = None
            return

        try:
            self._client = GuangyaAutoClient(
                access_token=access_token,
                device_id=device_id,
                refresh_token=refresh_token_cfg,
            )
            # 绑定 token 刷新持久化回调（实例级）
            self._client.on_token_refresh = self._persist_tokens
            logger.info(f"【光鸭STRM】光鸭云盘客户端初始化成功 (token来源: {token_source})")
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
        返回插件远程命令列表：短信登录发码/验证
        """
        return [
            {
                "cmd": "/gyap_send_code",
                "event": EventType.PluginAction,
                "desc": "光鸭云盘短信登录：发送验证码",
                "category": "插件命令",
                "data": {"action": "gyap_send_code"},
            },
            {
                "cmd": "/gyap_verify_code",
                "event": EventType.PluginAction,
                "desc": "光鸭云盘短信登录：验证并登录",
                "category": "插件命令",
                "data": {"action": "gyap_verify_code"},
            },
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_sms_command(self, event: Event):
        """
        处理短信登录按钮点击：读取表单里的手机号/验证码执行对应步骤，结果通过系统消息通知
        """
        if not event or not event.event_data:
            return
        event_data = event.event_data
        action = event_data.get("action") or ""
        if action == "gyap_send_code":
            self._command_send_code()
        elif action == "gyap_verify_code":
            self._command_verify_code()

    def _form_value(self, key: str) -> str:
        """
        从当前插件配置读表单值（配置页保存后即可读到最新输入）
        """
        try:
            cfg = self.get_config() or {}
            return str(cfg.get(key, "") or "").strip()
        except Exception:
            return ""

    def _command_send_code(self):
        client = getattr(self, "_client", None)
        if not self.get_state() or client is None:
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text="插件未启用或客户端未初始化")
            return
        phone = self._form_value("gyap_sms_phone")
        if not phone:
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text="请先在配置页填写手机号并保存")
            return
        if not phone.startswith("+"):
            phone = f"+86 {phone}"
        try:
            init_resp = client.login_sms_init(phone)
            captcha_token = init_resp.get("captcha_token") or ""
            if not captcha_token:
                self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"人机验证失败: {init_resp}")
                return
            send_resp = client.login_sms_send(phone, captcha_token)
            verification_id = send_resp.get("verification_id") or ""
            if not verification_id:
                self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"发送验证码失败: {send_resp}")
                return
            # 会话上下文暂存到插件数据（跨请求存活）
            self.save_data("gyap_login_ctx", {
                "phone": phone,
                "captcha_token": captcha_token,
                "verification_id": verification_id,
            })
            logger.info(f"【光鸭STRM】短信验证码已发送: {phone}")
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"验证码已发送到 {phone}，收到后填入验证码点【验证并登录】")
        except Exception as e:
            logger.error(f"【光鸭STRM】短信登录发码失败: {e}")
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"发码失败: {e}")

    def _command_verify_code(self):
        client = getattr(self, "_client", None)
        if not self.get_state() or client is None:
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text="插件未启用或客户端未初始化")
            return
        ctx = None
        try:
            ctx = self.get_data("gyap_login_ctx")
        except Exception:
            pass
        if not ctx or not ctx.get("verification_id"):
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text="请先点【发送验证码】")
            return
        code = self._form_value("gyap_sms_code")
        if not code:
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text="请先在配置页填写短信验证码并保存")
            return
        try:
            verify_resp = client.login_sms_verify(ctx["verification_id"], code)
            verification_token = verify_resp.get("verification_token") or ""
            if not verification_token:
                self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"验证码校验失败: {verify_resp}")
                return
            signin_resp = client.login_sms_signin(
                code=code,
                verification_token=verification_token,
                phone_number=ctx["phone"],
                captcha_token=ctx["captcha_token"],
            )
            if not signin_resp.get("access_token"):
                self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"登录失败: {signin_resp}")
                return
            # 清理会话上下文；token 已由刷新回调自动持久化
            try:
                self.del_data("gyap_login_ctx")
            except Exception:
                pass
            logger.info(f"【光鸭STRM】短信登录成功，token 已持久化 (phone={ctx['phone']})")
            self.post_message(
                mtype=MessageType.Plugin,
                title="光鸭云盘STRM",
                text=f"登录成功！token 已保存（有效期{signin_resp.get('expires_in', '?')}秒），后续自动刷新续期",
            )
        except Exception as e:
            logger.error(f"【光鸭STRM】短信登录失败: {e}")
            self.post_message(mtype=MessageType.Plugin, title="光鸭云盘STRM", text=f"登录失败: {e}")

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
            {
                "path": "/login_sms_init",
                "endpoint": self.login_sms_init,
                "methods": ["GET", "POST"],
                "auth": "bear",
                "summary": "短信登录-发码",
                "description": "初始化人机验证并发送短信验证码",
            },
            {
                "path": "/login_sms_verify",
                "endpoint": self.login_sms_verify,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "短信登录-验证",
                "description": "校验短信验证码并完成登录，持久化 token",
            },
        ]

    # ---------- 短信登录（TgtoDrive 同款：手机号验证码） ----------

    def login_sms_init(
        self,
        phone_number: str = "",
    ):
        """
        短信登录第一步：init captcha + 发送验证码
        """
        client = getattr(self, "_client", None)
        if not self.get_state() or client is None:
            return JSONResponse({"state": False, "message": "插件未启用或客户端未初始化"}, 500)
        phone = (phone_number or "").strip()
        if not phone:
            return JSONResponse({"state": False, "message": "缺少 phone_number"}, 400)
        try:
            init_resp = client.login_sms_init(phone)
            captcha_token = init_resp.get("captcha_token") or ""
            if not captcha_token:
                return JSONResponse({"state": False, "message": f"captcha init 失败: {init_resp}"}, 502)
            send_resp = client.login_sms_send(phone, captcha_token)
            verification_id = send_resp.get("verification_id") or ""
            if not verification_id:
                return JSONResponse({"state": False, "message": f"发送验证码失败: {send_resp}"}, 502)
            # 暂存登录会话上下文
            self._login_ctx = {
                "phone": phone,
                "captcha_token": captcha_token,
                "verification_id": verification_id,
            }
            logger.info(f"【光鸭STRM】短信验证码已发送: {phone}")
            return JSONResponse({"state": True, "message": "验证码已发送"})
        except Exception as e:
            logger.error(f"【光鸭STRM】短信登录发码失败: {e}")
            return JSONResponse({"state": False, "message": f"发码失败: {e}"}, 500)

    def login_sms_verify(
        self,
        code: str = "",
    ):
        """
        短信登录第二步：校验验证码完成登录，token 自动持久化
        """
        client = getattr(self, "_client", None)
        if not self.get_state() or client is None:
            return JSONResponse({"state": False, "message": "插件未启用或客户端未初始化"}, 500)
        ctx = getattr(self, "_login_ctx", None) or {}
        if not ctx.get("verification_id"):
            return JSONResponse({"state": False, "message": "请先调用 login_sms_init 发送验证码"}, 400)
        sms_code = (code or "").strip()
        if not sms_code:
            return JSONResponse({"state": False, "message": "缺少 code"}, 400)
        try:
            verify_resp = client.login_sms_verify(ctx["verification_id"], sms_code)
            verification_token = verify_resp.get("verification_token") or ""
            if not verification_token:
                return JSONResponse({"state": False, "message": f"验证码校验失败: {verify_resp}"}, 502)
            signin_resp = client.login_sms_signin(
                code=sms_code,
                verification_token=verification_token,
                phone_number=ctx["phone"],
                captcha_token=ctx["captcha_token"],
            )
            if not signin_resp.get("access_token"):
                return JSONResponse({"state": False, "message": f"登录失败: {signin_resp}"}, 502)
            # 持久化新 token 到插件配置（回调已在 _apply_tokens 触发）
            self._login_ctx = None
            logger.info(f"【光鸭STRM】短信登录成功，token 已持久化 (phone={ctx['phone']})")
            return JSONResponse({
                "state": True,
                "message": "登录成功，token 已保存",
                "expires_in": signin_resp.get("expires_in"),
            })
        except Exception as e:
            logger.error(f"【光鸭STRM】短信登录失败: {e}")
            return JSONResponse({"state": False, "message": f"登录失败: {e}"}, 500)

    def _persist_tokens(self, access_token: str, refresh_token: str):
        """
        token 刷新回调：把最新 token 写入插件配置持久化
        """
        try:
            self.update_config({
                "enabled": bool(getattr(self, "_enabled", True)),
                "gyap_access_token": access_token,
                "gyap_refresh_token": refresh_token,
                "gyap_device_id": str(getattr(self, "_device_id", "") or ""),
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

    def get_form(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
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
                                            "label": "Access Token（留空自动读取GuangyaDisk）",
                                            "placeholder": "留空则从 GuangyaDisk 插件读取登录态",
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
                                            "model": "gyap_refresh_token",
                                            "label": "Refresh Token（留空自动读取）",
                                            "placeholder": "短信登录后自动保存",
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
                                            "placeholder": "默认 我的秒传",
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
                                            "model": "enabled",
                                            "label": "启用插件",
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
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "density": "compact",
                                            "class": "mb-2",
                                        },
                                        "content": [
                                            {
                                                "component": "div",
                                                "text": "短信登录（TgtoDrive同款）：填手机号→点【发送验证码】→收到短信后填验证码→点【验证并登录】。登录成功后 token 自动保存，后续自动刷新续期，无需再手动维护。",
                                            },
                                        ],
                                    },
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "gyap_sms_phone",
                                            "label": "手机号（含+86）",
                                            "placeholder": "+86 13800138000",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 2},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "color": "primary",
                                            "variant": "flat",
                                            "block": True,
                                        },
                                        "events": {
                                            "click": "/gyap_send_code"
                                        },
                                        "content": [
                                            {
                                                "component": "span",
                                                "text": "发送验证码",
                                            }
                                        ],
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
                                            "model": "gyap_sms_code",
                                            "label": "短信验证码",
                                            "placeholder": "6位数字",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "color": "success",
                                            "variant": "flat",
                                            "block": True,
                                        },
                                        "events": {
                                            "click": "/gyap_verify_code"
                                        },
                                        "content": [
                                            {
                                                "component": "span",
                                                "text": "验证并登录",
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": True,
            "gyap_access_token": "",
            "gyap_refresh_token": "",
            "gyap_device_id": "",
            "gyap_md5_dir": "我的秒传",
            "gyap_sms_phone": "",
            "gyap_sms_code": "",
        }

    def get_page(self) -> List[Dict[str, Any]]:
        """
        返回插件页面
        """
        return []

    def stop_service(self):
        pass
