"""音乐 / 相册 tool 集合(3 个读)。

源:mcp_server.py:697-717
"""
from mcp_server import main as _main
from mcp_server.main import mcp
from mcp_server.perf import _to_json


@mcp.tool()
async def list_songs() -> str:
    """极音乐全部歌曲(实测 4549 首,主要 FLAC/DSF 高保真格式)。"""
    return _to_json(await _main.nas.post("/zmusic/api/v2/song/list", {}))


@mcp.tool()
async def list_albums() -> str:
    """相册列表(实测 218 个,含人脸/宠物/儿童/场景/地理/节日等分类)。
    type 编码: 40=来源 60=儿童 90=主题 100=人脸 110=场景 120=节日 130=地理 150=宠物。"""
    return _to_json(await _main.nas.post("/v2/album/albums", {}))


@mcp.tool()
async def list_album_feeds(album_id: int, num: int = 20) -> str:
    """列出某相册里的照片/视频。album_id 从 list_albums 拿。"""
    return _to_json(await _main.nas.post("/v2/album/album/feeds",
                                       {"album_id": album_id, "start": 0, "num": num}))
