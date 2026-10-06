"""Bounded QQ Official delivery for static, unusually tall Sky images."""
import io

from PIL import Image, ImageOps

from .core import SkyError

MAX_INPUT_BYTES = 12 * 1024 * 1024
MAX_SOURCE_PIXELS = 64_000_000
MAX_WIDTH = 1280
MAX_PAGE_HEIGHT = 4096
MAX_PAGES = 4
MAX_PAGE_BYTES = 4 * 1024 * 1024


def official_image_pages(content: bytes) -> tuple[bytes, ...]:
    """Decode once, then resize and crop adjacent rows without omitting content.

    Ordinary images retain their original bytes and format. All pages are
    prepared before returning, so a validation/encoding error cannot send a
    partial result. No URLs, accounts, or event objects enter this helper.
    """
    if not isinstance(content, bytes) or not content or len(content) > MAX_INPUT_BYTES:
        raise SkyError('光遇图片为空或超过处理大小限制，未发送图片。')
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.width * source.height > MAX_SOURCE_PIXELS:
                raise SkyError('光遇图片像素过大，未发送图片。')
            source.load()  # A valid header alone does not prove decodable pixels.
            orientation = source.getexif().get(274, 1)
            display_height = source.width if orientation in (5, 6, 7, 8) else source.height
            if display_height <= MAX_PAGE_HEIGHT:
                return (content,)
            if getattr(source, 'is_animated', False):
                raise SkyError('光遇超长动图暂不支持分段，未发送图片。')
            oriented = ImageOps.exif_transpose(source)
            try:
                width = min(oriented.width, MAX_WIDTH)
                height = max(1, round(oriented.height * width / oriented.width))
                count = (height + MAX_PAGE_HEIGHT - 1) // MAX_PAGE_HEIGHT
                if count > MAX_PAGES:
                    raise SkyError('光遇图片过长，分段后超过4页，未发送图片；内容未被截断。')
                mode = 'RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB'
                with oriented.convert(mode) as decoded:
                    resized = decoded.resize((width, height), Image.Resampling.LANCZOS) if decoded.size != (width, height) else decoded.copy()
                try:
                    pages = []
                    for top in range(0, height, MAX_PAGE_HEIGHT):
                        with resized.crop((0, top, width, min(top + MAX_PAGE_HEIGHT, height))) as page:
                            stream = io.BytesIO()
                            page.save(stream, format='WEBP', quality=90, method=4)
                            data = stream.getvalue()
                        if len(data) > MAX_PAGE_BYTES:
                            raise SkyError('光遇分段图片超过发送大小限制，未发送图片。')
                        pages.append(data)
                    return tuple(pages)
                finally:
                    resized.close()
            finally:
                oriented.close()
    except SkyError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SkyError('光遇图片无法完整解码或转换，未发送图片。') from exc
