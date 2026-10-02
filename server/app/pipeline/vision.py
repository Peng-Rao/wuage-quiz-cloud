"""把题目配图（存储 key）转换为看图模型可读取的地址。"""

import base64
import mimetypes

from ..config import Settings
from ..storage import get_store

# 模型签名地址的有效期：排队、重试期间都要可用
URL_EXPIRES_S = 3600


def image_inputs(keys: list[str], settings: Settings) -> list[str]:
    """返回对象存储签名地址，或 data URL（本地存储、或 VISION_IMAGE_MODE=base64）。会访问存储，异步代码中应在线程中调用。"""
    store = get_store()
    out: list[str] = []
    for key in keys[: settings.vision_max_images]:
        url = store.signed_url(key, URL_EXPIRES_S) if settings.vision_image_mode != "base64" else None
        if url is None:
            if settings.vision_image_mode == "url":
                raise ValueError("VISION_IMAGE_MODE=url 需要使用对象存储（OSS / COS），本地文件地址模型服务访问不到")
            mime = mimetypes.guess_type(key)[0] or "image/png"
            url = f"data:{mime};base64,{base64.b64encode(store.get(key)).decode()}"
        out.append(url)
    return out
