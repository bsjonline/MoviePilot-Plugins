import time
from typing import Any, Dict, List, Optional

import httpx


class GuangyaAutoClient:
    """
    光鸭云盘客户端（纯服务端，Bearer token 认证）

    全部接口走 api.guangyapan.com/nd.bizuserres.s 域名，
    与光鸭批量助手V4油猴脚本一致。
    """

    BASE = "https://api.guangyapan.com"
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

    def __init__(self, access_token: str, device_id: str = ""):
        self._access_token = access_token.strip()
        self._device_id = device_id.strip()

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

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = httpx.post(
            f"{self.BASE}{path}",
            json=payload,
            headers=self._headers,
            timeout=30,
        )
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
