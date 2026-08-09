from guangyaclient import GuangyaClient


class GuangyaAutoClient:
    """
    光鸭云盘客户端
    """

    def __init__(self, access_token: str):
        self._client = None
        self._access_token = access_token

    def __getattr__(self, name):
        if self._client is None:
            self._client = GuangyaClient(access_token=self._access_token)

        def wrapped(*args, **kwargs):
            """
            代理调用 GuangyaClient 的方法

            :param args: 传递给客户端方法的位置参数
            :param kwargs: 传递给客户端方法的关键字参数
            :return: 客户端方法的返回值
            """
            return getattr(self._client, name)(*args, **kwargs)

        return wrapped
