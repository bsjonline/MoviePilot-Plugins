__all__ = ["GuangyaClient"]

from base64 import b64encode
from hashlib import md5, sha1
from hmac import new as hmac_new
from xml.etree.ElementTree import fromstring
from email.utils import formatdate
from pathlib import Path
from time import sleep, time
from typing import Any, Callable, Dict, List, Optional, Union

from httpx import Client, post

from secrets import token_hex

from .tools import calculate_gcid, generate_did, generate_traceparent


class GuangyaClient:
    """
    光鸭云盘客户端
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        device_id: Optional[str] = None,
    ):
        self.token = access_token or ""
        self.token_expires_at: Optional[float] = None
        self.refresh_token_value: Optional[str] = refresh_token
        self.device_id = device_id or generate_did()
        self._client = Client(
            headers={
                "accept": "application/json, text/plain, */*",
                "authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                "did": self.device_id,
                "dt": "4",
                "origin": "https://www.guangyapan.com",
                "referer": "https://www.guangyapan.com/",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            }
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def request(self, url: str, method: str = "GET", **request_kwargs):
        headers = {"traceparent": generate_traceparent()}
        if "headers" in request_kwargs:
            headers.update(request_kwargs.pop("headers"))

        if (
            self.refresh_token_value
            and self.token_expires_at
            and time() >= self.token_expires_at
        ):
            self.refresh_token()

        response = self._client.request(method, url, headers=headers, **request_kwargs)
        if response.status_code == 401 and self.refresh_token_value:
            self.refresh_token()
            headers["traceparent"] = generate_traceparent()
            response = self._client.request(
                method, url, headers=headers, **request_kwargs
            )
        response.raise_for_status()
        return response

    # Auth

    def _account_headers(self) -> Dict:
        return {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "x-client-id": "aMe-8VSlkrbQXpUR",
            "x-client-version": "0.0.1",
            "x-device-id": self.device_id,
            "x-device-model": "chrome%2F147.0.0.0",
            "x-device-name": "PC-Chrome",
            "x-device-sign": f"wdi10.{self.device_id}{token_hex(16)}",
            "x-net-work-type": "NONE",
            "x-os-version": "MacIntel",
            "x-platform-version": "1",
            "x-protocol-version": "301",
            "x-provider-name": "NONE",
            "x-sdk-version": "9.0.2",
        }

    def login_sms_init(self, phone_number: str, captcha_token: Optional[str] = None):
        """
        短信登录 - 初始化验证码

        :param phone_number: 手机号，含国际区号，如 "+86 13800138000"
        :param captcha_token: 已有的验证码 token，首次调用可不传

        :return: 初始化结果，包含 captcha_token 或 url（需人机验证）
        """
        body = {
            "client_id": "aMe-8VSlkrbQXpUR",
            "action": "POST:/v1/auth/verification",
            "device_id": self.device_id,
            "meta": {"phone_number": phone_number},
        }
        if captcha_token:
            body["captcha_token"] = captcha_token
        return post(
            "https://account.guangyapan.com/v1/shield/captcha/init",
            headers=self._account_headers(),
            json=body,
        ).json()

    def login_sms_send(
        self,
        phone_number: str,
        captcha_token: str,
        target: str = "ANY",
    ):
        """
        短信登录 - 发送验证码

        :param phone_number: 手机号，含国际区号，如 "+86 13800138000"
        :param captcha_token: login_sms_init 返回的验证码 token
        :param target: 验证码发送方式，默认 "ANY"

        :return: 发送结果
        """
        headers = self._account_headers()
        headers["x-captcha-token"] = captcha_token
        return post(
            "https://account.guangyapan.com/v1/auth/verification",
            headers=headers,
            json={
                "phone_number": phone_number,
                "target": target,
                "client_id": "aMe-8VSlkrbQXpUR",
            },
        ).json()

    def login_sms_verify(self, verification_id: str, verification_code: str):
        """
        短信登录 - 验证短信验证码

        :param verification_id: login_sms_send 返回的 verification_id
        :param verification_code: 收到的短信验证码

        :return: 验证结果
        """
        return post(
            "https://account.guangyapan.com/v1/auth/verification/verify",
            headers=self._account_headers(),
            json={
                "verification_id": verification_id,
                "verification_code": verification_code,
                "client_id": "aMe-8VSlkrbQXpUR",
            },
        ).json()

    def login_sms_signin(
        self,
        verification_code: str,
        verification_token: str,
        username: str,
        captcha_token: str,
    ):
        """
        短信登录 - 提交验证码完成登录

        :param verification_code: 收到的短信验证码
        :param verification_token: login_sms_verify 返回的 verification_token
        :param username: 手机号，含国际区号，如 "+86 13800138000"
        :param captcha_token: 验证码 token

        :return: 登录结果，包含 access_token 等
        """
        headers = self._account_headers()
        headers["x-captcha-token"] = captcha_token
        result = post(
            "https://account.guangyapan.com/v1/auth/signin",
            headers=headers,
            json={
                "verification_code": verification_code,
                "verification_token": verification_token,
                "username": username,
                "client_id": "aMe-8VSlkrbQXpUR",
            },
        ).json()
        access_token = result.get("access_token")
        if access_token:
            self.token = access_token
            self._client.headers["authorization"] = f"Bearer {access_token}"
            expires_in = result.get("expires_in")
            self.token_expires_at = time() + expires_in if expires_in else None
            self.refresh_token_value = result.get("refresh_token")
        return result

    def refresh_token(self, refresh_token: Optional[str] = None):
        """
        刷新访问令牌

        :param refresh_token: refresh_token，不传则使用 self.refresh_token_value

        :return: 刷新结果，包含新的 access_token 等
        """
        token = refresh_token or self.refresh_token_value
        if not token:
            raise ValueError("无可用的 refresh_token")
        headers = self._account_headers()
        headers["x-action"] = "401"
        result = post(
            "https://account.guangyapan.com/v1/auth/token",
            headers=headers,
            json={
                "client_id": "aMe-8VSlkrbQXpUR",
                "grant_type": "refresh_token",
                "refresh_token": token,
            },
        ).json()
        access_token = result.get("access_token")
        if access_token:
            self.token = access_token
            self._client.headers["authorization"] = f"Bearer {access_token}"
            expires_in = result.get("expires_in")
            self.token_expires_at = time() + expires_in if expires_in else None
            self.refresh_token_value = result.get("refresh_token", token)
        return result

    def login_sms(
        self,
        phone_number: str,
        get_code: Callable[[], str] = lambda: input("请输入短信验证码: "),
        target: str = "ANY",
    ):
        """
        短信登录全流程

        :param phone_number: 手机号，含国际区号，如 "+86 13800138000"
        :param get_code: 获取短信验证码的回调，默认通过 input() 交互输入
        :param target: 验证码发送方式，默认 "ANY"

        :return: 登录结果，包含 access_token 等，同时更新客户端 token
        """
        init_result = self.login_sms_init(phone_number)
        captcha_token = init_result["captcha_token"]

        send_result = self.login_sms_send(phone_number, captcha_token, target)
        verification_id = send_result["verification_id"]

        code = get_code()

        verify_result = self.login_sms_verify(verification_id, code)
        verification_token = verify_result["verification_token"]

        return self.login_sms_signin(
            code, verification_token, phone_number, captcha_token
        )

    # User

    def user_info(self):
        """
        获取当前登录用户信息

        :return: 用户信息
        """
        headers = self._account_headers()
        headers["authorization"] = f"Bearer {self.token}"
        return post(
            "https://account.guangyapan.com/v1/user/me",
            headers=headers,
        ).json()

    # Cloud Download

    def cloud_task_list(
        self,
        page: int = 0,
        page_size: int = 50,
        status: Optional[List] = None,
    ):
        """
        获取云下载任务列表

        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param status: 任务状态列表，默认 [0, 1, 3, 4]

        :return: 云下载任务列表
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizcloudcollection.s/v1/list_task",
            method="POST",
            json={
                "page": page,
                "pageSize": page_size,
                "status": status if status is not None else [0, 1, 3, 4],
            },
        ).json()

    def cloud_resolve_url(self, url: str):
        """
        解析云下载链接

        :param url: 下载链接，支持 HTTP/磁力/ed2k 等

        :return: 解析结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizcloudcollection.s/v1/resolve_res",
            method="POST",
            json={"url": url},
        ).json()

    def cloud_resolve_torrent(self, torrent: Union[str, Path, bytes]):
        """
        解析 BT 种子文件

        :param torrent: 种子文件路径或二进制内容

        :return: 解析结果
        """
        if isinstance(torrent, bytes):
            data = torrent
            filename = "file.torrent"
        else:
            path_torrent = Path(torrent)
            data = path_torrent.read_bytes()
            filename = path_torrent.name
        return self.request(
            "https://api.guangyapan.com/nd.bizcloudcollection.s/v1/resolve_torrent",
            method="POST",
            files={"torrent": (filename, data, "application/octet-stream")},
        ).json()

    def cloud_create_task(self, url: str, parent_id: Optional[int] = None):
        """
        创建云下载任务

        :param url: 下载链接，支持 HTTP/磁力/ed2k 等
        :param parent_id: 保存到的目标目录 ID

        :return: 创建结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizcloudcollection.s/v1/create_task",
            method="POST",
            json={"url": url, "parentId": "" if parent_id is None else parent_id},
        ).json()

    # Download

    def download_url(self, file_id: str):
        """
        获取文件下载链接

        :param file_id: 文件 ID

        :return: 下载链接
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_res_download_url",
            method="POST",
            json={"fileId": file_id},
        ).json()

    # fs_files

    def get_task_status(self, task_id: str):
        """
        获取任务状态

        :param task_id: 任务 ID

        :return: 任务状态
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_task_status",
            method="POST",
            json={"taskId": task_id},
        ).json()

    def fs_create_dir(
        self,
        dir_name: str,
        parent_id: Optional[int] = None,
        fail_if_name_exist: bool = False,
    ):
        """
        创建文件夹

        :param dir_name: 文件夹名称
        :param parent_id: 父级目录 ID
        :param fail_if_name_exist: 同名文件夹已存在时是否报错

        :return: 创建结果
        """
        data: Dict = {
            "dirName": dir_name,
            "parentId": "" if parent_id is None else parent_id,
        }
        if fail_if_name_exist:
            data["failIfNameExist"] = True
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/create_dir",
            method="POST",
            json=data,
        ).json()

    def fs_copy(self, file_ids: List, parent_id: Optional[int] = None):
        """
        复制文件

        :param file_ids: 文件 ID 列表
        :param parent_id: 目标父级目录 ID

        :return: 任务 ID
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/copy_file",
            method="POST",
            json={
                "fileIds": file_ids,
                "parentId": "" if parent_id is None else parent_id,
            },
        ).json()

    def fs_move(self, file_ids: List, parent_id: Optional[int] = None):
        """
        移动文件

        :param file_ids: 文件 ID 列表
        :param parent_id: 目标父级目录 ID

        :return: 任务 ID
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/move_file",
            method="POST",
            json={
                "fileIds": file_ids,
                "parentId": "" if parent_id is None else parent_id,
            },
        ).json()

    def fs_delete(self, file_ids: List):
        """
        删除文件

        传入普通文件 ID 时移入回收站，传入回收站文件 ID 时从回收站永久删除

        :param file_ids: 文件 ID 列表

        :return: 删除结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/delete_file",
            method="POST",
            json={"fileIds": file_ids},
        ).json()

    def fs_recycle(self, file_ids: List):
        """
        将回收站文件还原

        :param file_ids: 回收站文件 ID 列表

        :return: 任务 ID
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/recycle_file",
            method="POST",
            json={"fileIds": file_ids},
        ).json()

    def fs_clear_recycle_bin(self):
        """
        清空回收站

        :return: 任务 ID
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/clear_recycle_bin",
            method="POST",
        ).json()

    def fs_rename(self, file_id: str, new_name: str):
        """
        重命名文件

        :param file_id: 文件 ID
        :param new_name: 新文件名

        :return: 重命名结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/rename",
            method="POST",
            json={"fileId": file_id, "newName": new_name},
        ).json()

    def fs_detail(self, file_id: str):
        """
        获取文件详情

        :param file_id: 文件 ID

        :return: 文件详情
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/get_file_detail",
            method="POST",
            json={"fileId": file_id},
        ).json()

    def fs_files(
        self,
        parent_id: Optional[Union[int, str]] = None,
        page: int = 0,
        page_size: int = 50,
        order_by: int = 0,
        sort_type: int = 0,
        file_types: Optional[List] = None,
        res_type: Optional[int] = None,
        dir_type: Optional[int] = None,
        need_play_record: bool = False,
    ):
        """
        获取文件列表

        :param parent_id: 父级目录 ID，传 "*" 表示全部目录
        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param order_by: 排序字段
        :param sort_type: 排序方式，0 升序，1 降序
        :param file_types: 文件类型过滤列表
        :param res_type: 资源类型
        :param dir_type: 目录类型，4 为回收站
        :param need_play_record: 是否需要播放记录

        :return: 文件列表
        """
        data: Dict = {
            "parentId": "" if parent_id is None else parent_id,
            "page": page,
            "pageSize": page_size,
            "orderBy": order_by,
            "sortType": sort_type,
        }
        if file_types is not None:
            data["fileTypes"] = file_types
        if res_type is not None:
            data["resType"] = res_type
        if dir_type is not None:
            data["dirType"] = dir_type
        if need_play_record:
            data["needPlayRecord"] = True
        return self.request(
            "https://api.guangyapan.com/userres/v1/file/get_file_list",
            method="POST",
            json=data,
        ).json()

    def fs_image_list(
        self,
        page: int = 0,
        page_size: int = 50,
        order_by: int = 3,
        sort_type: int = 1,
    ):
        """
        获取图片列表（全部目录）

        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param order_by: 排序字段
        :param sort_type: 排序方式，0 升序，1 降序

        :return: 图片列表
        """
        return self.fs_files(
            parent_id="*",
            page=page,
            page_size=page_size,
            order_by=order_by,
            sort_type=sort_type,
            file_types=[1],
            res_type=1,
        )

    def fs_video_list(
        self,
        page: int = 0,
        page_size: int = 50,
        order_by: int = 3,
        sort_type: int = 1,
        need_play_record: bool = False,
    ):
        """
        获取视频列表（全部目录）

        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param order_by: 排序字段
        :param sort_type: 排序方式，0 升序，1 降序
        :param need_play_record: 是否需要播放记录

        :return: 视频列表
        """
        return self.fs_files(
            parent_id="*",
            page=page,
            page_size=page_size,
            order_by=order_by,
            sort_type=sort_type,
            file_types=[2],
            res_type=1,
            need_play_record=need_play_record,
        )

    def fs_document_list(
        self,
        page: int = 0,
        page_size: int = 50,
        order_by: int = 3,
        sort_type: int = 1,
    ):
        """
        获取文档列表（全部目录）

        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param order_by: 排序字段
        :param sort_type: 排序方式，0 升序，1 降序

        :return: 文档列表
        """
        return self.fs_files(
            parent_id="*",
            page=page,
            page_size=page_size,
            order_by=order_by,
            sort_type=sort_type,
            file_types=[4],
            res_type=1,
        )

    def fs_recycle_files(
        self,
        page: int = 0,
        page_size: int = 50,
        order_by: int = 10,
        sort_type: int = 0,
    ):
        """
        获取回收站列表

        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param order_by: 排序字段
        :param sort_type: 排序方式，0 升序，1 降序

        :return: 回收站列表
        """
        return self.fs_files(
            page=page,
            page_size=page_size,
            order_by=order_by,
            sort_type=sort_type,
            dir_type=4,
        )

    # Share

    def share_create(
        self,
        file_ids: List,
        title: str,
        validate_duration: int = 0,
        share_type: int = 1,
        code: str = "",
        auto_fill_code: bool = True,
        traffic_limit: str = "0",
        max_restore_count: int = 0,
        download_type: int = 1,
    ):
        """
        创建分享

        :param file_ids: 文件 ID 列表
        :param title: 分享标题
        :param validate_duration: 有效期（天），0 表示永久
        :param share_type: 分享类型
        :param code: 提取码，为空时无密码
        :param auto_fill_code: 是否自动填充提取码
        :param traffic_limit: 流量限制，"0" 表示不限
        :param max_restore_count: 最大保存次数，0 表示不限
        :param download_type: 下载类型

        :return: 分享结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/share_file",
            method="POST",
            json={
                "fileIds": file_ids,
                "title": title,
                "validateDuration": validate_duration,
                "shareType": share_type,
                "code": code,
                "autoFillCode": auto_fill_code,
                "trafficLimit": traffic_limit,
                "maxRestoreCount": max_restore_count,
                "downloadType": download_type,
            },
        ).json()

    def share_user_list(
        self,
        page: int = 0,
        page_size: int = 50,
        order_type: int = 1,
        sort_type: int = 1,
    ):
        """
        获取自己的分享列表

        :param page: 页码，从 0 开始
        :param page_size: 每页数量
        :param order_type: 排序字段
        :param sort_type: 排序方式，0 升序，1 降序

        :return: 分享列表
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_share_list",
            method="POST",
            json={
                "page": page,
                "pageSize": page_size,
                "orderType": order_type,
                "sortType": sort_type,
            },
        ).json()

    def share_update(
        self,
        share_id: str,
        title: str,
        validate_duration: int = 0,
        share_type: int = 1,
        code: str = "",
        auto_fill_code: bool = True,
        traffic_limit: str = "0",
        max_restore_count: int = 0,
        download_type: int = 1,
    ):
        """
        更新分享

        :param share_id: 分享 ID
        :param title: 分享标题
        :param validate_duration: 有效期（秒），0 表示永久
        :param share_type: 分享类型
        :param code: 提取码，为空时无密码
        :param auto_fill_code: 是否自动填充提取码
        :param traffic_limit: 流量限制，"0" 表示不限
        :param max_restore_count: 最大保存次数，0 表示不限
        :param download_type: 下载类型

        :return: 更新结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/update_share",
            method="POST",
            json={
                "id": share_id,
                "title": title,
                "validateDuration": validate_duration,
                "shareType": share_type,
                "code": code,
                "autoFillCode": auto_fill_code,
                "trafficLimit": traffic_limit,
                "maxRestoreCount": max_restore_count,
                "downloadType": download_type,
            },
        ).json()

    def share_delete(self, ids: List):
        """
        删除分享

        :param ids: 分享 ID 列表

        :return: 删除结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/delete_share",
            method="POST",
            json={"ids": ids},
        ).json()

    def share_restore(
        self,
        access_token: str,
        file_ids: List,
        parent_id: str = "",
    ):
        """
        转存分享文件

        :param access_token: 分享访问令牌
        :param file_ids: 文件 ID 列表
        :param parent_id: 目标目录 ID

        :return: 转存结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/restore_share",
            method="POST",
            json={
                "accessToken": access_token,
                "fileIds": file_ids,
                "parentId": parent_id,
            },
        ).json()

    def share_download_url(self, file_id: str, access_token: str):
        """
        获取分享文件下载链接

        :param file_id: 文件 ID
        :param access_token: 分享访问令牌

        :return: 下载链接
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_share_download_url",
            method="POST",
            json={"fileId": file_id, "accessToken": access_token},
        ).json()

    def share_files_size(
        self,
        access_token: str,
        file_ids: List,
        download: bool = True,
    ):
        """
        获取分享文件大小

        :param access_token: 分享访问令牌
        :param file_ids: 文件 ID 列表
        :param download: 是否为下载场景

        :return: 文件大小信息
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_share_files_size",
            method="POST",
            json={
                "accessToken": access_token,
                "fileIds": file_ids,
                "download": download,
            },
        ).json()

    @staticmethod
    def _public_post(url: str, json: Dict):
        return post(
            url,
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "did": generate_did(),
                "dt": "4",
                "origin": "https://www.guangyapan.com",
                "referer": "https://www.guangyapan.com/",
                "traceparent": generate_traceparent(),
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            },
            json=json,
        ).json()

    @staticmethod
    def share_summary(share_id: str):
        """
        获取分享摘要（无需登录）

        :param share_id: 分享 ID

        :return: 分享摘要
        """
        return GuangyaClient._public_post(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_share_summary",
            {"shareId": share_id},
        )

    @staticmethod
    def share_access_token(share_id: str, code: str):
        """
        获取分享访问令牌（无需登录）

        :param share_id: 分享 ID
        :param code: 分享提取码

        :return: 访问令牌
        """
        return GuangyaClient._public_post(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_share_access_token",
            {"shareId": share_id, "code": code},
        )

    @staticmethod
    def share_files_list(
        access_token: str,
        parent_id: str = "",
        page: int = 1,
        page_size: int = 50,
        order_by: int = 0,
        sort_type: int = 0,
    ):
        """
        获取分享页文件列表（无需登录）

        :param access_token: 分享访问令牌
        :param parent_id: 父目录 ID
        :param page: 页码
        :param page_size: 每页数量
        :param order_by: 排序字段
        :param sort_type: 排序方式

        :return: 文件列表
        """
        return GuangyaClient._public_post(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_share_page_files_list",
            {
                "accessToken": access_token,
                "parentId": parent_id,
                "page": page,
                "pageSize": page_size,
                "orderBy": order_by,
                "sortType": sort_type,
            },
        )

    # Upload

    def upload_token(
        self,
        name: str,
        file_size: int,
        parent_id: Optional[int] = None,
        md5: Optional[str] = None,
    ):
        """
        获取上传 token

        如果文件 < 1MB，需要提供 md5 直接上传

        :param name: 文件名
        :param file_size: 文件大小
        :param parent_id: 父级 ID
        :param md5: 文件 MD5 值

        :return: token
        """
        url = "https://api.guangyapan.com/nd.bizuserres.s/v1/get_res_center_token"
        data: Dict[str, Any] = {
            "capacity": 2,
            "name": name,
            "res": {"fileSize": file_size},
            "parentId": "" if parent_id is None else parent_id,
        }
        if md5 is not None:
            data["res"]["md5"] = md5
        return self.request(
            url,
            method="POST",
            json=data,
        ).json()

    def check_can_flash_upload(self, task_id: str, file_path: Union[str, Path]):
        """
        检查是否可以秒传

        :param task_id: 任务 ID
        :param file_path: 文件路径

        :return: 检查结果
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/check_can_flash_upload",
            method="POST",
            json={"taskId": task_id, "gcid": calculate_gcid(file_path)},
        ).json()

    @staticmethod
    def oss_sign_headers(
        method: str,
        bucket: str,
        object_key: str,
        access_key_id: str,
        secret_access_key: str,
        security_token: str,
        content_type: str = "",
        content_md5: str = "",
        sub_resources: Optional[Dict] = None,
    ) -> Dict:
        """
        生成 OSS 签名头部

        :param method: 请求方法
        :param bucket: 桶名
        :param object_key: 对象键
        :param access_key_id: 访问密钥 ID
        :param secret_access_key: 访问密钥
        :param security_token: 安全令牌
        :param content_type: 内容类型
        :param content_md5: 内容 MD5
        :param sub_resources: 子资源

        :return: 签名头部
        """
        date = formatdate(usegmt=True)
        oss_canonical_headers = {
            "x-oss-date": date,
            "x-oss-security-token": security_token.strip(),
        }
        resource = f"/{bucket}/{object_key}"
        if sub_resources:
            resource += "?" + "&".join(
                k if (v is None or v == "") else f"{k}={v}"
                for k, v in sorted(sub_resources.items())
            )
        canonical = "\n".join(
            [
                method.upper(),
                content_md5,
                content_type,
                date,
                *[f"{k}:{v}" for k, v in sorted(oss_canonical_headers.items())],
                resource,
            ]
        )
        sig = b64encode(
            hmac_new(
                secret_access_key.encode(), canonical.encode("utf-8"), sha1
            ).digest()
        ).decode()
        headers = {
            "authorization": f"OSS {access_key_id}:{sig}",
            "x-oss-date": date,
            "x-oss-security-token": security_token,
        }
        if content_type:
            headers["content-type"] = content_type
        if content_md5:
            headers["content-md5"] = content_md5
        return headers

    def oss_request(
        self,
        method: str,
        url: str,
        bucket: str,
        object_key: str,
        access_key_id: str,
        secret_access_key: str,
        security_token: str,
        content: bytes = b"",
        content_type: str = "",
        content_md5: str = "",
        sub_resources: Optional[dict] = None,
    ):
        """
        OSS 请求

        :param method: 请求方法
        :param url: 请求 URL
        :param bucket: 桶名
        :param object_key: 对象键
        :param access_key_id: 访问密钥 ID
        :param secret_access_key: 访问密钥
        :param security_token: 安全令牌
        :param content: 请求内容
        :param content_type: 内容类型
        :param content_md5: 内容 MD5
        :param sub_resources: 子资源

        :return: 请求响应
        """
        headers = self.oss_sign_headers(
            method,
            bucket,
            object_key,
            access_key_id,
            secret_access_key,
            security_token,
            content_type=content_type,
            content_md5=content_md5,
            sub_resources=sub_resources,
        )
        params = (
            {k: (v or "") for k, v in sub_resources.items()} if sub_resources else {}
        )
        return self.request(
            url, method=method, params=params, headers=headers, content=content
        )

    def cdn_upload(
        self,
        file_path: Union[str, Path],
        token_data: Dict,
        content_type: str = "application/octet-stream",
        chunk_size: int = 5 * 1024 * 1024,
    ) -> str:
        """
        CDN 分片上传文件到 OSS

        :param file_path: 文件路径
        :param token_data: upload_token 返回的 data 字段
        :param content_type: 文件 MIME 类型
        :param chunk_size: 分片大小，默认 5MB

        :return: 上传完成后的 ETag
        """
        creds = token_data["creds"]
        access_key_id = creds["accessKeyID"]
        secret_access_key = creds["secretAccessKey"]
        security_token = creds["sessionToken"]
        full_endpoint = token_data["fullEndPoint"]
        bucket = token_data["bucketName"]
        object_key = token_data["objectPath"]
        base_url = f"{full_endpoint}/{object_key}"
        req_args = (
            base_url,
            bucket,
            object_key,
            access_key_id,
            secret_access_key,
            security_token,
        )

        resp = self.oss_request(
            "POST", *req_args, content_type=content_type, sub_resources={"uploads": ""}
        )
        upload_id = fromstring(resp.text).findtext("UploadId")

        parts = []
        with open(file_path, "rb") as f:
            part_number = 1
            while chunk := f.read(chunk_size):
                chunk_md5 = b64encode(md5(chunk).digest()).decode()
                resp = self.oss_request(
                    "PUT",
                    *req_args,
                    content=chunk,
                    content_type="application/octet-stream",
                    content_md5=chunk_md5,
                    sub_resources={
                        "partNumber": str(part_number),
                        "uploadId": upload_id,
                    },
                )
                etag = resp.headers.get("etag", "").strip('"')
                parts.append((part_number, etag))
                part_number += 1

        xml_parts = "".join(
            f'<Part><PartNumber>{n}</PartNumber><ETag>"{e}"</ETag></Part>'
            for n, e in parts
        )
        xml_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<CompleteMultipartUpload>{xml_parts}</CompleteMultipartUpload>"
        ).encode("utf-8")
        xml_md5 = b64encode(md5(xml_body).digest()).decode()
        resp = self.oss_request(
            "POST",
            *req_args,
            content=xml_body,
            content_type="application/xml",
            content_md5=xml_md5,
            sub_resources={"uploadId": upload_id},
        )
        return fromstring(resp.text).findtext("ETag") or ""

    def upload_info(self, task_id: str):
        """
        获取上传任务信息

        :param task_id: 任务 ID

        :return: 任务信息
        """
        return self.request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/get_info_by_task_id",
            method="POST",
            json={"taskId": task_id},
        ).json()

    def file_upload(
        self,
        file_path: Union[str, Path],
        name: Optional[str] = None,
        parent_id: Optional[int] = None,
        content_type: str = "application/octet-stream",
        chunk_size: int = 5 * 1024 * 1024,
    ) -> Dict:
        """
        上传文件全流程

        :param file_path: 文件路径
        :param name: 文件名，默认使用文件本身的名称
        :param parent_id: 父级目录 ID
        :param content_type: 文件 MIME 类型
        :param chunk_size: 分片大小，默认 5MB

        :return: upload_info 返回结果
        """
        file_path_obj = Path(file_path)
        file_size = file_path_obj.stat().st_size
        file_name = name or file_path_obj.name

        if file_size < 1024 * 1024:
            file_md5 = b64encode(md5(file_path_obj.read_bytes()).digest()).decode()
            token_resp = self.upload_token(
                file_name, file_size, parent_id, md5=file_md5
            )
            return self.upload_info(token_resp["data"]["taskId"])

        token_resp = self.upload_token(file_name, file_size, parent_id)
        token_data = token_resp["data"]
        task_id = token_data["taskId"]

        flash_resp = self.check_can_flash_upload(task_id, file_path_obj)
        if flash_resp["data"].get("canFlashUpload"):
            return self.upload_info(task_id)

        self.cdn_upload(
            file_path_obj, token_data, content_type=content_type, chunk_size=chunk_size
        )
        for _ in range(3):
            resp = self.upload_info(task_id)
            if resp.get("msg", "") == "文件上传中":
                sleep(2)
            else:
                return resp
        return self.upload_info(task_id)
