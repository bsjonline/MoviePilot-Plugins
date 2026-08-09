from guangyaclient import GuangyaClient
import httpx


class GuangyaAutoClient:
    """
    光鸭云盘客户端
    """

    def __init__(self, access_token: str):
        self._client = None
        self._access_token = access_token

    @property
    def client(self) -> GuangyaClient:
        if self._client is None:
            self._client = GuangyaClient(access_token=self._access_token)
        return self._client

    def scan_files(self, parent_id: str = "", page: int = 0, page_size: int = 100) -> list[dict]:
        """
        扫描云盘文件列表，返回包含 gcid/file_id/name/size 的字典列表
        """
        resp = self.client.fs_files(parent_id=parent_id, page=page, page_size=page_size)
        data = resp.get("data") or {}
        items = data.get("fileList") or data.get("list") or []
        result = []
        for item in items:
            file_id = str(item.get("fileId") or item.get("id") or "")
            gcid = str(item.get("gcid") or item.get("hash") or "")
            name = str(item.get("fileName") or item.get("name") or "")
            size = int(item.get("fileSize") or item.get("size") or 0)
            result.append({"file_id": file_id, "gcid": gcid, "name": name, "size": size})
        return result

    def rapid_get_token(self, gcid: str, name: str, size: int, parent_id: str = "") -> dict:
        """
        网页JSON导入通路：获取秒传 token（userres/v1/get_res_center_token）
        """
        url = "https://api.guangyapan.com/userres/v1/get_res_center_token"
        headers = {
            "authorization": f"Bearer {self._access_token}",
            "content-type": "application/json;charset=utf-8",
            "dt": "4",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "x-device-id": "700bdae5de558dfc3f0616070306c84f",
        }
        payload = {
            "capacity": 2,
            "res": {"gcid": gcid, "md5": "", "fileSize": size},
            "name": name,
            "parentId": str(parent_id or ""),
        }
        with httpx.Client(headers=headers, timeout=30) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def rapid_check_flash(self, task_id: str, gcid: str, size: int) -> dict:
        """
        网页JSON导入通路：检查是否可秒传（userres/v1/check_can_flash_upload）
        """
        url = "https://api.guangyapan.com/userres/v1/check_can_flash_upload"
        headers = {
            "authorization": f"Bearer {self._access_token}",
            "content-type": "application/json;charset=utf-8",
            "dt": "4",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "x-device-id": "700bdae5de558dfc3f0616070306c84f",
        }
        payload = {"taskId": task_id, "gcid": gcid, "md5": "", "fileSize": size}
        with httpx.Client(headers=headers, timeout=30) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def rapid_upload_info(self, task_id: str) -> dict:
        """
        网页JSON导入通路：查询秒传任务状态（userres/v1/file/get_info_by_task_id）
        """
        url = "https://api.guangyapan.com/userres/v1/file/get_info_by_task_id"
        headers = {
            "authorization": f"Bearer {self._access_token}",
            "content-type": "application/json;charset=utf-8",
            "dt": "4",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "x-device-id": "700bdae5de558dfc3f0616070306c84f",
        }
        payload = {"taskId": task_id}
        with httpx.Client(headers=headers, timeout=30) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def get_download_url(self, file_id: str) -> str:
        """
        获取文件下载地址
        """
        resp = self.client.download_url(file_id=file_id)
        data = resp.get("data") or resp
        return str(data.get("download_url") or data.get("url") or data.get("downloadUrl") or "")
