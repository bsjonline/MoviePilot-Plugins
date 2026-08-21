from p123client import P123Client


class P123AutoClient:
    """
    123云盘客户端
    """

    def __init__(self, passport: str, password: str):
        self._client = None
        self._passport = passport
        self._password = password
        # 123 新接口要求显式 driveId，传 0 会被拒（400 driveId is required）
        self._drive_id = 0

    def set_drive_id(self, drive_id):
        """设置 driveId（从 user_info 返回中取，取不到则保持默认 0）"""
        try:
            self._drive_id = int(drive_id) if drive_id not in (None, "", 0) else 0
        except (TypeError, ValueError):
            self._drive_id = 0

    def _get_client(self):
        if self._client is None:
            self._client = P123Client(passport=self._passport, password=self._password)
        return self._client

    # ------------------------------------------------------------------
    # 路径 1：绕开 vendor 默认 driveId:0，直接构造带 driveId 的请求，
    # 不依赖 dict_key_to_lower_merge 的合并优先级（该私有函数语义未知）。
    # ------------------------------------------------------------------
    def mkdir_with_drive(self, name: str, parent_id: int | str = 0):
        """
        创建目录并显式带上 driveId。
        对应 vendor.fs_mkdir -> POST /upload/v1/file/mkdir
        """
        client = self._get_client()
        payload = {
            "name": name,
            "parentID": parent_id,
            "driveId": self._drive_id,
        }
        return client.request(
            "/upload/v1/file/mkdir",
            "POST",
            json=payload,
            base_url="https://open-api.123pan.com",
        )

    def upload_fast_with_drive(
        self,
        file_md5: str = "",
        file_name: str = "",
        file_size: int = 0,
        parent_id: int | str = 0,
        duplicate: int = 0,
        not_reuse: bool = False,
    ):
        """
        尝试秒传文件并显式带上 driveId。
        对应 vendor.upload_request -> POST /api/file/upload_request
        """
        client = self._get_client()
        payload = {
            "fileName": file_name,
            "driveId": self._drive_id,
            "duplicate": duplicate,
            "etag": file_md5,
            "parentFileId": parent_id,
            "size": file_size,
            "type": 0,
            "NotReuse": not_reuse,
        }
        return client.request(
            "file/upload_request",
            "POST",
            json=payload,
            base_url="https://123pan.com/b",
        )

    def list_with_drive(
        self,
        parent_id: int | str = 0,
        event: str = "homeListFile",
        limit: int = 100,
        next_id: int = 0,
    ):
        """
        获取文件列表并显式带上 driveId。
        对应 vendor.fs_list -> GET /api/file/list
        """
        client = self._get_client()
        payload = {
            "driveId": self._drive_id,
            "limit": limit,
            "next": next_id,
            "orderDirection": "asc",
            "parentFileId": parent_id,
            "inDirectSpace": False,
            "event": event,
        }
        return client.request(
            "file/list",
            "GET",
            params=payload,
            base_url="https://123pan.com/b",
        )

    def resolve_drive_id(self):
        """
        从多个来源防御式解析 driveId，优先级：
          1. user_info() 返回的 data（defaultDriveId|driveId|drive_id）
          2. 登录 JWT 的 token_user_info claim（123 个人盘 driveId 常在此）
        取不到返回 0，并在调用方决定是否告警。
        返回 (drive_id:int, source:str)
        """
        client = self._get_client()
        # 1) user_info
        try:
            ui = client.user_info()
            if isinstance(ui, dict):
                data = ui.get("data") or ui
                for k in ("defaultDriveId", "driveId", "drive_id"):
                    if data.get(k):
                        return int(data[k]), "user_info"
        except Exception:
            pass
        # 2) JWT token_user_info
        try:
            tui = client.token_user_info
            if isinstance(tui, dict):
                for k in ("defaultDriveId", "driveId", "drive_id", "space"):
                    if tui.get(k):
                        return int(tui[k]), "jwt"
                # 有的账号 driveId 在 driveList 列表里
                dl = tui.get("driveList") or tui.get("drive_list")
                if isinstance(dl, list) and dl:
                    first = dl[0]
                    if isinstance(first, dict) and (first.get("driveId") or first.get("id")):
                        return int(first.get("driveId") or first.get("id")), "jwt.driveList"
        except Exception:
            pass
        return 0, "none"

    def __getattr__(self, name):
        if self._client is None:
            self._client = P123Client(passport=self._passport, password=self._password)

        def wrapped(*args, **kwargs):
            """
            代理调用 P123Client 的方法，自动处理 Token 超限重连

            :param args: 传递给客户端方法的位置参数
            :param kwargs: 传递给客户端方法的关键字参数
            :return: 客户端方法的返回值
            """
            attr = getattr(self._client, name)
            if not callable(attr):
                return attr
            result = attr(*args, **kwargs)
            if (
                isinstance(result, dict)
                and result.get("code") == 401
                and result.get("message") == "tokens number has exceeded the limit"
            ):
                self._client = P123Client(self._passport, self._password)
                attr = getattr(self._client, name)
                if not callable(attr):
                    return attr
                return attr(*args, **kwargs)
            return result

        return wrapped
