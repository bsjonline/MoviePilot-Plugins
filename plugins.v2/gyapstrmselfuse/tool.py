import time
from secrets import token_hex
from typing import Any, Callable, Dict, List, Optional

import httpx


class GuangyaAutoClient:
    """
    光鸭云盘客户端（纯服务端，Bearer token 认证）

    业务接口走 api.guangyapan.com/nd.bizuserres.s 域名，
    与光鸭批量助手V4油猴脚本一致。

    登录采用 TgtoDrive 同款手机号短信验证码方案：
      login_sms_init → login_sms_send → (用户收码) → login_sms_verify → login_sms_signin
    登录成功后持久化 access_token/refresh_token，请求层自动刷新续期。
    """

    BASE = "https://api.guangyapan.com"
    ACCOUNT_BASE = "https://account.guangyapan.com"
    CLIENT_ID = "aMe-8VSlkrbQXpUR"
    PATH_LIST = "/nd.bizuserres.s/v1/file/get_file_list"
    PATH_CREATE_DIR = "/nd.bizuserres.s/v1/file/create_dir"
    PATH_RES_TOKEN = "/nd.bizuserres.s/v1/get_res_center_token"
    PATH_CHECK_FLASH = "/nd.bizuserres.s/v1/check_can_flash_upload"
    PATH_DELETE_TASK = "/nd.bizuserres.s/v1/file/delete_upload_task"
    PATH_DOWNLOAD = "/nd.bizuserres.s/v1/get_res_download_url"

    # 业务码
    CODE_OK = 0
    CODE_RES_TOKEN_INSTANT = 156  # 秒传命中，文件已直接进入网盘
    CODE_DIR_EXISTS = 159  # 目录已存在

    # token 刷新回调（由插件层注入，用于持久化新 token）
    on_token_refresh: Optional[Callable[[str, str], None]] = None

    def __init__(
        self,
        access_token: str = "",
        device_id: str = "",
        refresh_token: str = "",
    ):
        self._access_token = (access_token or "").strip()
        self._refresh_token = (refresh_token or "").strip()
        self._device_id = (device_id or "").strip() or token_hex(16)
        self._token_expires_at: Optional[float] = None

    @property
    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=utf-8",
            "Authorization": f"Bearer {self._access_token}",
            "Platform": "web",
            "dt": "4",
        }
        if self._device_id:
            headers["did"] = self._device_id
        return headers

    def _account_headers(self) -> Dict[str, str]:
        """
        account.guangyapan.com 认证域请求头（对齐 guangyaclient/TgtoDrive）
        """
        return {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "x-client-id": self.CLIENT_ID,
            "x-client-version": "0.0.1",
            "x-device-id": self._device_id,
            "x-device-model": "chrome%2F131.0.0.0",
            "x-device-name": "PC-Chrome",
            "x-device-sign": f"wdi10.{self._device_id}{token_hex(16)}",
            "x-net-work-type": "NONE",
            "x-os-version": "Windows NT 10.0",
            "x-platform-version": "1",
            "x-protocol-version": "301",
            "x-provider-name": "NONE",
            "x-sdk-version": "9.0.2",
        }

    def _account_post(self, path: str, payload: Dict[str, Any], captcha_token: str = "") -> Dict[str, Any]:
        headers = self._account_headers()
        if captcha_token:
            headers["x-captcha-token"] = captcha_token
        resp = httpx.post(
            f"{self.ACCOUNT_BASE}{path}",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # ---------- 短信登录（TgtoDrive 同款方案） ----------

    def login_sms_init(self, phone_number: str) -> Dict[str, Any]:
        """
        第一步：初始化人机验证，返回 captcha_token
        """
        return self._account_post(
            "/v1/shield/captcha/init",
            {
                "client_id": self.CLIENT_ID,
                "action": "POST:/v1/auth/verification",
                "device_id": self._device_id,
                "meta": {"phone_number": phone_number},
            },
        )

    def login_sms_send(self, phone_number: str, captcha_token: str) -> Dict[str, Any]:
        """
        第二步：发送短信验证码，返回 verification_id
        """
        return self._account_post(
            "/v1/auth/verification",
            {
                "phone_number": phone_number,
                "target": "ANY",
                "client_id": self.CLIENT_ID,
            },
            captcha_token=captcha_token,
        )

    def login_sms_verify(self, verification_id: str, code: str) -> Dict[str, Any]:
        """
        第三步：校验短信验证码，返回 verification_token
        """
        return self._account_post(
            "/v1/auth/verification/verify",
            {
                "verification_id": verification_id,
                "verification_code": code,
                "client_id": self.CLIENT_ID,
            },
        )

    def login_sms_signin(
        self,
        code: str,
        verification_token: str,
        phone_number: str,
        captcha_token: str,
    ) -> Dict[str, Any]:
        """
        第四步：完成登录，返回 access_token/refresh_token 并更新客户端状态
        """
        result = self._account_post(
            "/v1/auth/signin",
            {
                "verification_code": code,
                "verification_token": verification_token,
                "username": phone_number,
                "client_id": self.CLIENT_ID,
            },
            captcha_token=captcha_token,
        )
        access_token = result.get("access_token")
        if access_token:
            self._apply_tokens(
                access_token=access_token,
                refresh_token=result.get("refresh_token", self._refresh_token),
                expires_in=result.get("expires_in"),
            )
        return result

    def _apply_tokens(
        self,
        access_token: str,
        refresh_token: str = "",
        expires_in: Optional[int] = None,
    ):
        """
        应用新 token：更新内存状态、记录过期时间、触发持久化回调
        """
        self._access_token = (access_token or "").strip()
        if refresh_token:
            self._refresh_token = refresh_token.strip()
        self._token_expires_at = (
            time.time() + int(expires_in) - 60 if expires_in else None
        )
        callback = type(self).on_token_refresh or self.on_token_refresh
        if callable(callback):
            try:
                callback(self._access_token, self._refresh_token)
            except Exception:
                pass

    def refresh_token(self, refresh_token: str = "") -> Dict[str, Any]:
        """
        用 refresh_token 换新 access_token（401 自动触发）
        """
        token = (refresh_token or "").strip() or self._refresh_token
        if not token:
            raise ValueError("无可用的 refresh_token")
        headers = self._account_headers()
        headers["x-action"] = "401"
        resp = httpx.post(
            f"{self.ACCOUNT_BASE}/v1/auth/token",
            json={
                "client_id": self.CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": token,
            },
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        access_token = result.get("access_token")
        if access_token:
            self._apply_tokens(
                access_token=access_token,
                refresh_token=result.get("refresh_token", token),
                expires_in=result.get("expires_in"),
            )
        return result

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token_value(self) -> str:
        return self._refresh_token

    @property
    def device_id(self) -> str:
        return self._device_id

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 过期自动刷新
        if (
            self._refresh_token
            and self._token_expires_at
            and time.time() >= self._token_expires_at
        ):
            try:
                self.refresh_token()
            except Exception:
                pass
        resp = httpx.post(
            f"{self.BASE}{path}",
            json=payload,
            headers=self._headers,
            timeout=30,
        )
        # 401 自动刷新后重试一次
        if resp.status_code == 401 and self._refresh_token:
            try:
                self.refresh_token()
                resp = httpx.post(
                    f"{self.BASE}{path}",
                    json=payload,
                    headers=self._headers,
                    timeout=30,
                )
            except Exception:
                pass
        resp.raise_for_status()
        return resp.json()

    # ---------- 目录 ----------

    def create_dir(self, dir_name: str, parent_id: str = "") -> Dict[str, Any]:
        """
        创建目录，返回原始响应（code 159 表示已存在）
        """
        return self._post(
            self.PATH_CREATE_DIR,
            {
                "dirName": dir_name,
                "parentId": str(parent_id or ""),
                "failIfNameExist": False,
            },
        )

    def extract_dir_id(self, payload: Dict[str, Any]) -> str:
        """从 create_dir 响应中递归抽取目录 ID"""
        return self._find_first_by_keys(payload, ["dirId", "dir_id", "fileId", "folderId", "folder_id", "id"])

    def list_dir(
        self,
        parent_id: str = "",
        page: int = 0,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        列出目录内容（仅文件和目录原始条目）
        """
        data = self._post(
            self.PATH_LIST,
            {
                "parentId": str(parent_id or ""),
                "page": page,
                "pageSize": page_size,
                "orderBy": 0,
                "sortType": 0,
            },
        )
        body = data.get("data") or {}
        items = (
            body.get("fileList")
            or body.get("list")
            or body.get("files")
            or []
        )
        return items if isinstance(items, list) else []

    def ensure_dir(self, dir_name: str, parent_id: str = "") -> Optional[str]:
        """
        确保目录存在并返回其 ID：
        先尝试创建（已存在返回159），再从父目录列表中定位目录 ID。
        """
        resp = self.create_dir(dir_name, parent_id=parent_id)
        code = resp.get("code")
        if code not in (None, self.CODE_OK, self.CODE_DIR_EXISTS):
            raise RuntimeError(f"创建目录失败: {resp}")
        dir_id = self.extract_dir_id(resp)
        if dir_id and code == self.CODE_OK:
            return str(dir_id)
        # 已存在或未返回ID：从父目录列表中查找
        for page in range(50):
            items = self.list_dir(parent_id=parent_id, page=page, page_size=100)
            if not items:
                break
            for item in items:
                is_dir = item.get("isDir") or item.get("is_dir") or item.get("type") == 1
                name = str(item.get("fileName") or item.get("name") or "")
                item_id = item.get("fileId") or item.get("id") or item.get("dirId")
                if is_dir and name == dir_name and item_id:
                    return str(item_id)
            if len(items) < 100:
                break
        return None

    # ---------- 秒传 ----------

    def get_res_center_token(
        self,
        name: str,
        size: int,
        parent_id: str = "",
        md5: str = "",
    ) -> Dict[str, Any]:
        """
        秒传第一步：提交 md5+size 查询服务端库存。

        code 156 = 库存命中，文件已直接进入网盘；
        其他 = 未命中（可能返回 taskId 创建了上传任务，需清理）。
        """
        return self._post(
            self.PATH_RES_TOKEN,
            {
                "capacity": 1,
                "name": name,
                "parentId": str(parent_id or ""),
                "res": {"md5": md5, "fileSize": int(size)},
            },
        )

    def check_can_flash_upload_gcid(
        self,
        task_id: str,
        gcid: str,
    ) -> Dict[str, Any]:
        """
        GCID 通路秒传检测（无需本地文件）

        data.canFlashUpload == true 表示库存命中，文件已进盘。
        """
        return self._post(
            self.PATH_CHECK_FLASH,
            {
                "taskId": str(task_id),
                "gcid": str(gcid).strip().upper(),
            },
        )

    def delete_upload_task(self, task_ids: List[str]) -> None:
        """清理未命中的上传任务"""
        try:
            self._post(self.PATH_DELETE_TASK, {"taskIds": [str(t) for t in task_ids]})
        except Exception:
            pass

    # ---------- 文件 ----------

    def find_file(
        self,
        parent_id: str,
        name: str,
        size: int,
        max_pages: int = 200,
    ) -> Optional[str]:
        """
        在目录中按 文件名+大小 查找文件，返回 file_id。
        """
        target = (name or "").strip()
        for page in range(max_pages):
            items = self.list_dir(parent_id=parent_id, page=page, page_size=100)
            if not items:
                return None
            for item in items:
                if item.get("isDir") or item.get("is_dir") or item.get("type") == 1:
                    continue
                f_name = str(item.get("fileName") or item.get("name") or "").strip()
                if f_name != target:
                    continue
                f_size = int(item.get("fileSize") or item.get("size") or 0)
                if size and f_size != int(size):
                    continue
                f_id = item.get("fileId") or item.get("id")
                if f_id:
                    return str(f_id)
            if len(items) < 100:
                return None
        return None

    # ---------- 播放 ----------

    def download_url(self, file_id: str) -> str:
        """
        获取文件下载直链（签名URL）
        """
        resp = self._post(self.PATH_DOWNLOAD, {"fileId": str(file_id)})
        url = self._find_first_by_keys(resp, ["signedURL", "signedUrl", "downloadUrl", "download_url", "url"])
        return str(url or "")

    # ---------- 工具 ----------

    @classmethod
    def _find_first_by_keys(cls, node: Any, keys: List[str]) -> Any:
        """在嵌套结构中按候选键名递归查找第一个非空值"""
        if isinstance(node, dict):
            for key in keys:
                value = node.get(key)
                if value not in (None, "", 0):
                    return value
            for value in node.values():
                found = cls._find_first_by_keys(value, keys)
                if found not in (None, ""):
                    return found
        elif isinstance(node, list):
            for item in node:
                found = cls._find_first_by_keys(item, keys)
                if found not in (None, ""):
                    return found
        return None
